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
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("STARLINK_MAX_FAILURES", "6"))  # 6*10s = 60s недоступності
MIN_REBOOT_INTERVAL_SEC = int(os.environ.get("STARLINK_MIN_REBOOT_INTERVAL", "180"))  # захист від reboot-loop
OBSTRUCTION_WARN_FRACTION = float(os.environ.get("STARLINK_OBSTRUCTION_WARN", "0.05"))

# Якщо dish недоступний (WiFi Starlink відсутня) довше цього часу -
# Telegram-сповіщення про спроби auto-reboot тимчасово припиняються
# (подія й далі пишеться в журнал дашборду). Відновлення зв'язку
# завжди повідомляється, незалежно від тривалості мовчання.
NOTIFICATIONS_MUTE_AFTER_SEC = int(os.environ.get("STARLINK_NOTIFICATIONS_MUTE_AFTER", "900"))
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
HISTORY_RETENTION_DAYS = int(os.environ.get("STARLINK_HISTORY_DAYS", "30"))
# Downsampling: raw-метрики (кожні POLL_INTERVAL_SEC) старші за
# DOWNSAMPLE_AFTER_DAYS агрегуються в DOWNSAMPLE_BUCKET_SEC-секундні
# середні (окрема таблиця metrics_downsampled), зменшуючи розмір БД -
# довгострокові тренди на /stats лишаються видимими (грубіші), лише
# детальна деталізація старих даних втрачається.
DOWNSAMPLE_AFTER_DAYS = int(os.environ.get("STARLINK_DOWNSAMPLE_AFTER_DAYS", "3"))
DOWNSAMPLE_BUCKET_SEC = int(os.environ.get("STARLINK_DOWNSAMPLE_BUCKET_SEC", "300"))

WEBUI_HOST = os.environ.get("STARLINK_WEBUI_HOST", "0.0.0.0")
WEBUI_PORT = int(os.environ.get("STARLINK_WEBUI_PORT", "8080"))

# GPIO BCM pin для фізичної кнопки виключення; 0 = вимкнено, 27 = дефолт
SHUTDOWN_BUTTON_GPIO_PIN = int(os.environ.get("STARLINK_SHUTDOWN_BUTTON_PIN", "27"))
SHUTDOWN_BUTTON_HOLD_SEC = float(os.environ.get("STARLINK_SHUTDOWN_BUTTON_HOLD_SEC", "3"))

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

# Періодичний реальний speedtest (не лише пропускна здатність з телеметрії
# dish, яка показує "заявлений" канал, не реальну користувацьку швидкість
# крізь весь маршрут до інтернету). Вимкнено за замовчуванням - тест
# споживає реальний трафік (десятки-сотні МБ на прогін) і на кілька
# секунд навантажує WiFi-радіомодуль, який dish/router також
# використовують для локального опитування.
SPEEDTEST_ENABLED = os.environ.get("STARLINK_SPEEDTEST_ENABLED", "0") == "1"
SPEEDTEST_INTERVAL_SEC = int(os.environ.get("STARLINK_SPEEDTEST_INTERVAL", "1800"))  # двічі/год

_local_cfg = "/etc/starlink-monitor/config.local.py"
if os.path.exists(_local_cfg):
    with open(_local_cfg) as f:
        exec(f.read())
