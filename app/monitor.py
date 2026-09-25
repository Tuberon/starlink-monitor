"""
Фоновий watchdog: опитує dish (POLL_INTERVAL_SEC) і router
(ROUTER_POLL_INTERVAL_SEC),
пише в БД, авто-reboot Mini при 3 умовах (watchdog failures,
update-ready dish, update-ready router) - див. docs/architecture.md.
Логує зміни стану/попереджень в events, дублює ключові події в
Telegram (не блокує цикл при помилках відправки).
"""
import json
import logging
import os
import signal
import time
from typing import Any, Callable, Optional

import psutil

from app import activity_led, config, config_editor, db, i18n, telegram_notify
from app import labels
from app.starlink_client import DishStatus, RouterInfo, StarlinkClient
from app.system_metrics import get_system_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("monitor")


def pi_just_booted(threshold_sec: float = 120.0) -> bool:
    """Чи Pi РЕАЛЬНО щойно завантажився (не просто service-restart,
    напр. `sudo systemctl restart starlink-monitor.service` під час
    оновлення коду через update.sh) - порівнює system uptime
    (`psutil.boot_time()`, уже використовується в system_metrics.py)
    з порогом. Чиста функція (легко тестується без реального psutil-
    виклику через мокування)."""
    return time.time() - psutil.boot_time() < threshold_sec


def version_in_target_list(current_version: Optional[str], target_raw: Optional[str]) -> bool:
    """Чи current_version входить у target_raw - список версій через
    кому (db.parse_version_list). Module-level (не метод класу) - щоб
    бути доступною і з Watchdog (фоновий цикл опитування), і напряму
    з webapp.py (ручна кнопка "Перевірити оновлення" - окремий процес,
    не має доступу до інстансу Watchdog з іншого сервісу)."""
    if not current_version:
        return False
    return current_version in db.parse_version_list(target_raw)


def check_target_version_reached(
    component_label: str, current_version: Optional[str], target_key: str,
    notified_key: str, dish_id: Optional[str], notify_fn: Callable[[str], None],
) -> None:
    """Порівнює встановлену версію (current_version) з очікуваними
    (target - введені користувачем на /settings через кому, той самий
    формат, що telegram_chat_ids - корисно, коли SpaceX випускає
    РІЗНІ номери версій для різних апаратних ревізій під однією
    умовною версією, і користувач не певен, який саме рядок реально
    прийде). Матч спрацьовує на БУДЬ-ЯКУ з перелічених версій.
    notified_key зберігає ТРІЙКУ (dish_id + яка саме версія збіглась +
    повний список target) - природно "скидається" і при зміні списку,
    і якщо ТА САМА версія випадково повториться на іншому опитуванні
    ТОГО САМОГО пристрою, АЛЕ КРИТИЧНО - dish_id у ключі означає, що
    ІНШИЙ фізичний Starlink (інший dish_id, напр. після заміни
    обладнання) з тим самим збігом версія+target ЗАВЖДИ отримає своє
    власне, свіже сповіщення, не заблоковане дедублікацією попереднього
    пристрою. notify_fn - інʼєкція функції сповіщення (Watchdog передає
    self._notify, webapp.py передає telegram_notify.send_message напряму
    - module-level функція сама не залежить від того, ЯК саме сповіщати)."""
    target_raw = db.get_setting(target_key)
    if not version_in_target_list(current_version, target_raw):
        return
    notified_value = f"{dish_id}|{current_version}|{target_raw}"
    if db.get_setting(notified_key) == notified_value:
        return
    notify_fn(f"✅ Останнє оновлення {component_label} встановлено: версія {current_version}")
    db.set_setting(notified_key, notified_value)


def check_both_targets_reached(last_known_dish_id: Optional[str], notify_fn: Callable[[str], None]) -> None:
    """Окремо від per-component сповіщень вище (ті корисні самі по собі
    - dish оновився, вже цікаво знати, навіть якщо router ще ні) - це
    додаткове, комбіноване підтвердження: коли ОБИДВІ (тарілка й
    роутер) очікувані версії одночасно збігаються зі встановленими,
    надсилає одне повідомлення про завершення ВСІЄЇ процедури
    оновлення. Викликається з обох poll_once() (dish) і poll_router()
    (router) - або один, або інший цикл опитування може стати тим,
    що робить умову істинною одночасно для обох (компоненти
    опитуються незалежно, різними циклами), а також з api_check_updates()
    (webapp.py) - ручна кнопка теж має отримувати повний спектр
    перевірок, не лише читання статусу.

    Дедублікація - той самий принцип, що в check_target_version_reached():
    notified-ключ включає dish_id (різні фізичні Starlink НЕ ділять
    дедублікаційний стан), САМЕ ЗНАЧЕННЯ поточних версій обох
    компонентів і повні target-списки одночасно - природно
    "скидається", щойно користувач змінить БУДЬ-ЯКИЙ з двох
    target-списків (напр. додасть версію для іншої апаратної
    ревізії), без потреби окремо очищати стан."""
    dish_target = db.get_setting("dish_target_version")
    router_target = db.get_setting("router_target_version")
    if not dish_target or not router_target:
        return

    latest = db.get_latest_metric()
    router_status = db.get_router_status()
    dish_current = latest.get("software_version") if latest else None
    router_current = router_status.get("software_version") if router_status else None

    if not version_in_target_list(dish_current, dish_target) or \
            not version_in_target_list(router_current, router_target):
        return

    combo_key = f"{last_known_dish_id}|{dish_current}|{dish_target}|{router_current}|{router_target}"
    if db.get_setting("both_targets_notified") == combo_key:
        return

    notify_fn(f"🎉 Процедуру оновлення завершено: тарілка {dish_current}, роутер {router_current}")
    db.set_setting("both_targets_notified", combo_key)


def _format_firmware_change_message(component_label: str, old_version: str, new_version: str) -> Optional[str]:
    """Формує повідомлення про зміну прошивки - лише для реального
    ОНОВЛЕННЯ (нова версія новіша). db.is_older_version() (толерантний
    компаратор, вже перевірений на реалістичних версіях) визначає
    напрямок зміни: якщо це насправді ВІДКАТ (SpaceX інколи відкочує
    проблемні білди глобально) - навмисно НЕ сповіщаємо (`known_
    devices` все одно оновлюється незалежно, лише Telegram-сповіщення
    пропускається для цього напрямку)."""
    if db.is_older_version(new_version, old_version):
        return None
    return f"🔄 Прошивка {component_label} оновлена: {old_version} → {new_version}"


def upsert_dish_and_notify(status: DishStatus, notify_fn: Callable[[str], None]) -> None:
    """Записує/оновлює відому версію dish у known_devices, сповіщає
    при РЕАЛЬНІЙ зміні версії ("🔄 оновлена" чи "⏪ відкочена" - залежно
    від напрямку, див. _format_firmware_change_message), і перевіряє
    target-версію. Module-level - той самий принцип, що решта функцій
    вище: спільна логіка для watchdog-циклу (poll_once) і ручної
    кнопки "Перевірити оновлення" (webapp.py api_check_updates) - без
    цього ручна перевірка мовчки НЕ надсилала жодного сповіщення,
    навіть коли версія якраз збігалась із target (знайдено на
    реальному запиті користувача - "перевір процедуру перевірки
    оновлень на помилки")."""
    if not status.online:
        return
    real_change, old_version = db.upsert_known_device_dish(status.dish_id, status.hardware_version, status.software_version)
    if real_change and old_version:
        msg = _format_firmware_change_message("тарілки", old_version, status.software_version)
        if msg:
            notify_fn(msg)
    check_target_version_reached(
        "тарілки", status.software_version, "dish_target_version", "dish_target_notified", status.dish_id, notify_fn
    )
    check_both_targets_reached(status.dish_id, notify_fn)


def upsert_router_and_notify(info: RouterInfo, dish_id: Optional[str], notify_fn: Callable[[str], None]) -> None:
    """Аналогічно до upsert_dish_and_notify(), для router. dish_id -
    router прив'язується до dish_id того самого фізичного Mini
    (router не має власного окремого ідентифікатора)."""
    if not info.online:
        return
    if dish_id:
        real_change, old_version = db.upsert_known_device_router(dish_id, info.hardware_version, info.software_version)
        if real_change and old_version:
            msg = _format_firmware_change_message("роутера", old_version, info.software_version)
            if msg:
                notify_fn(msg)
    check_target_version_reached(
        "роутера", info.software_version, "router_target_version", "router_target_notified", dish_id, notify_fn
    )
    check_both_targets_reached(dish_id, notify_fn)


# Попередження, які навмисно ігноруються (не пишуться в БД, журнал,
# Telegram, дашборд) - шумні для конкретної конфігурації мережі, без
# практичної цінності. Module-level (не атрибут класу Watchdog) -
# потрібна і в Watchdog.poll_router() (фоновий watchdog-цикл), і в
# check_updates_now() нижче (ручна перевірка через веб-кнопку/
# /checkupdates) - реальний баг, знайдений на запиті користувача:
# check_updates_now() записувала router-статус БЕЗ цього фільтра,
# тому "прибраний" alert повертався щоразу, коли user тиснув
# "Перевірити оновлення" вручну, аж до наступного фонового циклу
# poll_router() (~35с), який знову коректно його прибирав.
IGNORED_ROUTER_ALERTS = {"wired_mesh_not_using_wan_iface"}


def build_backup_dict() -> dict[str, Any]:
    """Формує повний backup-словник (Telegram config, auto-reboot,
    перевизначені параметри, історія відомих пристроїв) -
    спільна для webapp.py api_settings_backup() (ручний, через веб-
    кнопку) і perform_auto_backup() нижче (автоматичний, періодичний,
    з watchdog-циклу) - уникає дублювання тієї самої логіки в двох
    місцях. Bot token включається у відкритому вигляді - файл backup
    потрібно берегти як secret."""
    token, chat_ids, enabled = telegram_notify.get_telegram_config()
    env_params = {
        p["key"]: p["current"]
        for p in config_editor.read_current_values()
        if p["overridden"]
    }
    return {
        "format_version": db.BACKUP_FORMAT_VERSION,
        "created_at": time.time(),
        "telegram_bot_token": token,
        "telegram_chat_ids": chat_ids,
        "telegram_enabled": enabled,
        "auto_reboot_enabled": db.get_auto_reboot_enabled(),
        "dish_target_version": db.get_setting("dish_target_version"),
        "router_target_version": db.get_setting("router_target_version"),
        "known_devices": db.get_all_known_devices(),
        "env_params": env_params,
    }


def perform_auto_backup() -> None:
    """Записує backup у файл з ротацією (зберігає останні AUTO_BACKUP_
    KEEP_COUNT копій, видаляє старіші) - страховка від втрати known_
    devices/налаштувань при пошкодженні БД чи виходу SD-картки з ладу,
    незалежно від того, чи user робив ручний backup через веб-кнопку.
    Winятки НЕ поширюються назовні - невдалий запис backup не має
    ламати основний watchdog-цикл (аналогічно до решти periodic-задач
    у run_forever(), обгорнутих у try/except на рівні виклику)."""
    os.makedirs(config.AUTO_BACKUP_DIR, exist_ok=True)
    backup = build_backup_dict()
    ts = int(backup["created_at"])
    path = os.path.join(config.AUTO_BACKUP_DIR, f"backup-{ts}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(backup, f, ensure_ascii=False)
    # Той самий Telegram bot token у відкритому вигляді, що вже
    # захищений через chmod 600 у install.sh для /etc/starlink-
    # monitor/env - без явного chmod тут файл покладався б лише на
    # системний umask, потенційно читабельний іншими локальними
    # користувачами на тому самому Pi (0644 - типовий umask-дефолт).
    os.chmod(path, 0o600)

    existing = sorted(
        (f for f in os.listdir(config.AUTO_BACKUP_DIR) if f.startswith("backup-") and f.endswith(".json")),
    )
    for old_name in existing[:-config.AUTO_BACKUP_KEEP_COUNT] if config.AUTO_BACKUP_KEEP_COUNT > 0 else []:
        try:
            os.remove(os.path.join(config.AUTO_BACKUP_DIR, old_name))
        except OSError as e:
            logger.warning("Не вдалося видалити старий backup %s: %s", old_name, e)


def send_latest_backup_to_telegram() -> tuple[bool, str]:
    """Знаходить ОСТАННІЙ (найновіший за mtime) backup-файл із
    AUTO_BACKUP_DIR і надсилає його в Telegram як документ. Спільна
    логіка для двох викликачів: Watchdog._maybe_send_backup_to_
    telegram() (періодичний, з перевіркою інтервалу) і webapp.py
    /api/send-backup-telegram (ручна кнопка на /settings, без
    перевірки інтервалу - користувач явно натиснув, робити негайно).
    Записує подію в журнал незалежно від результату."""
    if not os.path.isdir(config.AUTO_BACKUP_DIR):
        return False, "Каталог backup ще не створений - зробіть перший backup"
    backups = [f for f in os.listdir(config.AUTO_BACKUP_DIR) if f.endswith(".json")]
    if not backups:
        return False, "Немає жодного backup-файлу для відправки"
    latest = max(backups, key=lambda f: os.path.getmtime(os.path.join(config.AUTO_BACKUP_DIR, f)))
    path = os.path.join(config.AUTO_BACKUP_DIR, latest)

    ok, msg = telegram_notify.send_document(path, caption=f"📦 Backup Starlink Monitor: {latest}")
    db.insert_event("telegram_backup_sent", f"Backup у Telegram ({latest}): {msg}", success=ok)
    return ok, f"{latest}: {msg}"


def check_db_integrity_and_notify(notify_fn: Callable[[str], None]) -> None:
    """PRAGMA quick_check раз на добу (той самий цикл, що VACUUM) -
    виявляє мовчазну деградацію БД ДО того, як вона стане критичною.
    При виявленому пошкодженні - Telegram-сповіщення + СПРОБА
    аварійного backup (в try/except: якщо БД РЕАЛЬНО пошкоджена,
    сам backup теж може провалитись, читаючи з тієї самої БД - але
    спроба краща за її відсутність, і винятку тут вистачить
    logger.error, не поширення далі й не крах watchdog-циклу)."""
    ok, message = db.check_integrity()
    if ok:
        return
    logger.error("PRAGMA quick_check виявив пошкодження БД: %s", message)
    notify_fn(f"🔴 Виявлено пошкодження БД: {message}. Спроба аварійного backup...")
    try:
        perform_auto_backup()
        notify_fn("✅ Аварійний backup виконано, перевір /var/lib/starlink-monitor/backups/")
    except Exception as e:
        logger.error("Аварійний backup теж провалився: %s", e)
        notify_fn(f"🔴 Аварійний backup ТЕЖ провалився: {e}")


def check_updates_now(client: StarlinkClient, notify_fn: Callable[[str], None]) -> tuple[DishStatus, RouterInfo]:
    """Ручна перевірка стану оновлень - негайно опитує dish і router
    (замість очікування наступного фонового циклу), записує в БД,
    викликає ту саму логіку сповіщень (target-версії, "🔄 прошивка
    оновлена"/"⏪ відкочена"), що фоновий watchdog-цикл. Спільна для
    /api/check-updates (webapp.py) і /checkupdates (telegram_bot.py)
    - уникає дублювання ІДЕНТИЧНОЇ логіки в обох місцях (той самий
    клас прогалини, що вже кілька разів знаходився в цьому проєкті:
    дублювання накопичується непомітно при паралельних правках).

    ВАЖЛИВО: локальний gRPC API dish/router не має команди "примусово
    перевірити оновлення в хмарі SpaceX" (підтверджено прямими
    викликами - software_update повертає помилку, призначений для
    sideload завантаження прошивки вручну, не перевірки в хмарі).
    Натомість повертає актуальний поточний стан - це те, що реально
    доступно через локальний API."""
    dish_status = client.get_status()
    db.insert_metric(dish_status.to_dict())

    router_info = client.get_router_info()
    router_info.active_alerts = [a for a in router_info.active_alerts if a not in IGNORED_ROUTER_ALERTS]
    db.set_router_status(router_info.to_dict())

    # dish_id для router - якщо dish зараз online, беремо ЙОГО
    # (найсвіжіше джерело правди); інакше падаємо на останній відомий
    # з known_devices (dish міг бути offline саме в момент цієї
    # ручної перевірки, поки router усе ще відповідає - той самий
    # фізичний Mini).
    dish_id_for_router: Optional[str] = dish_status.dish_id
    if not dish_id_for_router:
        known = db.get_all_known_devices()
        dish_id_for_router = known[0]["dish_id"] if known else None

    upsert_dish_and_notify(dish_status, notify_fn)
    upsert_router_and_notify(router_info, dish_id_for_router, notify_fn)

    db.insert_event(
        "manual_update_check",
        f"Ручна перевірка: dish={dish_status.update_state or 'н/д'}, "
        f"router={router_info.update_state or 'н/д'}",
        success=dish_status.online or router_info.online,
    )
    return dish_status, router_info


def format_duration(seconds: float, tr: Callable[..., str]) -> str:
    """"8 год 20 хв" / "45 хв" - мовою перекладача tr (i18n.translator())."""
    minutes = max(0, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} {tr('dur_hours')} {minutes} {tr('dur_minutes')}"
    return f"{minutes} {tr('dur_minutes')}"


class Watchdog:
    # Скільки опитувань поспіль роутер має мовчати, щоб стан "Starlink
    # вимкнено" зафіксувався (~30 с при інтервалі 10 с). Короткі розриви
    # WiFi (на Pi користувача - щохвилини, ~0.5 с) інколи збігаються з
    # опитуванням; без гістерезису кожен такий збіг давав пару подій
    # starlink_offline/online у журналі (до ~100 на годину, симуляція).
    STARLINK_OFFLINE_CONFIRM_POLLS = 3

    def __init__(self) -> None:
        self.client = StarlinkClient()
        self.consecutive_failures = 0
        self.last_reboot_ts = 0.0
        # SD-card-wear reduction: dish-зчитування накопичуються тут
        # замість негайного окремого запису (кожні 10с) - flush
        # (insert_metrics_batch) одним batch-INSERT відбувається раз
        # на DISH_METRICS_BATCH_INTERVAL_SEC у run_forever(), і
        # додатково при отриманні SIGTERM/SIGINT (graceful shutdown) -
        # щоб звичайний systemctl restart/update.sh НЕ втрачав дані,
        # лише справжнє раптове вимкнення живлення.
        self.metrics_buffer: list[dict[str, Any]] = []
        self.last_batch_flush_ts = time.time()
        # На відміну від last_reboot_ts (0.0 - "дозволити reboot
        # одразу", свідомо для auto-reboot-при-невдачах), тут
        # ІНІЦІАЛІЗУЄМО поточним часом - інакше "now - 0.0" завжди
        # величезне число, спричиняючи НЕГАЙНЕ надсилання backup-
        # файлу в Telegram при КОЖНОМУ старті сервісу (небажано -
        # таймер має "стартувати" з моменту запуску, не миттєво
        # спрацьовувати).
        self.last_telegram_backup_sent_ts = time.time()
        # Час першої невдалої спроби в поточному безперервному ланцюжку
        # відмов - None, поки dish online. Використовується, щоб приглушити
        # Telegram-сповіщення про auto-reboot при тривалій (>15 хв, за
        # замовчуванням) відсутності WiFi Starlink - події й далі пишуться
        # в журнал дашборду, лише Telegram-звіти призупиняються.
        self.first_failure_ts: Optional[float] = None
        # Попередні значення для детекції змін стану оновлення/попереджень.
        # None означає "ще не бачили жодного online-статусу" - перший
        # реальний статус теж логуємо, якщо він не порожній/не IDLE-без-алертів.
        self.prev_update_state: Optional[str] = None
        self.prev_alerts: Optional[set[str]] = None
        self.prev_router_update_state: Optional[str] = None
        self.prev_router_alerts: Optional[set[str]] = None
        # Останній відомий dish_id - потрібен, щоб прив'язати опитування
        # роутера (окремий цикл, без власного dish_id у RouterInfo) до
        # того самого фізичного Mini в таблиці known_devices.
        self.last_known_dish_id: Optional[str] = None
        # Групування спаму reboot-сповіщень: якщо стається кілька
        # ребутів поспіль за короткий час (флап), кожен окремий
        # "🔁 перезавантажено" - спам. reboot_notify_ts - timestamps
        # уже надісланих reboot-сповіщень (не всіх спроб reboot, лише
        # тих, що дійшли до Telegram) за ковзне вікно.
        self.reboot_notify_ts: list[float] = []
        self.reboot_spam_muted = False
        self.muted_reboot_count = 0
        # Starlink вимкнено (ніч, відключення світла): dish недоступний І
        # роутер теж не відповідає. None - Starlink у мережі. Поки стан
        # активний, watchdog не шле марних reboot (команда йде тим самим
        # недоступним шляхом) і не пише рядок на кожне опитування.
        self.starlink_offline_since: Optional[float] = None
        self._router_down_polls = 0
        # last_reboot_ts, для якого вже записано "Пропускаю авто-reboot" -
        # один рядок на вікно очікування замість рядка щоопитування.
        self._reboot_skip_logged_for: Optional[float] = None

    def _notify(self, text: str) -> None:
        """Безпечна відправка Telegram-сповіщення - ніколи не кидає виняток
        назовні і не блокує основний цикл моніторингу."""
        try:
            ok, msg = telegram_notify.send_message(text)
            if not ok and msg not in ("Telegram сповіщення вимкнені", "Не вказано bot token", "Не вказано жодного chat_id"):
                logger.warning("Telegram сповіщення не надіслано: %s", msg)
        except Exception as e:
            logger.warning("Помилка відправки Telegram-сповіщення: %s", e)

    def _notifications_muted(self) -> bool:
        """True, якщо dish недоступний безперервно довше
        config.NOTIFICATIONS_MUTE_AFTER_SEC - Telegram-сповіщення про
        auto-reboot тимчасово призупиняються (журнал подій не зачіпається)."""
        if self.first_failure_ts is None:
            return False
        return (time.time() - self.first_failure_ts) >= config.NOTIFICATIONS_MUTE_AFTER_SEC

    def _notify_reboot(self, text: str) -> None:
        """Групування спаму reboot-сповіщень - на відміну від
        _notifications_muted() (приглушує при ОДНІЙ тривалій відмові),
        це про ЧАСТОТУ: кілька окремих коротких reboot-циклів поспіль
        (флап), кожен з яких проходить MIN_REBOOT_INTERVAL_SEC і тому
        не приглушується тим механізмом. Коли за REBOOT_SPAM_WINDOW_SEC
        назбирались REBOOT_SPAM_THRESHOLD+ такі сповіщення - мовчки
        групуємо (без окремого повідомлення про початок групування,
        щоб не додавати ще одне сповіщення до вже частих) до затишшя,
        коли надсилаємо підсумок (див. _check_reboot_spam_recovery)."""
        now = time.time()
        self.reboot_notify_ts = [t for t in self.reboot_notify_ts if now - t < config.REBOOT_SPAM_WINDOW_SEC]
        self.reboot_notify_ts.append(now)

        if len(self.reboot_notify_ts) < config.REBOOT_SPAM_THRESHOLD:
            self._notify(text)
            return

        if not self.reboot_spam_muted:
            self.reboot_spam_muted = True
            self.muted_reboot_count = 1
        else:
            self.muted_reboot_count += 1

    def _check_reboot_spam_recovery(self) -> None:
        """Викликається щоцикл опитування - якщо reboot-флап (див.
        _notify_reboot) припинився (минуло REBOOT_SPAM_WINDOW_SEC без
        нового reboot-сповіщення), надсилає підсумок і скидає стан
        групування, повертаючись до звичайного індивідуального режиму."""
        if not self.reboot_spam_muted:
            return
        if not self.reboot_notify_ts:
            return
        if time.time() - self.reboot_notify_ts[-1] < config.REBOOT_SPAM_WINDOW_SEC:
            return
        total = self.muted_reboot_count
        self._notify(f"✅ Часті авто-reboot припинились (усього {total} згруповано)")
        self.reboot_spam_muted = False
        self.muted_reboot_count = 0
        self.reboot_notify_ts = []

    def _enter_starlink_offline(self) -> None:
        """Dish недоступний, роутер теж - Starlink вимкнено або його WiFi
        немає. Одна подія на вхід у стан; збої dish не накопичуються в
        consecutive_failures (це не зависання тарілки), тож після
        увімкнення watchdog стартує з нуля."""
        if self.starlink_offline_since is None:
            self.starlink_offline_since = self.first_failure_ts or time.time()
            logger.info(
                "Starlink недоступний (роутер %s теж не відповідає) - авто-reboot призупинено до відновлення",
                self.client.router_addr,
            )
            db.insert_event("starlink_offline", "Starlink недоступний (роутер не відповідає) — авто-reboot призупинено", success=True)
        self.consecutive_failures = 0

    def _exit_starlink_offline(self) -> None:
        """Роутер знову відповідає. Подія в журнал завжди; Telegram - лише
        якщо Starlink був вимкнений довше NOTIFICATIONS_MUTE_AFTER_SEC
        (нічне вимкнення - так; хвилинне перезавантаження при оновленні
        прошивки - ні, воно й так має власні сповіщення)."""
        since = self.starlink_offline_since
        self.starlink_offline_since = None
        self.first_failure_ts = None
        if since is None:
            return
        duration = time.time() - since
        uk = i18n.translator("uk")
        logger.info("Starlink знову доступний (був недоступний %s)", format_duration(duration, uk))
        db.insert_event("starlink_online", f"Starlink знову доступний (був недоступний {format_duration(duration, uk)})", success=True)
        if duration >= config.NOTIFICATIONS_MUTE_AFTER_SEC:
            tr = i18n.translator()
            self._notify(tr("tg_starlink_back", duration=format_duration(duration, tr)))

    def flush_metrics_buffer(self) -> None:
        """Записує накопичені dish-зчитування одним batch-INSERT і
        очищає буфер. Викликається періодично з run_forever() (кожні
        DISH_METRICS_BATCH_INTERVAL_SEC) і додатково при отриманні
        SIGTERM/SIGINT (graceful shutdown), щоб звичайний systemctl
        restart/update.sh НЕ втрачав буферизовані дані."""
        if not self.metrics_buffer:
            return
        db.insert_metrics_batch(self.metrics_buffer)
        self.metrics_buffer = []
        self.last_batch_flush_ts = time.time()

    def poll_once(self) -> DishStatus:
        self._check_reboot_spam_recovery()
        status = self.client.get_status()
        self.metrics_buffer.append(status.to_dict())

        if status.online:
            self._router_down_polls = 0
            if self.starlink_offline_since is not None:
                self._exit_starlink_offline()
            if self.consecutive_failures > 0:
                downtime_sec = time.time() - self.first_failure_ts if self.first_failure_ts else 0
                logger.info("Dish знову online після %d невдалих спроб", self.consecutive_failures)
                if config.NOTIFY_DISH_RECOVERY:
                    if downtime_sec >= config.NOTIFICATIONS_MUTE_AFTER_SEC:
                        downtime_min = round(downtime_sec / 60)
                        self._notify(f"✅ Dish знову online (WiFi Starlink була відсутня ~{downtime_min} хв, сповіщення відновлено)")
                    else:
                        self._notify(f"✅ Dish знову online (після {self.consecutive_failures} невдалих спроб)")
            self.consecutive_failures = 0
            self.first_failure_ts = None
            self._notify_first_dish_connection(status)
            if status.dish_id:
                self.last_known_dish_id = status.dish_id
            upsert_dish_and_notify(status, self._notify)
            self._log_update_state_change(status)
            self._log_alerts_change(status)
            self._maybe_reboot_for_update(status)
        elif not self.client.router_reachable():
            if self.first_failure_ts is None:
                self.first_failure_ts = time.time()
            self._router_down_polls += 1
            if self.starlink_offline_since is not None or self._router_down_polls >= self.STARLINK_OFFLINE_CONFIRM_POLLS:
                self._enter_starlink_offline()
            # інакше - ще не підтверджено (можливо, короткий розрив WiFi):
            # тихо чекаємо, лічильник збоїв dish не росте
        else:
            self._router_down_polls = 0
            if self.starlink_offline_since is not None:
                # роутер повернувся раніше за dish (dish ще завантажується) -
                # далі звичайний watchdog з нульового лічильника
                self._exit_starlink_offline()
            if self.first_failure_ts is None:
                self.first_failure_ts = time.time()
            self.consecutive_failures += 1
            logger.warning(
                "Dish недоступний (%d/%d): %s",
                self.consecutive_failures,
                config.MAX_CONSECUTIVE_FAILURES,
                status.error,
            )
            self._maybe_reboot()

        if status.online and status.obstruction_fraction > config.OBSTRUCTION_WARN_FRACTION:
            db.insert_event(
                "obstruction_warning",
                f"Фракція обструкції {status.obstruction_fraction:.2%} перевищує поріг "
                f"{config.OBSTRUCTION_WARN_FRACTION:.2%}",
                success=True,
            )

        return status

    # Стани безпосереднього завантаження/встановлення оновлення (без
    # REBOOT_REQUIRED - той має власне окреме повідомлення "готове").
    # Перехід у ці стани з "не активного" (IDLE) - початок оновлення.
    DOWNLOADING_UPDATE_STATES = {"FETCHING", "PRE_CHECK", "WRITING", "POST_CHECK"}
    # Той самий набір + REBOOT_REQUIRED - для визначення "був у процесі
    # оновлення", коли перевіряємо повернення в IDLE (кінець циклу
    # після успішного перезавантаження з новою версією).
    ACTIVE_UPDATE_STATES = DOWNLOADING_UPDATE_STATES | {"REBOOT_REQUIRED"}

    def _log_update_state_change(self, status: DishStatus) -> None:
        """Пише подію в журнал кожного разу, коли змінюється стан оновлення ПЗ dish."""
        state = status.update_state or "SOFTWARE_UPDATE_STATE_UNKNOWN"
        if state == self.prev_update_state:
            return

        label = labels.update_state_label(state)
        detail = ""
        if state in self.DOWNLOADING_UPDATE_STATES and status.update_progress_pct:
            detail = f" ({status.update_progress_pct:.0f}%)"

        # Перший запис після старту сервісу (prev_update_state is None) логуємо
        # лише якщо стан не "нейтральний" (IDLE) - інакше журнал засмічується
        # одноразовим повідомленням "IDLE" при кожному рестарті сервісу.
        if self.prev_update_state is not None or state != "IDLE":
            db.insert_event(
                "update_state_change",
                f"Стан оновлення ПЗ: {label}{detail}",
                success=(state not in ("FAULTED",)),
            )
            was_active = self.prev_update_state in self.ACTIVE_UPDATE_STATES

            if state == "REBOOT_REQUIRED":
                self._notify(f"🔄 Оновлення ПЗ dish готове — очікує перезавантаження{detail}")
            elif state == "FAULTED":
                self._notify(f"⚠️ Помилка оновлення ПЗ dish: {label}")
            elif self.prev_update_state is not None and not was_active and state in self.DOWNLOADING_UPDATE_STATES:
                self._notify(f"🔽 Розпочато оновлення ПЗ dish: {label}{detail}")
            elif was_active and state == "IDLE":
                self._notify("✅ Оновлення ПЗ dish завершено (нова версія встановлена)")
        self.prev_update_state = state

    def _log_alerts_change(self, status: DishStatus) -> None:
        """Пише окрему подію для кожного попередження, яке з'явилось або зникло."""
        current = set(status.active_alerts or [])
        previous = self.prev_alerts

        # Перший виклик (previous is None): не генеруємо подій "з'явилось",
        # бо це вже поточний стан на момент старту сервісу, а не нова зміна.
        if previous is not None:
            appeared = current - previous
            resolved = previous - current
            for alert in sorted(appeared):
                label = labels.alert_label(alert)
                db.insert_event(
                    "dish_alert",
                    f"Нове попередження dish: {label}",
                    success=False,
                )
                if alert not in self.MUTED_DISH_ALERTS:
                    self._notify(f"⚠️ Нове попередження dish: {label}")
            for alert in sorted(resolved):
                # obstruction_map_reset - Starlink періодично скидає
                # карту перешкод сам по собі як частину нормальної
                # роботи (не аварійна подія) - навмисно не журналюється
                # взагалі, на відміну від решти resolved-алертів.
                if alert == "obstruction_map_reset":
                    continue
                label = labels.alert_label(alert)
                db.insert_event(
                    "dish_alert_resolved",
                    f"Попередження знято: {label}",
                    success=True,
                )

        self.prev_alerts = current

    def _notify_first_dish_connection(self, status: DishStatus) -> None:
        """Надсилає в Telegram ID тарілки один раз - лише при першому
        підключенні кожної конкретної тарілки (за dish_id) до Pi. Усі
        колись бачені ID зберігаються в settings (JSON-список), тож
        переживають рестарт сервісу; при підключенні НОВОЇ тарілки
        (ID, якого ще не було в списку) сповіщення прийде знову, навіть
        якщо до цього вже підключались дві чи більше різних тарілок."""
        if not status.dish_id:
            return

        raw = db.get_setting("known_dish_ids", "[]") or "[]"
        try:
            known_ids = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            known_ids = []

        if status.dish_id in known_ids:
            return

        known_ids.append(status.dish_id)
        db.set_setting("known_dish_ids", json.dumps(known_ids, ensure_ascii=False))
        db.insert_event("dish_connected", f"Підключено Starlink Mini, ID: {status.dish_id}", success=True)
        self._notify(f"📡 Підключено Starlink Mini (тарілка), ID: {status.dish_id}")

    def poll_system_metrics(self) -> None:
        try:
            metrics = get_system_metrics()
            db.insert_system_metric(metrics)
        except Exception as e:
            logger.warning("Не вдалося зібрати системні метрики: %s", e)

    # Попередження/стани, які й далі пишуться в журнал подій (для
    # дашборду), але НЕ надсилаються в Telegram - шумні конкретно для
    # цього звіту, без потреби негайного сповіщення.
    MUTED_DISH_ALERTS = {"roaming"}
    MUTED_ROUTER_ALERTS = {"install_pending"}
    MUTED_ROUTER_UPDATE_STATES = {"GETTING_TARGET_VERSION_FAILED"}

    def poll_router(self) -> None:
        """Опитує окремий роутерний компонент Starlink Mini (інша адреса,
        ніж dish). Версія прошивки роутера змінюється рідко, тому зберігаємо
        лише останній відомий стан (singleton-таблиця, не історія по часу)."""
        try:
            info = self.client.get_router_info()
            info.active_alerts = [a for a in info.active_alerts if a not in IGNORED_ROUTER_ALERTS]
            db.set_router_status(info.to_dict())
            if not info.online:
                logger.debug("Роутер недоступний: %s", info.error)
                return
            upsert_router_and_notify(info, self.last_known_dish_id, self._notify)
            self._log_router_update_state_change(info)
            self._log_router_alerts_change(info)
            self._maybe_reboot_for_router_update(info)
        except Exception as e:
            logger.warning("Не вдалося опитати роутер: %s", e)

    def _log_router_update_state_change(self, info: RouterInfo) -> None:
        """Пише подію в журнал кожного разу, коли змінюється стан оновлення ПЗ роутера."""
        state = info.update_state or "NOT_RUN"
        if state == self.prev_router_update_state:
            return

        label = labels.router_update_state_label(state)
        detail = ""
        if state in ("DOWNLOADING_UPDATE_IMAGE", "FLASHING") and info.update_progress_pct:
            detail = f" ({info.update_progress_pct:.0f}%)"

        if self.prev_router_update_state is not None or state != "NOT_RUN":
            is_failure = "FAILED" in state or "ILLEGAL" in state
            db.insert_event(
                "router_update_state_change",
                f"Стан оновлення ПЗ роутера: {label}{detail}",
                success=(not is_failure),
            )
            if state == "REBOOT_PENDING":
                self._notify(f"🔄 Оновлення ПЗ роутера готове — очікує перезавантаження{detail}")
            elif is_failure and state not in self.MUTED_ROUTER_UPDATE_STATES:
                self._notify(f"⚠️ Помилка оновлення ПЗ роутера: {label}")
        self.prev_router_update_state = state

    def _log_router_alerts_change(self, info: RouterInfo) -> None:
        """Пише окрему подію для кожного попередження роутера, яке з'явилось або зникло.
        info.active_alerts тут уже профільтровано від IGNORED_ROUTER_ALERTS (poll_router)."""
        current = set(info.active_alerts or [])
        previous = self.prev_router_alerts

        if previous is not None:
            appeared = current - previous
            resolved = previous - current
            for alert in sorted(appeared):
                label = labels.router_alert_label(alert)
                db.insert_event(
                    "router_alert",
                    f"Нове попередження роутера: {label}",
                    success=False,
                )
                if alert not in self.MUTED_ROUTER_ALERTS:
                    self._notify(f"⚠️ Нове попередження роутера: {label}")
            for alert in sorted(resolved):
                label = labels.router_alert_label(alert)
                db.insert_event(
                    "router_alert_resolved",
                    f"Попередження роутера знято: {label}",
                    success=True,
                )

        self.prev_router_alerts = current

    def _reboot_for_update_ready(self, component_label: str, reason: str) -> None:
        """Спільна логіка для _maybe_reboot_for_update/_maybe_reboot_for_router_update:
        обидва мають ідентичну послідовність дій (лише текст сповіщень
        відрізняється), винесено сюди, щоб не дублювати - зокрема захист
        MIN_REBOOT_INTERVAL_SEC/last_reboot_ts, який критично мати
        однаковим в обох місцях (див. reboot-loop баг у _maybe_reboot)."""
        now = time.time()
        if now - self.last_reboot_ts < config.MIN_REBOOT_INTERVAL_SEC:
            logger.info(
                "Оновлення ПЗ %s готове до встановлення, але пропускаю авто-reboot: "
                "останній reboot був %.0f с тому (мін. інтервал %d с)",
                component_label,
                now - self.last_reboot_ts,
                config.MIN_REBOOT_INTERVAL_SEC,
            )
            return

        logger.warning("Оновлення ПЗ %s готове до встановлення (%s) — ініціюю reboot Starlink Mini", component_label, reason)
        db.insert_event(
            "watchdog_trigger",
            f"Оновлення ПЗ {component_label} готове до встановлення ({reason}) — ініціюю reboot",
            success=True,
        )
        ok, msg = self.client.reboot_dish()
        db.insert_event("dish_reboot", msg, success=ok)
        # last_reboot_ts оновлюється завжди, навіть при невдачі - той самий
        # захист від reboot-loop, що й у _maybe_reboot() (якщо dish саме в
        # цю мить недоступний, наступний цикл не повинен повторювати
        # спробу негайно, а почекати MIN_REBOOT_INTERVAL_SEC).
        self.last_reboot_ts = now
        if ok:
            self._notify_reboot(f"🔁 Starlink Mini автоматично перезавантажено (оновлення ПЗ {component_label} готове: {reason})")
        else:
            self._notify(f"❌ Не вдалося перезавантажити Starlink Mini (оновлення ПЗ {component_label} готове): {msg}")

    def _maybe_reboot_for_router_update(self, info: RouterInfo) -> None:
        """Автоматичний reboot усього Starlink Mini, коли роутерний компонент
        повідомляє про готове до встановлення оновлення (REBOOT_PENDING або
        install_pending). Reboot виконується через dish_addr - dish і router
        фізично один пристрій, тож це перезавантажує обидва компоненти."""
        if not db.get_auto_reboot_enabled():
            return
        update_ready = info.update_state == "REBOOT_PENDING" or info.update_install_pending
        if not update_ready:
            return
        reason = info.update_state if info.update_state == "REBOOT_PENDING" else "install_pending"
        self._reboot_for_update_ready("роутера", reason)

    def _maybe_reboot_for_update(self, status: DishStatus) -> None:
        if not db.get_auto_reboot_enabled():
            return
        update_ready = status.update_state == "REBOOT_REQUIRED" or status.update_install_pending
        if not update_ready:
            return
        reason = status.update_state if status.update_state == "REBOOT_REQUIRED" else "install_pending"
        self._reboot_for_update_ready("dish", reason)

    def _maybe_send_backup_to_telegram(self) -> None:
        """Періодично надсилає ОСТАННІЙ (найновіший за mtime) backup-
        файл із AUTO_BACKUP_DIR у Telegram як документ - страховка,
        якщо єдина копія backup лишається на тій самій SD-картці, що
        й сама БД (обидві могли б вийти з ладу одночасно). Окремий,
        незалежний інтервал (TELEGRAM_BACKUP_INTERVAL_HOURS) від
        AUTO_BACKUP_INTERVAL_SEC (створення файлу) - можна створювати
        backup частіше, надсилати рідше, щоб не спамити Telegram
        великими файлами."""
        if not config.TELEGRAM_BACKUP_ENABLED:
            return
        now = time.time()
        if now - self.last_telegram_backup_sent_ts < config.TELEGRAM_BACKUP_INTERVAL_HOURS * 3600:
            return

        # Оновлюємо ТАЙМЕР безумовно (до самої спроби) - провал
        # відправки (напр. Telegram тимчасово недоступний) не має
        # спричиняти повторні спроби щоцикл опитування (~10с), а
        # чекати до наступного повного інтервалу.
        self.last_telegram_backup_sent_ts = now

        ok, msg = send_latest_backup_to_telegram()
        if not ok:
            logger.warning("Не вдалося надіслати backup у Telegram: %s", msg)

    def _maybe_reboot(self) -> None:
        if self.consecutive_failures < config.MAX_CONSECUTIVE_FAILURES:
            return

        now = time.time()
        if now - self.last_reboot_ts < config.MIN_REBOOT_INTERVAL_SEC:
            # Один рядок на вікно очікування - раніше писався на КОЖНОМУ
            # опитуванні (~30 рядків за вікно, сотні за ніч на SD-картку).
            if self._reboot_skip_logged_for != self.last_reboot_ts:
                self._reboot_skip_logged_for = self.last_reboot_ts
                logger.info(
                    "Пропускаю авто-reboot: останній reboot був %.0f с тому (мін. інтервал %d с)",
                    now - self.last_reboot_ts,
                    config.MIN_REBOOT_INTERVAL_SEC,
                )
            return

        failures = self.consecutive_failures
        should_log = failures <= config.MAX_LOGGED_CONSECUTIVE_FAILURES
        is_final_marker = failures == config.MAX_LOGGED_CONSECUTIVE_FAILURES + 1

        logger.warning("Ініціюю автоматичний reboot dish після %d невдалих спроб", failures)
        if should_log:
            db.insert_event(
                "watchdog_trigger",
                f"{failures} послідовних невдалих опитувань — ініціюю reboot",
                success=True,
            )
        elif is_final_marker:
            db.insert_event(
                "watchdog_trigger",
                f"Понад {config.MAX_LOGGED_CONSECUTIVE_FAILURES} послідовних невдалих опитувань — "
                "подальші спроби reboot не записуються в журнал до відновлення зв'язку",
                success=False,
            )

        ok, msg = self.client.reboot_dish()
        if should_log or is_final_marker:
            db.insert_event("dish_reboot", msg, success=ok)
        # last_reboot_ts оновлюється завжди, навіть при невдачі: якщо dish
        # ще перезавантажується з попередньої спроби, команда reboot теж
        # провалиться (grpcurl: connection refused) - без цього watchdog
        # намагався б "перезавантажити" вже перезавантажуваний dish щоцикл,
        # ігноруючи MIN_REBOOT_INTERVAL_SEC (справжній reboot-loop).
        self.last_reboot_ts = now
        if ok:
            self.consecutive_failures = 0
            if not self._notifications_muted():
                self._notify_reboot(f"🔁 Starlink Mini автоматично перезавантажено (dish не відповідав {failures} спроб поспіль)")

    def run_forever(self) -> None:
        db.init_db()
        logger.info("Starlink watchdog запущено. Опитування кожні %d с.", config.POLL_INTERVAL_SEC)

        # LED активності SD-картки (опційно, вимкнено за замовчуванням) -
        # лише watchdog-процес (не webapp.py) ініціалізує GPIO-пін,
        # щоб уникнути конфлікту двох процесів за один і той самий
        # ексклюзивний GPIO-запит.
        led = activity_led.ActivityLed(config.ACTIVITY_LED_PIN, config.ACTIVITY_LED_BLINK_MS)
        if led.init():
            db.set_activity_callback(led.blink)

        # Створюється тут (ДО реєстрації signal handler нижче, не
        # пізніше поруч із .start()) - _handle_shutdown_signal()
        # посилається на telegram_bot, тому SIGTERM/SIGINT, що прийшов
        # би МІЖ реєстрацією й пізнішим створенням, спричинив би
        # NameError. __init__() безпечний для раннього виклику - лише
        # створює StarlinkClient()/ThreadPoolExecutor/порожні
        # структури даних, без мережевих запитів.
        from app.telegram_bot import TelegramBot
        telegram_bot = TelegramBot()

        # Graceful shutdown: flush буфера dish-метрик ПЕРЕД завершенням
        # процесу - інакше звичайний "sudo systemctl restart" (напр.
        # під час update.sh) втрачав би до DISH_METRICS_BATCH_INTERVAL_
        # SEC буферизованих даних щоразу, не лише при справжньому
        # раптовому вимкненні живлення (для якого ця втрата - свідомо
        # прийнятий компроміс, не помилка).
        def _handle_shutdown_signal(signum: int, frame: Any) -> None:
            logger.info("Отримано сигнал завершення (%d) - flush буфера метрик перед виходом", signum)
            self.flush_metrics_buffer()
            led.close()
            telegram_bot.stop()
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, _handle_shutdown_signal)
        signal.signal(signal.SIGINT, _handle_shutdown_signal)

        if config.NOTIFY_PI_STARTUP and pi_just_booted():
            self._notify("🟢 Dish Watch запущено (Raspberry Pi перезавантажено)")

        telegram_bot.start()

        # "Прогрів" psutil.cpu_percent: перший виклик без базового заміру
        # завжди повертає 0.0, тому робимо його тут і відкидаємо результат.
        psutil.cpu_percent(interval=None)
        last_prune = 0.0
        last_vacuum = 0.0
        last_integrity_check = 0.0
        last_auto_backup = 0.0  # 0 гарантує перший backup одразу після старту сервісу
        last_router_poll = 0.0  # 0 гарантує негайне перше опитування роутера
        last_system_metrics_poll = 0.0  # 0 гарантує негайний перший запис
        while True:
            try:
                self.poll_once()
            except Exception as e:
                logger.exception("Неочікувана помилка в циклі опитування: %s", e)

            # SD-card-wear reduction: batch-flush накопичених dish-
            # зчитувань замість запису кожного окремо кожні 10с. Той
            # самий таймер-паттерн, що system_metrics/router нижче.
            if time.time() - self.last_batch_flush_ts > config.DISH_METRICS_BATCH_INTERVAL_SEC:
                self.flush_metrics_buffer()

            # CPU/температура/пам'ять змінюються повільно - окремий,
            # довший інтервал (STARLINK_SYSTEM_METRICS_INTERVAL_SEC),
            # не той самий, що критичні dish-метрики кожні 10с - без
            # цього SD-картка отримувала б зайві записи без практичної
            # користі (той самий принцип, що вже застосований до
            # router нижче).
            if time.time() - last_system_metrics_poll > config.SYSTEM_METRICS_INTERVAL_SEC:
                self.poll_system_metrics()
                last_system_metrics_poll = time.time()

            # Роутерний компонент опитуємо рідше, ніж dish (окремий,
            # довший інтервал, STARLINK_ROUTER_POLL_INTERVAL_SEC) - його
            # версія прошивки змінюється нечасто, і зайве навантаження
            # на WiFi-канал непотрібне при опитуванні dish кожні 10с.
            if time.time() - last_router_poll > config.ROUTER_POLL_INTERVAL_SEC:
                self.poll_router()
                last_router_poll = time.time()

            if time.time() - last_prune > 3600:
                try:
                    db.prune_old()
                except Exception:
                    logger.exception("Помилка очищення старих записів")
                last_prune = time.time()

            if time.time() - last_vacuum > 86400:
                try:
                    logger.info("Періодична оптимізація БД (VACUUM + ANALYZE)")
                    db.vacuum_and_analyze()
                except Exception:
                    logger.exception("Помилка VACUUM/ANALYZE")
                last_vacuum = time.time()

            # Окремий інтервал від VACUUM (типово той самий 86400с, але
            # реально конфігурований через DB_INTEGRITY_CHECK_INTERVAL_SEC,
            # не жорстко прив'язаний до VACUUM-таймера) - виявляє мовчазну
            # деградацію БД до того, як вона стане критичною.
            if time.time() - last_integrity_check > config.DB_INTEGRITY_CHECK_INTERVAL_SEC:
                try:
                    check_db_integrity_and_notify(self._notify)
                except Exception:
                    logger.exception("Помилка перевірки цілісності БД")
                last_integrity_check = time.time()

            # Автоматичний періодичний backup - страховка від втрати
            # known_devices/налаштувань, незалежно від того, чи user
            # робив ручний backup через веб-кнопку (міг не робити
            # місяцями).
            if config.AUTO_BACKUP_ENABLED and time.time() - last_auto_backup > config.AUTO_BACKUP_INTERVAL_SEC:
                try:
                    perform_auto_backup()
                    logger.info("Автоматичний backup виконано")
                except Exception:
                    logger.exception("Помилка автоматичного backup")
                last_auto_backup = time.time()

            # Окремий, незалежний таймер від створення backup вище -
            # власна логіка (не try/except тут) вже обробляє помилки
            # й оновлює власний таймер безумовно всередині методу.
            try:
                self._maybe_send_backup_to_telegram()
            except Exception:
                logger.exception("Помилка відправки backup у Telegram")

            time.sleep(config.POLL_INTERVAL_SEC)


def main() -> None:
    Watchdog().run_forever()


if __name__ == "__main__":
    main()
