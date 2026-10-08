"""
Конфігурація Starlink Monitor. Перевизначається через змінні
середовища (systemd EnvironmentFile /etc/starlink-monitor/env; редагується
на сторінці /settings).
Повний опис змінних - README.md, таблиця "Конфігурація".
"""
import logging
import math
import os

_log = logging.getLogger("config")

def _invalid(name: str, raw: str, why: str, default: str) -> None:
    _log.warning("%s=%r — %s; використано типове значення %s", name, raw, why, default)


def _env_int(name: str, default: str) -> int:
    """Некоректне значення (порожнє, не число) НЕ валить імпорт: інакше від одного рядка `STARLINK_X=` у env
    падали б усі процеси, зокрема /settings, яким це можна виправити (лишався б лише SSH)."""
    raw = os.environ.get(name, default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        _invalid(name, raw, "не ціле число", default)
        return int(default)


def _env_float(name: str, default: str) -> float:
    raw = os.environ.get(name, default)
    try:
        value = float(raw)
    except (TypeError, ValueError):
        _invalid(name, raw, "не число", default)
        return float(default)
    if not math.isfinite(value):
        _invalid(name, raw, "не скінченне число", default)
        return float(default)
    return value


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _env_bool(name: str, default: str) -> bool:
    """1/true/yes/on і 0/false/no/off (без урахування регістру). Раніше перевірялось лише == "1", тож ручне
    `true` мовчки ВИМИКАЛО функцію."""
    raw = os.environ.get(name, default)
    word = raw.strip().lower() if isinstance(raw, str) else ""
    if word in _TRUE:
        return True
    if word in _FALSE:
        return False
    _invalid(name, raw, "не булеве значення (1/0, true/false, yes/no, on/off)", default)
    return default.strip().lower() in _TRUE


def _env_text(name: str, default: str) -> str:
    """Адреса/шлях: порожнє значення недопустиме (порожній DB_PATH означав би тимчасову БД SQLite, тобто тиху втрату даних)."""
    raw = os.environ.get(name, default)
    if not isinstance(raw, str) or not raw.strip():
        _invalid(name, raw, "порожнє значення", default)
        return default
    return raw.strip()

DISH_ADDR = _env_text("STARLINK_DISH_ADDR", "192.168.100.1:9200")
DISH_HTTP_TIMEOUT = _env_float("STARLINK_DISH_TIMEOUT", "5")
ROUTER_ADDR = _env_text("STARLINK_ROUTER_ADDR", "192.168.1.1:9000")

POLL_INTERVAL_SEC = _env_int("STARLINK_POLL_INTERVAL", "10")
# Роутерний компонент опитується РІДШЕ за dish (окремий, довший
# інтервал) - версія прошивки роутера змінюється нечасто, зайве
# навантаження на WiFi-канал при опитуванні так само часто, як dish,
# непотрібне.
ROUTER_POLL_INTERVAL_SEC = _env_int("STARLINK_ROUTER_POLL_INTERVAL_SEC", "35")
# CPU/температура/пам'ять Pi змінюються повільно, тож писати їх так само часто, як критичні
# Starlink-метрики (10 с), — зайве навантаження на SD. Окремий довший інтервал у рази зменшує кількість
# записів у system_metrics, не зачіпаючи моніторинг Starlink.
SYSTEM_METRICS_INTERVAL_SEC = _env_int("STARLINK_SYSTEM_METRICS_INTERVAL_SEC", "60")
# Замість запису КОЖНОГО dish-зчитування (10 с) окремою транзакцією — кілька в пам'яті й один
# batch-INSERT: в рази менше write-транзакцій на SD (при 30 с — ~3×) без втрати точок. Компроміс: при
# РАПТОВОМУ вимкненні живлення (не при systemctl restart/update.sh — SIGTERM скидає буфер негайно) можна
# втратити кілька останніх зчитувань.
DISH_METRICS_BATCH_INTERVAL_SEC = _env_int("STARLINK_DISH_METRICS_BATCH_INTERVAL_SEC", "30")
MAX_CONSECUTIVE_FAILURES = _env_int("STARLINK_MAX_FAILURES", "6")  # 6*10s = 60s недоступності
MIN_REBOOT_INTERVAL_SEC = _env_int("STARLINK_MIN_REBOOT_INTERVAL", "180")  # захист від reboot-loop
OBSTRUCTION_WARN_FRACTION = _env_float("STARLINK_OBSTRUCTION_WARN", "0.05")

# Якщо dish недоступний довше цього часу — Telegram-сповіщення про спроби auto-reboot припиняються
# (подія й далі пишеться в журнал дашборду). Відновлення повідомляється незалежно від тривалості, ЯКЩО
# NOTIFY_DISH_RECOVERY увімкнено (він керує самим сповіщенням, це — лише тривалість mute).
NOTIFICATIONS_MUTE_AFTER_SEC = _env_int("STARLINK_NOTIFICATIONS_MUTE_AFTER", "900")
# Сповіщення "✅ Dish знову online (після N невдалих спроб)" - за
# бажанням користувача можна вимкнути (0), якщо ці короткі, часті
# flap-відновлення не інформативні особисто для нього. Журнал подій
# і далі пишеться незалежно від цього параметра.
NOTIFY_DISH_RECOVERY = _env_bool("STARLINK_NOTIFY_DISH_RECOVERY", "1")
# "🟢 Dish Watch запущено (Raspberry Pi перезавантажено)" - лише при
# РЕАЛЬНОМУ завантаженні Pi (питання psutil.boot_time(), не при
# кожному sudo systemctl restart starlink-monitor.service під час
# оновлення коду - інакше сповіщало б набагато частіше, ніж "Pi
# увімкнувся/перезавантажився").
NOTIFY_PI_STARTUP = _env_bool("STARLINK_NOTIFY_PI_STARTUP", "1")
# Технічні timeout/TTL для Telegram-бота (app/telegram_bot.py, обробка
# вхідних команд) - раніше hardcoded module-level константи, винесено
# для консистентності з рештою "усе через env" паттерну проєкту.
TELEGRAM_SEND_TIMEOUT_SEC = _env_float("STARLINK_TELEGRAM_SEND_TIMEOUT_SEC", "10")
TELEGRAM_POLL_TIMEOUT_SEC = _env_float("STARLINK_TELEGRAM_POLL_TIMEOUT_SEC", "30")
TELEGRAM_CONFIRM_TTL_SEC = _env_float("STARLINK_TELEGRAM_CONFIRM_TTL_SEC", "120")
# Технічний timeout для app/telegram_notify.py (надсилання сповіщень,
# окремий модуль від telegram_bot.py) - той самий дефолт, що
# TELEGRAM_SEND_TIMEOUT_SEC вище, АЛЕ незалежний параметр (різні
# модулі, різні сервіси - не хочу штучно об'єднувати).
TELEGRAM_NOTIFY_TIMEOUT_SEC = _env_float("STARLINK_TELEGRAM_NOTIFY_TIMEOUT_SEC", "10")
# Повторні спроби ЛИШЕ для мережевих помилок (timeout, DNS, з'єднання
# розірвано) - не для HTTP-рівня відповідей типу "chat not found",
# де повтор нічого не змінить. Невелика кількість (дефолт 1) і
# коротка затримка - забагато затримало б основний watchdog-цикл,
# який чекає на send_message() синхронно.
TELEGRAM_SEND_RETRIES = _env_int("STARLINK_TELEGRAM_SEND_RETRIES", "1")
TELEGRAM_SEND_RETRY_DELAY_SEC = _env_float("STARLINK_TELEGRAM_SEND_RETRY_DELAY_SEC", "2")
# Максимум записів у списку /id без аргументів (Telegram обмежує
# повідомлення 4096 символами - без цього ліміту довгий список
# known_devices міг би бути повністю відхилений API, виглядаючи
# як "команда не відповідає взагалі").
TELEGRAM_ID_LIST_MAX_ITEMS = _env_int("STARLINK_TELEGRAM_ID_LIST_MAX_ITEMS", "40")
# Групування спаму reboot-сповіщень - на відміну від MUTE_AFTER (одна
# ТРИВАЛА відмова), це про ЧАСТОТУ: REBOOT_SPAM_THRESHOLD+ окремих
# reboot-сповіщень за REBOOT_SPAM_WINDOW_SEC (флап коротких циклів,
# кожен проходить MIN_REBOOT_INTERVAL_SEC і тому не приглушується
# MUTE_AFTER) - призупиняє індивідуальні повідомлення до затишшя.
REBOOT_SPAM_THRESHOLD = _env_int("STARLINK_REBOOT_SPAM_THRESHOLD", "3")
REBOOT_SPAM_WINDOW_SEC = _env_int("STARLINK_REBOOT_SPAM_WINDOW_SEC", "1800")

# При тривалій недоступності dish кожна watchdog-спроба reboot (кожні MIN_REBOOT_INTERVAL_SEC)
# засмічувала б журнал/БД без нової інформації. Після цієї кількості послідовних спроб запис у журнал/БД
# припиняється (сама спроба reboot триває); один фінальний запис позначає момент припинення.
MAX_LOGGED_CONSECUTIVE_FAILURES = _env_int("STARLINK_MAX_LOGGED_FAILURES", "15")

# reboot при software_update_state==REBOOT_REQUIRED або alerts.install_pending
AUTO_REBOOT_ON_UPDATE_READY = _env_bool("STARLINK_AUTO_REBOOT_ON_UPDATE", "1")

# Навмисно НЕ в /settings (EDITABLE_PARAMS): зміна шляху до БД через веб-UI, поки сервіс читає/пише в
# стару, вимагала б ручної міграції даних, а друкарська помилка виглядала б як "уся історія зникла".
# Лише вручну в /etc/starlink-monitor/env — свідомий крок.
DB_PATH = _env_text("STARLINK_DB_PATH", "/var/lib/starlink-monitor/history.db")
# Автоматичний періодичний backup — страховка від втрати known_devices/налаштувань при пошкодженні БД чи
# виході SD з ладу (ручний через кнопку user міг не робити місяцями). Дефолт увімкнено: на відміну від
# дій, що чіпають моніторинг чи обладнання, це безпечна корисна дія.
AUTO_BACKUP_ENABLED = _env_bool("STARLINK_AUTO_BACKUP_ENABLED", "1")
AUTO_BACKUP_INTERVAL_SEC = _env_int("STARLINK_AUTO_BACKUP_INTERVAL_SEC", "604800")  # тиждень
AUTO_BACKUP_KEEP_COUNT = _env_int("STARLINK_AUTO_BACKUP_KEEP_COUNT", "4")
# Опційна періодична відправка ОСТАННЬОГО backup у Telegram як документ — страховка на випадок, якщо
# AUTO_BACKUP_DIR лежить на тій самій SD, що й БД. Інтервал незалежний від AUTO_BACKUP_INTERVAL_SEC:
# створювати можна частіше, надсилати рідше (чи навпаки).
TELEGRAM_BACKUP_ENABLED = _env_bool("STARLINK_TELEGRAM_BACKUP_ENABLED", "0")
TELEGRAM_BACKUP_INTERVAL_HOURS = _env_float("STARLINK_TELEGRAM_BACKUP_INTERVAL_HOURS", "168")
AUTO_BACKUP_DIR = _env_text("STARLINK_AUTO_BACKUP_DIR", os.path.join(os.path.dirname(DB_PATH), "backups"))
# Перевірка цілісності БД (PRAGMA quick_check) - виявляє мовчазну
# деградацію ДО того, як вона стане критичною. Той самий щоденний
# цикл, що VACUUM - не частіше, не потребує.
DB_INTEGRITY_CHECK_INTERVAL_SEC = _env_int("STARLINK_DB_INTEGRITY_CHECK_INTERVAL_SEC", "86400")
HISTORY_RETENTION_DAYS = _env_int("STARLINK_HISTORY_DAYS", "14")

# Навмисно НЕ в /settings (EDITABLE_PARAMS) - self-lockout ризик:
# зміна адреси прослуховування через сам веб-інтерфейс могла б
# відрізати користувача від доступу до /settings з іншого пристрою
# в мережі (напр. звуження на 127.0.0.1). Редагування - лише вручну
# в /etc/starlink-monitor/env, з розумінням наслідків.
WEBUI_HOST = _env_text("STARLINK_WEBUI_HOST", "0.0.0.0")  # noqa: S104 - усі інтерфейси: рішення власника (docs/decisions-log.md)
WEBUI_PORT = _env_int("STARLINK_WEBUI_PORT", "8080")

# GPIO BCM pin для фізичної кнопки виключення; 0 = вимкнено, 27 = дефолт
SHUTDOWN_BUTTON_GPIO_PIN = _env_int("STARLINK_SHUTDOWN_BUTTON_PIN", "27")
SHUTDOWN_BUTTON_HOLD_SEC = _env_float("STARLINK_SHUTDOWN_BUTTON_HOLD_SEC", "3")
# Опційний GPIO-світлодіод, що коротко блимає при кожному реальному записі в SQLite (метрики, events,
# router-status, backup, VACUUM) — індикація активності SD. Вимкнено (0), як shutdown-кнопка й TFT;
# дефолт 17 не перетинається з SHUTDOWN_BUTTON_GPIO_PIN (27) і DISPLAY_*_PIN (8/25/24/18).
ACTIVITY_LED_PIN = _env_int("STARLINK_ACTIVITY_LED_PIN", "0")
ACTIVITY_LED_BLINK_MS = _env_int("STARLINK_ACTIVITY_LED_BLINK_MS", "50")
# Як часто перевіряти стан GPIO-піна кнопки виключення - окремий від
# DISPLAY_BUTTON_POLL_INTERVAL_SEC нижче, бо це РІЗНІ сервіси
# (shutdown_button.py, окремий від display.py), навіть якщо дефолт
# однаковий (0.1с).
SHUTDOWN_BUTTON_POLL_INTERVAL_SEC = _env_float("STARLINK_SHUTDOWN_BUTTON_POLL_INTERVAL_SEC", "0.1")

# Фізичний TFT-дисплей (ST7789, SPI) - показує live-статус dish прямо
# на екрані, без потреби відкривати веб-дашборд. Вимкнено за
# замовчуванням - не всі мають цей дисплей підключений. Дефолти
# відповідають офіційній специфікації конкретної моделі (SKU MSP1901,
# 1.9" IPS, 170x320, driver ST7789, 4-line SPI) - піни (DC/RST/BL)
# все одно залежать від фактичного підключення до Pi, редагуються
# через /etc/starlink-monitor/env чи /settings.
DISPLAY_ENABLED = _env_bool("STARLINK_DISPLAY_ENABLED", "0")
# Adafruit CircuitPython ST7789 - SPI clock/MOSI/MISO завжди апаратні (board.SCK/MOSI/MISO, стандартний SPI0). CS - НЕ
# апаратний CE0/CE1 номер (0/1), а звичайний GPIO-пін (бібліотека сама
# перемикає його програмно навколо кожної транзакції) - дефолт 8
# (BCM8=CE0 фізично, але тут використовується як bit-banged GPIO).
DISPLAY_SPI_CS_PIN = _env_int("STARLINK_DISPLAY_SPI_CS_PIN", "8")
# DC/RST/BL - НЕ можуть бути в діапазоні BCM 7-11 (апаратні SPI0-піни
# CE1/CE0/MISO/MOSI/SCLK, зарезервовані на рівні ядра при dtparam=spi=on,
# недоступні одночасно як звичайні GPIO). 25/24/18 - стандартний,
# безконфліктний вибір для SPI TFT-дисплеїв на Raspberry Pi.
DISPLAY_DC_PIN = _env_int("STARLINK_DISPLAY_DC_PIN", "25")
DISPLAY_RST_PIN = _env_int("STARLINK_DISPLAY_RST_PIN", "24")
DISPLAY_BL_PIN = _env_int("STARLINK_DISPLAY_BL_PIN", "18")
# 170x320 (portrait) — паспортна роздільна здатність MSP1901 (документація LCDWIKI і напис на платі
# "1.9" IPS 170x320(RGB)"). Дефолти 320x170 були помилковим висновком з емпіричного тесту: шум на
# 320x170 — це команда контролеру на 320 стовпців при фізичній матриці 170, а "покращення" було побічним
# ефектом (див. docs/decisions-log.md).
DISPLAY_WIDTH = _env_int("STARLINK_DISPLAY_WIDTH", "170")
DISPLAY_HEIGHT = _env_int("STARLINK_DISPLAY_HEIGHT", "320")
# rotation=0/90/180/270 підтримується для будь-якого aspect ratio -
# обертання застосовується програмно через PIL img.rotate(), не через
# MADCTL.
DISPLAY_ROTATION = _env_int("STARLINK_DISPLAY_ROTATION", "0")
# Зміщення видимої області відносно GRAM контролера — типове для дешевих ST7789-клонів. Емпірично
# підтверджено на Pi для SKU MSP1901: без зміщення (0) частина екрана показувала кольоровий шум (не
# апаратний дефект, як спершу вважалось — див. docs/decisions-log.md). Значення 35-50 усувають шум
# (діапазон, не одне число); дефолт 35.
DISPLAY_OFFSET_LEFT = _env_int("STARLINK_DISPLAY_OFFSET_LEFT", "35")
DISPLAY_OFFSET_TOP = _env_int("STARLINK_DISPLAY_OFFSET_TOP", "0")
DISPLAY_REFRESH_SEC = _env_int("STARLINK_DISPLAY_REFRESH_SEC", "5")
# Затримка ПЕРЕД systemctl reboot/poweroff: дає display.py (окремий процес, швидкий цикл ~100 мс) час
# намалювати "Вимикається/Перезавантажується" ДО того, як SIGTERM від самого reboot/poweroff уб'є процес
# дисплея. Не застосовується при DISPLAY_ENABLED=0.
DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC = _env_float("STARLINK_DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC", "2")
# Специфікація модуля не вказує максимальну частоту SPI. 40 МГц — консервативний дефолт для підключення
# джампер-дротами: вищі частоти (60-80 МГц) підвищують ризик спотворень. Підніміть через env, якщо дроти
# короткі чи шлейф якісний.
DISPLAY_SPI_SPEED_HZ = _env_int("STARLINK_DISPLAY_SPI_SPEED_HZ", "40000000")
# Кнопка підсвітки дисплея - та сама, що й SHUTDOWN_BUTTON_GPIO_PIN
# (коротке натискання перемикає підсвітку, довге - вимикає Pi, як і
# раніше). Обробляється в display.py (не в shutdown_button.py, який
# сам себе вимикає при DISPLAY_ENABLED=1), бо керування підсвіткою
# можливе лише через той самий об'єкт, що володіє SPI/BL-піном.
# Автовимкнення підсвітки через N сек після ввімкнення (нічний режим,
# економія) - 0 вимикає фічу (підсвітка лишається доти, доки не
# перемкнеш кнопкою вручну).
DISPLAY_BACKLIGHT_AUTO_OFF_SEC = _env_int("STARLINK_DISPLAY_BACKLIGHT_AUTO_OFF_SEC", "60")
# Коли update_state (dish чи router) змінюється - підсвітка вмикається
# на цей час і потім явно вимикається (0 вимикає фічу повністю).
DISPLAY_UPDATE_FLASH_SEC = _env_int("STARLINK_DISPLAY_UPDATE_FLASH_SEC", "5")
# Як часто перевіряти стан GPIO-піна кнопки в display.py - окремий
# від SHUTDOWN_BUTTON_POLL_INTERVAL_SEC вище (та сама роль, інший
# сервіс/файл - shutdown_button.py, не display.py).
DISPLAY_BUTTON_POLL_INTERVAL_SEC = _env_float("STARLINK_DISPLAY_BUTTON_POLL_INTERVAL_SEC", "0.1")
