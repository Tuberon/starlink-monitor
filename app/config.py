"""
Конфігурація Starlink Monitor. Перевизначається через змінні
середовища (systemd EnvironmentFile) або /etc/starlink-monitor/config.local.py.
Повний опис змінних - README.md, таблиця "Конфігурація".
"""
import os

DISH_ADDR = os.environ.get("STARLINK_DISH_ADDR", "192.168.100.1:9200")
DISH_HTTP_TIMEOUT = float(os.environ.get("STARLINK_DISH_TIMEOUT", "5"))
ROUTER_ADDR = os.environ.get("STARLINK_ROUTER_ADDR", "192.168.1.1:9000")

POLL_INTERVAL_SEC = int(os.environ.get("STARLINK_POLL_INTERVAL", "10"))
# Роутерний компонент опитується РІДШЕ за dish (окремий, довший
# інтервал) - версія прошивки роутера змінюється нечасто, зайве
# навантаження на WiFi-канал при опитуванні так само часто, як dish,
# непотрібне.
ROUTER_POLL_INTERVAL_SEC = int(os.environ.get("STARLINK_ROUTER_POLL_INTERVAL_SEC", "35"))
# CPU/температура/пам'ять Pi змінюються повільно (на відміну від
# ping/швидкості каналу dish, де кожна секунда важлива) - записувати їх із
# тією самою частотою, що критичні Starlink-метрики (10с), лише
# зайве навантаження на SD-картку без практичної користі. Окремий,
# довший інтервал - зменшує кількість записів у system_metrics у
# рази, не зачіпаючи основний Starlink-моніторинг взагалі.
SYSTEM_METRICS_INTERVAL_SEC = int(os.environ.get("STARLINK_SYSTEM_METRICS_INTERVAL_SEC", "60"))
# Замість запису КОЖНОГО dish-зчитування (10с) окремою транзакцією -
# накопичуємо кілька в пам'яті, пишемо разом одним batch-INSERT.
# Зменшує кількість фізичних write-транзакцій на SD-картку в рази
# (при 30с - у ~3 рази), БЕЗ втрати жодної точки даних - усі
# зчитування все одно потрапляють у БД, лише трохи пізніше.
# Компроміс: при РАПТОВОМУ вимкненні живлення (не при звичайному
# systemctl restart/update.sh - для цього є graceful shutdown через
# SIGTERM, який flush-ить буфер негайно) можна втратити останні
# кілька зчитувань, що ще не потрапили в БД.
DISH_METRICS_BATCH_INTERVAL_SEC = int(os.environ.get("STARLINK_DISH_METRICS_BATCH_INTERVAL_SEC", "30"))
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("STARLINK_MAX_FAILURES", "6"))  # 6*10s = 60s недоступності
MIN_REBOOT_INTERVAL_SEC = int(os.environ.get("STARLINK_MIN_REBOOT_INTERVAL", "180"))  # захист від reboot-loop
OBSTRUCTION_WARN_FRACTION = float(os.environ.get("STARLINK_OBSTRUCTION_WARN", "0.05"))

# Якщо dish недоступний (WiFi Starlink відсутня) довше цього часу -
# Telegram-сповіщення про спроби auto-reboot тимчасово припиняються
# (подія й далі пишеться в журнал дашборду). Відновлення зв'язку
# повідомляється незалежно від тривалості мовчання - ЯКЩО
# NOTIFY_DISH_RECOVERY увімкнено (нижче; той параметр контролює,
# чи це сповіщення взагалі надсилається, це - лише mute-тривалість).
NOTIFICATIONS_MUTE_AFTER_SEC = int(os.environ.get("STARLINK_NOTIFICATIONS_MUTE_AFTER", "900"))
# Сповіщення "✅ Dish знову online (після N невдалих спроб)" - за
# бажанням користувача можна вимкнути (0), якщо ці короткі, часті
# flap-відновлення не інформативні особисто для нього. Журнал подій
# і далі пишеться незалежно від цього параметра.
NOTIFY_DISH_RECOVERY = os.environ.get("STARLINK_NOTIFY_DISH_RECOVERY", "1") == "1"
# "🟢 Dish Watch запущено (Raspberry Pi перезавантажено)" - лише при
# РЕАЛЬНОМУ завантаженні Pi (питання psutil.boot_time(), не при
# кожному sudo systemctl restart starlink-monitor.service під час
# оновлення коду - інакше сповіщало б набагато частіше, ніж "Pi
# увімкнувся/перезавантажився").
NOTIFY_PI_STARTUP = os.environ.get("STARLINK_NOTIFY_PI_STARTUP", "1") == "1"
# Технічні timeout/TTL для Telegram-бота (app/telegram_bot.py, обробка
# вхідних команд) - раніше hardcoded module-level константи, винесено
# для консистентності з рештою "усе через env" паттерну проєкту.
TELEGRAM_SEND_TIMEOUT_SEC = float(os.environ.get("STARLINK_TELEGRAM_SEND_TIMEOUT_SEC", "10"))
TELEGRAM_POLL_TIMEOUT_SEC = float(os.environ.get("STARLINK_TELEGRAM_POLL_TIMEOUT_SEC", "30"))
TELEGRAM_CONFIRM_TTL_SEC = float(os.environ.get("STARLINK_TELEGRAM_CONFIRM_TTL_SEC", "120"))
# Технічний timeout для app/telegram_notify.py (надсилання сповіщень,
# окремий модуль від telegram_bot.py) - той самий дефолт, що
# TELEGRAM_SEND_TIMEOUT_SEC вище, АЛЕ незалежний параметр (різні
# модулі, різні сервіси - не хочу штучно об'єднувати).
TELEGRAM_NOTIFY_TIMEOUT_SEC = float(os.environ.get("STARLINK_TELEGRAM_NOTIFY_TIMEOUT_SEC", "10"))
# Повторні спроби ЛИШЕ для мережевих помилок (timeout, DNS, з'єднання
# розірвано) - не для HTTP-рівня відповідей типу "chat not found",
# де повтор нічого не змінить. Невелика кількість (дефолт 1) і
# коротка затримка - забагато затримало б основний watchdog-цикл,
# який чекає на send_message() синхронно.
TELEGRAM_SEND_RETRIES = int(os.environ.get("STARLINK_TELEGRAM_SEND_RETRIES", "1"))
TELEGRAM_SEND_RETRY_DELAY_SEC = float(os.environ.get("STARLINK_TELEGRAM_SEND_RETRY_DELAY_SEC", "2"))
# Максимум записів у списку /id без аргументів (Telegram обмежує
# повідомлення 4096 символами - без цього ліміту довгий список
# known_devices міг би бути повністю відхилений API, виглядаючи
# як "команда не відповідає взагалі").
TELEGRAM_ID_LIST_MAX_ITEMS = int(os.environ.get("STARLINK_TELEGRAM_ID_LIST_MAX_ITEMS", "40"))
# Групування спаму reboot-сповіщень - на відміну від MUTE_AFTER (одна
# ТРИВАЛА відмова), це про ЧАСТОТУ: REBOOT_SPAM_THRESHOLD+ окремих
# reboot-сповіщень за REBOOT_SPAM_WINDOW_SEC (флап коротких циклів,
# кожен проходить MIN_REBOOT_INTERVAL_SEC і тому не приглушується
# MUTE_AFTER) - призупиняє індивідуальні повідомлення до затишшя.
REBOOT_SPAM_THRESHOLD = int(os.environ.get("STARLINK_REBOOT_SPAM_THRESHOLD", "3"))
REBOOT_SPAM_WINDOW_SEC = int(os.environ.get("STARLINK_REBOOT_SPAM_WINDOW_SEC", "1800"))

# При тривалій безперервній недоступності dish кожна watchdog-спроба
# reboot (кожні MIN_REBOOT_INTERVAL_SEC) і далі пишеться в журнал/БД
# з дедалі більшим лічильником невдалих опитувань - засмічує журнал
# без нової корисної інформації. Після цієї кількості послідовних
# спроб записи в журнал/БД припиняються (сама спроба reboot триває
# нормально); один фінальний запис позначає момент припинення.
MAX_LOGGED_CONSECUTIVE_FAILURES = int(os.environ.get("STARLINK_MAX_LOGGED_FAILURES", "15"))

# reboot при software_update_state==REBOOT_REQUIRED або alerts.install_pending
AUTO_REBOOT_ON_UPDATE_READY = os.environ.get("STARLINK_AUTO_REBOOT_ON_UPDATE", "1") == "1"

DB_PATH = os.environ.get("STARLINK_DB_PATH", "/var/lib/starlink-monitor/history.db")
# Автоматичний періодичний backup (страховка від втрати known_devices/
# налаштувань при пошкодженні БД чи виходу SD-картки з ладу - на
# відміну від ручного через веб-кнопку, який user міг не робити
# місяцями). Дефолт увімкнено - на відміну від опційних дій, що
# впливають на сам моніторинг чи обладнання, backup - чисто корисна
# дія без побічних ефектів.
AUTO_BACKUP_ENABLED = os.environ.get("STARLINK_AUTO_BACKUP_ENABLED", "1") == "1"
AUTO_BACKUP_INTERVAL_SEC = int(os.environ.get("STARLINK_AUTO_BACKUP_INTERVAL_SEC", "604800"))  # тиждень
AUTO_BACKUP_KEEP_COUNT = int(os.environ.get("STARLINK_AUTO_BACKUP_KEEP_COUNT", "4"))
# Опційна періодична відправка ОСТАННЬОГО (найновішого) backup-файлу
# в Telegram як документ - страховка, якщо AUTO_BACKUP_DIR лишається
# лише на тій самій SD-картці, яка може вийти з ладу разом з БД.
# Окремий, незалежний інтервал від AUTO_BACKUP_INTERVAL_SEC (створення
# backup) - можна створювати частіше, надсилати рідше (щоб не спамити
# Telegram великими файлами), чи навпаки.
TELEGRAM_BACKUP_ENABLED = os.environ.get("STARLINK_TELEGRAM_BACKUP_ENABLED", "0") == "1"
TELEGRAM_BACKUP_INTERVAL_HOURS = float(os.environ.get("STARLINK_TELEGRAM_BACKUP_INTERVAL_HOURS", "168"))
AUTO_BACKUP_DIR = os.environ.get(
    "STARLINK_AUTO_BACKUP_DIR", os.path.join(os.path.dirname(DB_PATH), "backups")
)
# Перевірка цілісності БД (PRAGMA quick_check) - виявляє мовчазну
# деградацію ДО того, як вона стане критичною. Той самий щоденний
# цикл, що VACUUM - не частіше, не потребує.
DB_INTEGRITY_CHECK_INTERVAL_SEC = int(os.environ.get("STARLINK_DB_INTEGRITY_CHECK_INTERVAL_SEC", "86400"))
HISTORY_RETENTION_DAYS = int(os.environ.get("STARLINK_HISTORY_DAYS", "30"))

WEBUI_HOST = os.environ.get("STARLINK_WEBUI_HOST", "0.0.0.0")
WEBUI_PORT = int(os.environ.get("STARLINK_WEBUI_PORT", "8080"))

# GPIO BCM pin для фізичної кнопки виключення; 0 = вимкнено, 27 = дефолт
SHUTDOWN_BUTTON_GPIO_PIN = int(os.environ.get("STARLINK_SHUTDOWN_BUTTON_PIN", "27"))
SHUTDOWN_BUTTON_HOLD_SEC = float(os.environ.get("STARLINK_SHUTDOWN_BUTTON_HOLD_SEC", "3"))
# Додатковий GPIO-світлодіод, що коротко блимає при кожному реальному
# записі в SQLite (dish-метрики, system_metrics, events, router-
# status, backup, VACUUM) - візуальна індикація активності SD-картки.
# Вимкнено за замовчуванням (0) - опційна периферія, як shutdown-
# кнопка й TFT-дисплей; дефолт 17 - вільний GPIO-пін, не перетинається
# з SHUTDOWN_BUTTON_GPIO_PIN (27) чи DISPLAY_*_PIN (8/25/24/18).
ACTIVITY_LED_PIN = int(os.environ.get("STARLINK_ACTIVITY_LED_PIN", "0"))
ACTIVITY_LED_BLINK_MS = int(os.environ.get("STARLINK_ACTIVITY_LED_BLINK_MS", "50"))
# Як часто перевіряти стан GPIO-піна кнопки виключення - окремий від
# DISPLAY_BUTTON_POLL_INTERVAL_SEC нижче, бо це РІЗНІ сервіси
# (shutdown_button.py, окремий від display.py), навіть якщо дефолт
# однаковий (0.1с).
SHUTDOWN_BUTTON_POLL_INTERVAL_SEC = float(os.environ.get("STARLINK_SHUTDOWN_BUTTON_POLL_INTERVAL_SEC", "0.1"))

# Фізичний TFT-дисплей (ST7789, SPI) - показує live-статус dish прямо
# на екрані, без потреби відкривати веб-дашборд. Вимкнено за
# замовчуванням - не всі мають цей дисплей підключений. Дефолти
# відповідають офіційній специфікації конкретної моделі (SKU MSP1901,
# 1.9" IPS, 170x320, driver ST7789, 4-line SPI) - піни (DC/RST/BL)
# все одно залежать від фактичного підключення до Pi, редагуються
# через /etc/starlink-monitor/env чи /settings.
DISPLAY_ENABLED = os.environ.get("STARLINK_DISPLAY_ENABLED", "0") == "1"
# Adafruit CircuitPython ST7789 - SPI clock/MOSI/MISO завжди апаратні (board.SCK/MOSI/MISO, стандартний SPI0). CS - НЕ
# апаратний CE0/CE1 номер (0/1), а звичайний GPIO-пін (бібліотека сама
# перемикає його програмно навколо кожної транзакції) - дефолт 8
# (BCM8=CE0 фізично, але тут використовується як bit-banged GPIO).
DISPLAY_SPI_CS_PIN = int(os.environ.get("STARLINK_DISPLAY_SPI_CS_PIN", "8"))
# DC/RST/BL - НЕ можуть бути в діапазоні BCM 7-11 (апаратні SPI0-піни
# CE1/CE0/MISO/MOSI/SCLK, зарезервовані на рівні ядра при dtparam=spi=on,
# недоступні одночасно як звичайні GPIO). 25/24/18 - стандартний,
# безконфліктний вибір для SPI TFT-дисплеїв на Raspberry Pi.
DISPLAY_DC_PIN = int(os.environ.get("STARLINK_DISPLAY_DC_PIN", "25"))
DISPLAY_RST_PIN = int(os.environ.get("STARLINK_DISPLAY_RST_PIN", "24"))
DISPLAY_BL_PIN = int(os.environ.get("STARLINK_DISPLAY_BL_PIN", "18"))
# 170x320 (portrait) - паспортна роздільна здатність моделі MSP1901,
# підтверджена офіційною документацією виробника (LCDWIKI) і
# написом на самій платі ("1.9" IPS 170x320(RGB)"). Попередня версія
# мала дефолти 320x170 (поміняні місцями) - це БУВ ПОМИЛКОВИЙ
# висновок з емпіричного тесту (реальна причина залишкового шуму на
# 320x170 - командування контролеру 320 стовпців, тоді як фізична
# матриця має лише 170; "покращення" було випадковим побічним
# ефектом, не правильним рішенням) - див. docs/decisions-log.md.
DISPLAY_WIDTH = int(os.environ.get("STARLINK_DISPLAY_WIDTH", "170"))
DISPLAY_HEIGHT = int(os.environ.get("STARLINK_DISPLAY_HEIGHT", "320"))
# rotation=0/90/180/270 підтримується для будь-якого aspect ratio -
# обертання застосовується програмно через PIL img.rotate(), не через
# MADCTL.
DISPLAY_ROTATION = int(os.environ.get("STARLINK_DISPLAY_ROTATION", "0"))
# Зміщення видимої області відносно GRAM контролера - типова потреба
# для дешевих ST7789-клонів (видима область менша за фізичний GRAM
# контролера, потрібне центрування). Емпірично підтверджено на
# реальному Pi для цієї моделі (SKU MSP1901): без зміщення (0)
# частина екрана показувала стабільний кольоровий шум (не апаратний
# дефект, як спершу помилково припускалось - див. docs/decisions-
# log.md). Значення 35-50 (принаймні) усувають шум - це діапазон,
# не одне точне число; 35 обрано як дефолт.
DISPLAY_OFFSET_LEFT = int(os.environ.get("STARLINK_DISPLAY_OFFSET_LEFT", "35"))
DISPLAY_OFFSET_TOP = int(os.environ.get("STARLINK_DISPLAY_OFFSET_TOP", "0"))
DISPLAY_REFRESH_SEC = int(os.environ.get("STARLINK_DISPLAY_REFRESH_SEC", "5"))
# Затримка ПЕРЕД реальним systemctl reboot/poweroff - дає display.py
# (окремий процес, опитує швидким циклом ~100мс, не чекає звичайний
# 5-секундний REFRESH_SEC) час намалювати повідомлення "Вимикається/
# Перезавантажується" на екрані ДО того, як SIGTERM від самого
# reboot/poweroff вб'є процес дисплея. Не застосовується, якщо
# DISPLAY_ENABLED=0 - немає екрана, немає сенсу чекати.
DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC = float(os.environ.get("STARLINK_DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC", "2"))
# Офіційна специфікація модуля не вказує максимальну частоту SPI.
# 40МГц - консервативний дефолт для типового підключення джампер-
# дротами (не пресована плата) - на такому монтажі вищі частоти
# (60-80МГц) підвищують ризик спотворення сигналу/помилок зчитування.
# Підніми через env, якщо підключення якісне (короткі дроти/шлейф).
DISPLAY_SPI_SPEED_HZ = int(os.environ.get("STARLINK_DISPLAY_SPI_SPEED_HZ", "40000000"))
# Кнопка підсвітки дисплея - та сама, що й SHUTDOWN_BUTTON_GPIO_PIN
# (коротке натискання перемикає підсвітку, довге - вимикає Pi, як і
# раніше). Обробляється в display.py (не в shutdown_button.py, який
# сам себе вимикає при DISPLAY_ENABLED=1), бо керування підсвіткою
# можливе лише через той самий об'єкт, що володіє SPI/BL-піном.
# Автовимкнення підсвітки через N сек після ввімкнення (нічний режим,
# економія) - 0 вимикає фічу (підсвітка лишається доти, доки не
# перемкнеш кнопкою вручну).
DISPLAY_BACKLIGHT_AUTO_OFF_SEC = int(os.environ.get("STARLINK_DISPLAY_BACKLIGHT_AUTO_OFF_SEC", "60"))
# Коли update_state (dish чи router) змінюється - підсвітка вмикається
# на цей час і потім явно вимикається (0 вимикає фічу повністю).
DISPLAY_UPDATE_FLASH_SEC = int(os.environ.get("STARLINK_DISPLAY_UPDATE_FLASH_SEC", "5"))
# Як часто перевіряти стан GPIO-піна кнопки в display.py - окремий
# від SHUTDOWN_BUTTON_POLL_INTERVAL_SEC вище (та сама роль, інший
# сервіс/файл - shutdown_button.py, не display.py).
DISPLAY_BUTTON_POLL_INTERVAL_SEC = float(os.environ.get("STARLINK_DISPLAY_BUTTON_POLL_INTERVAL_SEC", "0.1"))

_local_cfg = "/etc/starlink-monitor/config.local.py"
if os.path.exists(_local_cfg):
    with open(_local_cfg) as f:
        exec(f.read())
