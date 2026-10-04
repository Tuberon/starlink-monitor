"""
Сервісна логіка, спільна для кількох процесів: відстеження версій прошивок
(`upsert_*_and_notify`, `check_*_targets_reached`, `check_updates_now`) і
бекапи (`build_backup_dict`, `perform_auto_backup`,
`send_latest_backup_to_telegram`, `check_db_integrity_and_notify`).

Раніше жила в monitor.py (точка входу сервісу starlink-monitor): webapp і
telegram_bot імпортували його цілком заради трьох функцій, що створювало
цикл monitor <-> telegram_bot (тримався на лінивому імпорті) і змушувало
процес веб-інтерфейсу вантажити весь Watchdog. Перенесено ЛИШЕ ці функції,
тіла не змінено (перевірено порівнянням AST): змінились тільки назви, що
стали публічними при виході за межі модуля - component_text, COMPONENT_NAME,
COMPONENT_UPDATE, format_firmware_change_message. Сам Watchdog лишився в
monitor.py: його розбиття на класи не рекомендовано (docs/decisions-log.md).
"""
import json
import logging
import os
import time
from typing import Any, Callable, Optional

from app import config, config_editor, db, i18n, telegram_notify
from app.starlink_client import DishStatus, RouterInfo, StarlinkClient

logger = logging.getLogger("services")


def version_in_target_list(current_version: Optional[str], target_raw: Optional[str]) -> bool:
    """Чи current_version входить у target_raw - список версій через
    кому (db.parse_version_list). Module-level (не метод класу) - щоб
    бути доступною і з Watchdog (фоновий цикл опитування), і напряму
    з webapp.py (ручна кнопка "Перевірити оновлення" - окремий процес,
    не має доступу до інстансу Watchdog з іншого сервісу)."""
    if not current_version:
        return False
    return current_version in db.parse_version_list(target_raw)


# Компоненти Starlink Mini передаються як ІДЕНТИФІКАТОРИ ("dish"/"router"),
# людські назви - лише тут, через i18n. Раніше в коді ходили українські
# граматичні форми ("тарілки"/"роутера", а подекуди "dish" для того самого
# компонента), і опечатка тихо ставала сирим текстом у сповіщенні.
# Дві форми, бо так склались тексти (і вони лишились побайтово тими самими):
#   COMPONENT_NAME   - "Останнє оновлення тарілки...", "Прошивка роутера..."
#   COMPONENT_UPDATE - "оновлення ПЗ dish готове", "оновлення ПЗ роутера готове"
COMPONENT_NAME = {"dish": "comp_dish", "router": "comp_router"}
COMPONENT_UPDATE = {"dish": "comp_dish_short", "router": "comp_router"}


def component_text(component: str, forms: dict[str, str], tr: Callable[..., str] = i18n.t) -> str:
    if component not in forms:
        raise ValueError(f"невідомий компонент Starlink: {component!r} (очікується 'dish' або 'router')")
    return tr(forms[component])


def check_target_version_reached(
    component: str, current_version: Optional[str], target_key: str,
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
    # Атомарне "захоплення" прапорця ДО сповіщення (раніше get -> notify -> set:
    # одночасні перевірки - монітор і веб-кнопка - обидві сповіщали б).
    if not db.claim_setting(notified_key, notified_value):
        return
    notify_fn(i18n.t("tg_target_reached", component=component_text(component, COMPONENT_NAME), version=current_version))


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
    if not db.claim_setting("both_targets_notified", combo_key):
        return

    notify_fn(i18n.t("tg_both_targets", dish=dish_current, router=router_current))


def format_firmware_change_message(component: str, old_version: str, new_version: str) -> Optional[str]:
    """Формує повідомлення про зміну прошивки - лише для реального
    ОНОВЛЕННЯ (нова версія новіша). db.is_older_version() (толерантний
    компаратор, вже перевірений на реалістичних версіях) визначає
    напрямок зміни: якщо це насправді ВІДКАТ (SpaceX інколи відкочує
    проблемні білди глобально) - навмисно НЕ сповіщаємо (`known_
    devices` все одно оновлюється незалежно, лише Telegram-сповіщення
    пропускається для цього напрямку)."""
    if db.is_older_version(new_version, old_version):
        return None
    return i18n.t("tg_firmware_changed", component=component_text(component, COMPONENT_NAME), old=old_version, new=new_version)


def upsert_dish_and_notify(status: DishStatus, notify_fn: Callable[[str], None]) -> None:
    """Записує/оновлює відому версію dish у known_devices, сповіщає
    при РЕАЛЬНІЙ зміні версії ("🔄 оновлена" чи "⏪ відкочена" - залежно
    від напрямку, див. format_firmware_change_message), і перевіряє
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
        msg = format_firmware_change_message("dish", old_version, status.software_version)
        if msg:
            notify_fn(msg)
    check_target_version_reached(
        "dish", status.software_version, "dish_target_version", "dish_target_notified", status.dish_id, notify_fn
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
            msg = format_firmware_change_message("router", old_version, info.software_version)
            if msg:
                notify_fn(msg)
    check_target_version_reached(
        "router", info.software_version, "router_target_version", "router_target_notified", dish_id, notify_fn
    )
    check_both_targets_reached(dish_id, notify_fn)


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
        return False, i18n.t("api_backup_no_dir")
    backups = [f for f in os.listdir(config.AUTO_BACKUP_DIR) if f.endswith(".json")]
    if not backups:
        return False, i18n.t("api_backup_no_files")
    latest = max(backups, key=lambda f: os.path.getmtime(os.path.join(config.AUTO_BACKUP_DIR, f)))
    path = os.path.join(config.AUTO_BACKUP_DIR, latest)

    ok, msg = telegram_notify.send_document(path, caption=i18n.t("tg_backup_caption", name=latest))
    db.insert_event("telegram_backup_sent", f"Backup у Telegram ({latest}): {msg}", success=ok)
    return ok, f"{latest}: {msg}"


def describe_integrity_problem(message: str) -> str:
    """Текст проблеми мовою інтерфейсу з повідомлення db.check_integrity():
    db повертає КОД ("not_a_database: <деталь>"), а не переклад - шар даних
    не залежить від i18n."""
    code, separator, detail = message.partition(": ")
    if separator and code == db.NOT_A_DATABASE:
        return i18n.t("db_not_sqlite", error=detail)
    return message


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
    message = describe_integrity_problem(message)
    logger.error("PRAGMA quick_check виявив пошкодження БД: %s", message)
    notify_fn(i18n.t("tg_db_corrupt", message=message))
    try:
        perform_auto_backup()
        notify_fn(i18n.t("tg_emergency_backup_ok"))
    except Exception as e:
        logger.error("Аварійний backup теж провалився: %s", e)
        notify_fn(i18n.t("tg_emergency_backup_failed", error=e))


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
