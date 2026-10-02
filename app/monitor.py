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
import queue
import signal
import threading
import time
from typing import Any, Callable, Optional, Union

import psutil

from app import activity_led, config, db, i18n, labels, log_redact, services, telegram_notify
from app.starlink_client import DishStatus, RouterInfo, StarlinkClient
from app.system_metrics import get_system_metrics
from app.telegram_bot import TelegramBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log_redact.install()   # токен бота ніколи не потрапляє в журнал (див. app/log_redact.py)
logger = logging.getLogger("monitor")


def pi_just_booted(threshold_sec: float = 120.0) -> bool:
    """Чи Pi РЕАЛЬНО щойно завантажився (не просто service-restart,
    напр. `sudo systemctl restart starlink-monitor.service` під час
    оновлення коду через update.sh) - порівнює system uptime
    (`psutil.boot_time()`, уже використовується в system_metrics.py)
    з порогом. Чиста функція (легко тестується без реального psutil-
    виклику через мокування)."""
    return bool(time.time() - psutil.boot_time() < threshold_sec)


# Ігноровані попередження dish і роутера відкидаються в джерелі -
# starlink_client.IGNORED_DISH_ALERTS / IGNORED_ROUTER_ALERTS.

# Стани оновлення ПЗ роутера, які не пишуться в журнал подій, не
# надсилаються в Telegram і показуються як "немає оновлень" (дашборд і
# TFT-дисплей ховають їх так само). DOWNLOADING_UPDATE_IMAGE_FAILED -
# тимчасова хмарна помилка завантаження на боці SpaceX, роутер сам
# повторює спробу; на станції оновлення прошивок - шум (рішення
# користувача). Попередній стан при цьому НЕ змінюється, тож
# повернення до завантаження після такої помилки теж не логується.
HIDDEN_ROUTER_UPDATE_STATES = frozenset({"DOWNLOADING_UPDATE_IMAGE_FAILED"})


def format_duration(seconds: float, tr: Callable[..., str]) -> str:
    """"8 год 20 хв" / "45 хв" - мовою перекладача tr (i18n.translator())."""
    minutes = max(0, round(seconds / 60))
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} {tr('dur_hours')} {minutes} {tr('dur_minutes')}"
    return f"{minutes} {tr('dur_minutes')}"


_POLL_PAUSE_DEFAULT_SEC = 10.0
# Стеля буфера dish-метрик у пам'яті, коли БД недоступна: ~доба опитувань по
# 10 с. Понад це відкидаються найстаріші (пам'ять на Pi Zero - 415 МБ).
_METRICS_BUFFER_MAX = 8640


def _poll_pause() -> float:
    """Пауза між опитуваннями. Значення з /settings уже перевірено (1..3600),
    але env-файл можна змінити й вручну: time.sleep() від'ємного/nan/inf
    кидає виняток ПОЗА обробником циклу і валив увесь процес монітора
    (crash-loop під systemd), а нуль гнав би цикл без пауз."""
    interval = config.POLL_INTERVAL_SEC
    return interval if 1 <= interval <= 3600 else _POLL_PAUSE_DEFAULT_SEC


# Скільки при зупинці сервісу чекати відправки вже поставлених сповіщень, с
_NOTIFY_DRAIN_TIMEOUT_SEC = 10


class _BackgroundSender:
    """Фонова відправка в Telegram: один потік, обмежена черга, порядок
    повідомлень зберігається. Раніше сповіщення йшли синхронно в циклі
    монітора: без інтернету одне проходило всі таймаути й повтори (~62 с
    на чат, ~2 хв на 2 чати) - watchdog стояв, /healthz перевищував поріг
    свіжості (60 с) і healthcheck примусово перезапускав монітор посеред
    відправки. Circuit breaker цього не розв'язав би: перше блокування
    однаково відбулось би до розмикання."""

    def __init__(self, maxsize: int = 50) -> None:
        self._queue: "queue.Queue[Optional[Callable[[], None]]]" = queue.Queue(maxsize=maxsize)
        self._thread = threading.Thread(target=self._run, daemon=True, name="tg-notify")
        self._thread.start()

    def submit(self, job: Callable[[], None], label: str) -> None:
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            logger.warning("Черга Telegram-сповіщень переповнена - відкинуто: %s", label[:80])

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                return
            try:
                job()
            except Exception:
                logger.exception("Помилка фонової відправки в Telegram")

    def stop(self, timeout: float) -> None:
        """Дочекатись уже поставлених повідомлень (не довше timeout) і
        зупинити потік; без мережі решта черги губиться разом із
        процесом (потік - daemon)."""
        try:
            self._queue.put(None, timeout=1)
        except queue.Full:
            pass
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()


class _Periodic:
    """Періодична задача циклу монітора: "якщо минув інтервал - виконати, НЕ
    впавши, і запам'ятати момент".

    Раніше цей шаблон був розгорнутий вручну шість разів у
    Watchdog.run_forever() (локальні last_*, по try/except у кожного, складність
    22); єдина відмінність - що виконати, інтервал, текст помилки. Різнилась і
    обережність: poll_system_metrics()/poll_router() стояли БЕЗ try, тож
    виняток звідти валив би весь цикл (watchdog, який помирає, - гірше за
    watchdog, що логує й працює далі); тепер усі задачі захищені однаково.

    - `action` шукається в момент виклику (у викликача це lambda), бо тести й
      код підміняють атрибути;
    - `interval` - число або функція (конфіг читається щоразу); `None` - щоразу
      на кожній ітерації (задача має власний таймер всередині);
    - `enabled` - функція-умова (напр. AUTO_BACKUP_ENABLED); перевіряється ДО
      годинника;
    - `last` стартує з 0.0: перше спрацювання - одразу після старту сервісу.
    """

    def __init__(
        self,
        action: Callable[[], None],
        *,
        error: str,
        interval: Union[float, Callable[[], float], None],
        enabled: Optional[Callable[[], bool]] = None,
    ) -> None:
        self._action = action
        self._error = error
        self._interval = interval
        self._enabled = enabled
        self.last = 0.0

    def run_if_due(self) -> bool:
        """Виконує задачу, якщо пора. Повертає, чи виконувалась."""
        if self._enabled is not None and not self._enabled():
            return False
        if self._interval is not None:
            interval = self._interval() if callable(self._interval) else self._interval
            if not time.time() - self.last > interval:     # саме так: NaN -> не виконується
                return False
        try:
            self._action()
        except Exception:
            logger.exception(self._error)
        if self._interval is not None:
            self.last = time.time()      # і після збою: повтор - через інтервал, не щоітерації
        return True


def _optimize_db() -> None:
    vacuumed = db.vacuum_and_analyze()
    logger.info("Періодична оптимізація БД: ANALYZE, VACUUM %s",
                "виконано" if vacuumed else "пропущено (вільних сторінок мало)")


def _auto_backup() -> None:
    services.perform_auto_backup()
    logger.info("Автоматичний backup виконано")


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
        # (online, update_state) попереднього опитування - зміна скидає
        # буфер у БД одразу (див. poll_once)
        self._last_state_key: Optional[tuple[bool, str]] = None
        self._local_fault_reported = False
        # Фоновий відправник запускається лише в run_forever(); без нього
        # (прямі виклики, тести) _notify() надсилає синхронно, як раніше.
        self._sender: Optional[_BackgroundSender] = None
        # last_reboot_ts, для якого вже записано "Пропускаю авто-reboot" -
        # один рядок на вікно очікування замість рядка щоопитування.
        self._reboot_skip_logged_for: Optional[float] = None

    def _notify(self, text: str) -> None:
        """Telegram-сповіщення: у робочому циклі - у фонову чергу (цикл
        монітора не чекає на мережу), інакше - одразу."""
        if self._sender is not None:
            self._sender.submit(lambda: self._send_now(text), text)
        else:
            self._send_now(text)

    def _send_now(self, text: str) -> None:
        """Безпечна відправка Telegram-сповіщення - ніколи не кидає виняток."""
        try:
            ok, msg = telegram_notify.send_message(text)
            # Telegram не налаштований/вимкнений - очікувана ситуація, не
            # попередження. Рішення - за кодом стану, не за текстом (текст
            # тепер перекладається мовою інтерфейсу).
            if not ok and telegram_notify.config_problem() is None:
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
        self._notify(i18n.t("tg_reboot_spam_over", total=total))
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
        try:
            db.insert_metrics_batch(self.metrics_buffer)
        except Exception as e:
            # НІКОЛИ не кидає: викликається з трьох місць - періодично в
            # циклі (виняток звідти валив увесь процес монітора: при
            # заблокованій БД/повній SD зависла тарілка не перезавантажувалась
            # ніколи), при зміні стану й з обробника SIGTERM (виняток там
            # заважав би вийти, а в poll_once() його ковтав би except -
            # сервіс ігнорував би SIGTERM аж до SIGKILL). Буфер лишається;
            # повтор - через DISH_METRICS_BATCH_INTERVAL_SEC, не щоітерації.
            self.last_batch_flush_ts = time.time()
            dropped = max(0, len(self.metrics_buffer) - _METRICS_BUFFER_MAX)
            if dropped:
                del self.metrics_buffer[:dropped]   # найстаріші: пам'ять не росте безмежно
            logger.warning("Не вдалося записати %d метрик у БД (повтор через %d с%s): %s",
                           len(self.metrics_buffer), config.DISH_METRICS_BATCH_INTERVAL_SEC,
                           f", відкинуто {dropped} найстаріших" if dropped else "", e)
            return
        self.metrics_buffer = []
        self.last_batch_flush_ts = time.time()

    def _report_local_fault(self, status: DishStatus) -> None:
        """Один раз за процес (усунення потребує перезапуску сервісу - модуль
        завантажується при старті): подія в журналі, лог, Telegram."""
        if self._local_fault_reported:
            return
        self._local_fault_reported = True
        logger.error("Локальна несправність моніторингу тарілки: %s. Перезавантаження тарілки вимкнено.", status.error)
        self._event("local_fault", f"Моніторинг тарілки не працює (локальна причина): {status.error}. "
                                    "Перезавантаження тарілки вимкнено", success=False)
        self._notify(i18n.t("tg_local_fault", error=status.error))

    def poll_once(self) -> DishStatus:
        self._check_reboot_spam_recovery()
        status = self.client.get_status()
        self.metrics_buffer.append(status.to_dict())
        # Метрики пишуться пакетом раз на DISH_METRICS_BATCH_INTERVAL_SEC
        # (менше записів на SD), але дашборд і TFT-дисплей читають БД - тож
        # новий стан (online/offline, оновлення прошивки; на станції - заміна
        # Starlink) з'являвся там із затримкою до 30 с. Зміна стану - рідкісна
        # подія: її скидаємо одразу, однакові опитування й далі йдуть пакетом.
        # Збій запису тут лише логується: недоступна БД (заблокована, повна
        # SD-картка) не має обривати опитування - інакше watchdog нижче
        # ніколи не дійшов би до лічильника збоїв і перезавантаження
        # зависшої тарілки. Буфер лишається для планового запису.
        state_key = (status.online, status.update_state)
        changed = self._last_state_key is not None and state_key != self._last_state_key
        self._last_state_key = state_key
        if changed:
            self.flush_metrics_buffer()   # не кидає: збій лише логується, буфер лишається

        if status.local_fault:
            # Локальна несправність (модуль starlink_grpc не завантажився) - не збій
            # тарілки: не рахуємо, не перезавантажуємо, повідомляємо один раз.
            self._report_local_fault(status)
            return status

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
                        self._notify(i18n.t("tg_dish_back_after_mute", minutes=downtime_min))
                    else:
                        self._notify(i18n.t("tg_dish_back", failures=self.consecutive_failures))
            self.consecutive_failures = 0
            self.first_failure_ts = None
            self._notify_first_dish_connection(status)
            if status.dish_id:
                self.last_known_dish_id = status.dish_id
            services.upsert_dish_and_notify(status, self._notify)
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
                self._notify(i18n.t("tg_dish_update_ready", detail=detail))
            elif state == "FAULTED":
                self._notify(i18n.t("tg_dish_update_error", label=label))
            elif self.prev_update_state is not None and not was_active and state in self.DOWNLOADING_UPDATE_STATES:
                self._notify(i18n.t("tg_dish_update_started", label=label, detail=detail))
            elif was_active and state == "IDLE":
                self._notify(i18n.t("tg_dish_update_done"))
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
                    self._notify(i18n.t("tg_dish_alert_new", label=label))
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
        self._notify(i18n.t("tg_dish_connected", dish_id=status.dish_id))

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
            db.set_router_status(info.to_dict())
            if not info.online:
                logger.debug("Роутер недоступний: %s", info.error)
                return
            services.upsert_router_and_notify(info, self.last_known_dish_id, self._notify)
            self._log_router_update_state_change(info)
            self._log_router_alerts_change(info)
            self._maybe_reboot_for_router_update(info)
        except Exception as e:
            logger.warning("Не вдалося опитати роутер: %s", e)

    def _log_router_update_state_change(self, info: RouterInfo) -> None:
        """Пише подію в журнал кожного разу, коли змінюється стан оновлення ПЗ роутера."""
        if info.update_state in HIDDEN_ROUTER_UPDATE_STATES:
            return
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
                self._notify(i18n.t("tg_router_update_ready", detail=detail))
            elif is_failure and state not in self.MUTED_ROUTER_UPDATE_STATES:
                self._notify(i18n.t("tg_router_update_error", label=label))
        self.prev_router_update_state = state

    def _log_router_alerts_change(self, info: RouterInfo) -> None:
        """Пише окрему подію для кожного попередження роутера, яке з'явилось або зникло.
        Ігноровані попередження відкинуто вже в джерелі (starlink_client)."""
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
                    self._notify(i18n.t("tg_router_alert_new", label=label))
            for alert in sorted(resolved):
                label = labels.router_alert_label(alert)
                db.insert_event(
                    "router_alert_resolved",
                    f"Попередження роутера знято: {label}",
                    success=True,
                )

        self.prev_router_alerts = current

    def _reboot_for_update_ready(self, component: str, reason: str) -> None:
        """Спільна логіка для _maybe_reboot_for_update/_maybe_reboot_for_router_update:
        обидва мають ідентичну послідовність дій (лише текст сповіщень
        відрізняється), винесено сюди, щоб не дублювати - зокрема захист
        MIN_REBOOT_INTERVAL_SEC/last_reboot_ts, який критично мати
        однаковим в обох місцях (див. reboot-loop баг у _maybe_reboot)."""
        name_uk = services.component_text(component, services.COMPONENT_UPDATE, i18n.translator("uk"))
        now = time.time()
        if now - self.last_reboot_ts < config.MIN_REBOOT_INTERVAL_SEC:
            logger.info(
                "Оновлення ПЗ %s готове до встановлення, але пропускаю авто-reboot: "
                "останній reboot був %.0f с тому (мін. інтервал %d с)",
                name_uk,
                now - self.last_reboot_ts,
                config.MIN_REBOOT_INTERVAL_SEC,
            )
            return

        logger.warning("Оновлення ПЗ %s готове до встановлення (%s) — ініціюю reboot Starlink Mini", name_uk, reason)
        self._event(
            "watchdog_trigger",
            f"Оновлення ПЗ {name_uk} готове до встановлення ({reason}) — ініціюю reboot",
            success=True,
        )
        ok, msg = self.client.reboot_dish()
        self._event("dish_reboot", msg, success=ok)
        # last_reboot_ts оновлюється завжди, навіть при невдачі - той самий
        # захист від reboot-loop, що й у _maybe_reboot() (якщо dish саме в
        # цю мить недоступний, наступний цикл не повинен повторювати
        # спробу негайно, а почекати MIN_REBOOT_INTERVAL_SEC).
        self.last_reboot_ts = now
        if ok:
            self._notify_reboot(i18n.t("tg_auto_reboot_update", component=services.component_text(component, services.COMPONENT_UPDATE), reason=reason))
        else:
            self._notify(i18n.t("tg_auto_reboot_update_failed", component=services.component_text(component, services.COMPONENT_UPDATE), msg=msg))

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
        self._reboot_for_update_ready("router", reason)

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

        def job() -> None:
            ok, msg = services.send_latest_backup_to_telegram()
            if not ok:
                logger.warning("Не вдалося надіслати backup у Telegram: %s", msg)

        if self._sender is not None:
            self._sender.submit(job, "backup")
        else:
            job()

    def _event(self, kind: str, message: str, success: bool = True) -> None:
        """Запис у журнал подій на шляхах перезавантаження, що ніколи не
        кидає виняток. Журнал не має блокувати рятувальну дію: раніше при
        недоступній БД подія "watchdog_trigger" падала ДО reboot_dish()
        (зависла тарілка не перезавантажувалась ніколи), а "dish_reboot" -
        ДО оновлення last_reboot_ts (reboot на кожному опитуванні)."""
        try:
            db.insert_event(kind, message, success=success)
        except Exception as e:
            logger.warning("Не вдалося записати подію %s у журнал: %s", kind, e)

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
            self._event(
                "watchdog_trigger",
                f"{failures} послідовних невдалих опитувань — ініціюю reboot",
                success=True,
            )
        elif is_final_marker:
            self._event(
                "watchdog_trigger",
                f"Понад {config.MAX_LOGGED_CONSECUTIVE_FAILURES} послідовних невдалих опитувань — "
                "подальші спроби reboot не записуються в журнал до відновлення зв'язку",
                success=False,
            )

        ok, msg = self.client.reboot_dish()
        if should_log or is_final_marker:
            self._event("dish_reboot", msg, success=ok)
        # last_reboot_ts оновлюється завжди, навіть при невдачі: якщо dish
        # ще перезавантажується з попередньої спроби, команда reboot теж
        # провалиться (grpcurl: connection refused) - без цього watchdog
        # намагався б "перезавантажити" вже перезавантажуваний dish щоцикл,
        # ігноруючи MIN_REBOOT_INTERVAL_SEC (справжній reboot-loop).
        self.last_reboot_ts = now
        if ok:
            self.consecutive_failures = 0
            if not self._notifications_muted():
                self._notify_reboot(i18n.t("tg_auto_reboot_watchdog", failures=failures))

    def _periodic_tasks(self) -> list[_Periodic]:
        """Періодичні задачі циклу - у тому ж порядку, що раніше в run_forever()."""
        return [
            # CPU/температура/пам'ять змінюються повільно - окремий, довший
            # інтервал (STARLINK_SYSTEM_METRICS_INTERVAL_SEC), не той самий, що
            # критичні dish-метрики кожні 10с: без цього SD-картка отримувала б
            # зайві записи без практичної користі.
            _Periodic(lambda: self.poll_system_metrics(), error="Помилка опитування метрик Pi",
                      interval=lambda: config.SYSTEM_METRICS_INTERVAL_SEC),
            # Роутер опитуємо рідше, ніж dish (STARLINK_ROUTER_POLL_INTERVAL_SEC):
            # його версія прошивки змінюється нечасто, а зайве навантаження на
            # WiFi-канал непотрібне при опитуванні dish кожні 10с.
            _Periodic(lambda: self.poll_router(), error="Помилка опитування роутера",
                      interval=lambda: config.ROUTER_POLL_INTERVAL_SEC),
            _Periodic(lambda: db.prune_old(), error="Помилка очищення старих записів", interval=3600),
            _Periodic(_optimize_db, error="Помилка VACUUM/ANALYZE", interval=86400),
            # Окремий інтервал від VACUUM (типово той самий 86400с, але реально
            # конфігурований через DB_INTEGRITY_CHECK_INTERVAL_SEC) - виявляє
            # мовчазну деградацію БД до того, як вона стане критичною.
            _Periodic(lambda: services.check_db_integrity_and_notify(self._notify),
                      error="Помилка перевірки цілісності БД",
                      interval=lambda: config.DB_INTEGRITY_CHECK_INTERVAL_SEC),
            # Автоматичний періодичний backup - страховка від втрати
            # known_devices/налаштувань, незалежно від ручного backup через
            # веб-кнопку (міг не робитись місяцями).
            _Periodic(_auto_backup, error="Помилка автоматичного backup",
                      interval=lambda: config.AUTO_BACKUP_INTERVAL_SEC,
                      enabled=lambda: config.AUTO_BACKUP_ENABLED),
            # Окремий, незалежний таймер від створення backup вище: метод має
            # власну логіку інтервалу й оновлює власний таймер всередині.
            _Periodic(lambda: self._maybe_send_backup_to_telegram(),
                      error="Помилка відправки backup у Telegram", interval=None),
        ]

    def run_forever(self) -> None:
        db.init_db()
        if _poll_pause() != config.POLL_INTERVAL_SEC:
            logger.warning(
                "STARLINK_POLL_INTERVAL=%r поза межами 1..3600 - використовую %d с (виправте на /settings)",
                config.POLL_INTERVAL_SEC, _POLL_PAUSE_DEFAULT_SEC,
            )
        logger.info("Starlink watchdog запущено. Опитування кожні %d с.", _poll_pause())

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
            self._notify(i18n.t("tg_pi_started"))

        telegram_bot.start()

        # "Прогрів" psutil.cpu_percent: перший виклик без базового заміру
        # завжди повертає 0.0, тому робимо його тут і відкидаємо результат.
        psutil.cpu_percent(interval=None)
        tasks = self._periodic_tasks()
        self._sender = _BackgroundSender()
        db.open_anchor()   # WAL "живе" весь час роботи - див. db.open_anchor()
        try:
            while True:
                try:
                    self.poll_once()
                except Exception as e:
                    logger.exception("Неочікувана помилка в циклі опитування: %s", e)

                # SD-card-wear reduction: batch-flush накопичених dish-
                # зчитувань замість запису кожного окремо кожні 10с. Свій
                # таймер (self.last_batch_flush_ts) - його оновлює й
                # flush при зміні стану, тож це не _Periodic.
                if time.time() - self.last_batch_flush_ts > config.DISH_METRICS_BATCH_INTERVAL_SEC:
                    self.flush_metrics_buffer()

                for task in tasks:
                    task.run_if_due()

                time.sleep(_poll_pause())
        finally:
            # SystemExit з обробника SIGTERM теж проходить сюди: дочекатись
            # уже поставлених сповіщень (не довше 10 с) і зупинити потік.
            sender, self._sender = self._sender, None
            if sender is not None:
                sender.stop(timeout=_NOTIFY_DRAIN_TIMEOUT_SEC)
            db.close_anchor()   # після відправника: його завдання ще можуть читати БД


def main() -> None:
    Watchdog().run_forever()


if __name__ == "__main__":
    main()
