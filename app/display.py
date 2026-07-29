"""
Фізичний TFT-дисплей (ST7789, SPI) - показує live-статус Starlink
Mini (online/offline, uptime) прямо на екрані, підключеному до Pi,
без потреби відкривати веб-дашборд.

Використовує Adafruit CircuitPython ST7789 (adafruit-circuitpython-
rgb-display + adafruit-blinka). Ключові особливості:
- reset() з правильною послідовністю викликається автоматично в
  конструкторі, до SPI-команд ініціалізації;
- rotation=0/90/180/270 підтримується для будь-якого aspect ratio,
  застосовується програмно через PIL img.rotate();
- НЕ має вбудованого керування підсвіткою (set_backlight()) - BL-пін
  керується напряму через окремий digitalio.DigitalInOut в цьому
  модулі (_set_backlight()).

Та сама кнопка виключення Pi (SHUTDOWN_BUTTON_GPIO_PIN) обробляється
тут: коротке натискання перемикає підсвітку дисплея, довге (довше
SHUTDOWN_BUTTON_HOLD_SEC) вимикає Pi - як і раніше. Обробляється саме
тут (не в окремому shutdown_button.py), бо керування підсвіткою
можливе лише через той самий процес, що тримає BL-пін; той сервіс сам
себе вимикає, коли DISPLAY_ENABLED=1, щоб не конкурувати за той самий
GPIO.

Окремий процес - періодично перемальовує кадр через Pillow і надсилає
на дисплей. Вимкнено за замовчуванням (DISPLAY_ENABLED=0) - не всі
мають цей дисплей підключений.
"""
import logging
import time

from app import config, db, gpio_utils
from app.shutdown_button import _trigger_shutdown

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("display")

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
BUTTON_POLL_INTERVAL_SEC = 0.1  # як часто перевіряти кнопку


def _fmt_uptime(uptime_s) -> str:
    if not uptime_s:
        return "—"
    h = int(uptime_s) // 3600
    m = (int(uptime_s) % 3600) // 60
    return f"{h}г {m}хв"


def _status_lines(latest_metric: dict) -> list:
    """Формує рядки тексту для відображення - чиста функція без
    залежності від самого дисплея, легко тестується окремо. Свідомо
    НЕ показує downlink/uplink/ping/drop%/obstruction% (замалий екран
    для змістовних числових метрик, це вже є на веб-дашборді) - лише
    online/offline статус і uptime."""
    if not latest_metric:
        return ["Немає даних"]

    online = bool(latest_metric.get("online"))
    lines = ["● ONLINE" if online else "○ OFFLINE"]
    lines.append(f"Uptime: {_fmt_uptime(latest_metric.get('uptime_s'))}")
    return lines


def _should_auto_off(backlight_on: bool, last_activity_ts: float, now: float, timeout_sec: int) -> bool:
    """Чи час автоматично вимкнути підсвітку через бездіяльність -
    чиста функція, легко тестується без реального дисплея/таймерів.
    timeout_sec<=0 - фіча вимкнена (завжди False)."""
    if timeout_sec <= 0 or not backlight_on:
        return False
    return (now - last_activity_ts) >= timeout_sec


def _load_font(size: int):
    from PIL import ImageFont
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _set_backlight(bl_pin, value: bool):
    """Adafruit CircuitPython ST7789 не має вбудованого set_backlight() -
    BL керується напряму через цей окремий digitalio-пін. bl_pin=None
    (DISPLAY_BL_PIN=0, підсвітка на 3.3V напряму) - нічого не робимо."""
    if bl_pin is not None:
        bl_pin.value = value


def _redraw(display, Image, ImageDraw, font_big, font_small):
    latest = db.get_latest_metric()
    lines = _status_lines(latest)
    online = bool(latest and latest.get("online"))

    img = Image.new("RGB", (display.width, display.height), "black")
    draw = ImageDraw.Draw(img)
    y = 10
    for i, line in enumerate(lines):
        font = font_big if i == 0 else font_small
        color = ("lime" if online else "red") if i == 0 else "white"
        draw.text((10, y), line, font=font, fill=color)
        y += 34 if i == 0 else 28

    display.image(img)


def run_forever(stop_event=None):
    if not config.DISPLAY_ENABLED:
        logger.info("DISPLAY_ENABLED не встановлено (0) - дисплей вимкнено, завершення")
        return

    try:
        import board
        import digitalio
        import busio
        from adafruit_rgb_display import st7789
        from PIL import Image, ImageDraw
    except ImportError as e:
        logger.error(
            "Пакети для дисплея не встановлено (adafruit-blinka/"
            "adafruit-circuitpython-rgb-display/Pillow): %s", e
        )
        return

    bl_pin = None
    try:
        spi = busio.SPI(clock=board.SCK, MOSI=board.MOSI, MISO=board.MISO)
        cs_pin = digitalio.DigitalInOut(getattr(board, f"D{config.DISPLAY_SPI_CS_PIN}"))
        dc_pin = digitalio.DigitalInOut(getattr(board, f"D{config.DISPLAY_DC_PIN}"))
        rst_pin = digitalio.DigitalInOut(getattr(board, f"D{config.DISPLAY_RST_PIN}"))

        display = st7789.ST7789(
            spi,
            cs=cs_pin,
            dc=dc_pin,
            rst=rst_pin,
            width=config.DISPLAY_WIDTH,
            height=config.DISPLAY_HEIGHT,
            baudrate=config.DISPLAY_SPI_SPEED_HZ,
            x_offset=config.DISPLAY_OFFSET_LEFT,
            y_offset=config.DISPLAY_OFFSET_TOP,
            rotation=config.DISPLAY_ROTATION,
        )

        if config.DISPLAY_BL_PIN:
            bl_pin = digitalio.DigitalInOut(getattr(board, f"D{config.DISPLAY_BL_PIN}"))
            bl_pin.switch_to_output(value=True)
    except Exception as e:
        logger.error(
            "Не вдалося ініціалізувати дисплей (перевір SPI/піни в /etc/starlink-monitor/env): %s", e
        )
        return

    logger.info("Дисплей ініціалізовано (%dx%d, поворот %d°)",
                config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT, config.DISPLAY_ROTATION)
    font_big = _load_font(26)
    font_small = _load_font(20)

    try:
        db.init_db()
    except Exception as e:
        logger.warning("Не вдалося ініціалізувати БД: %s", e)

    button_get_value = None
    button_release = None
    button_tracker = None
    button_pin = config.SHUTDOWN_BUTTON_GPIO_PIN
    if button_pin and button_pin > 0:
        try:
            button_get_value, button_release = gpio_utils.open_input_line(
                button_pin, "starlink-display-button"
            )
            button_tracker = gpio_utils.ButtonPressTracker(config.SHUTDOWN_BUTTON_HOLD_SEC)
            logger.info("Слухаю кнопку на GPIO%d (коротке=підсвітка, довге=вимкнення Pi)", button_pin)
        except Exception as e:
            logger.error("Не вдалося ініціалізувати кнопку GPIO%d: %s", button_pin, e)

    backlight_on = True
    last_activity_ts = time.time()
    last_redraw = 0.0

    try:
        while True:
            if stop_event and stop_event.is_set():
                return

            now = time.time()

            if button_get_value and button_tracker:
                try:
                    value = button_get_value()
                    event = button_tracker.poll(value)
                    if event == "short_press":
                        backlight_on = not backlight_on
                        _set_backlight(bl_pin, backlight_on)
                        if backlight_on:
                            last_activity_ts = now
                        logger.info("Підсвітка %s (коротке натискання GPIO%d)",
                                    "увімкнена" if backlight_on else "вимкнена", button_pin)
                    elif event == "long_press":
                        _trigger_shutdown(button_pin)
                except Exception as e:
                    logger.warning("Помилка читання кнопки: %s", e)

            if _should_auto_off(backlight_on, last_activity_ts, now, config.DISPLAY_BACKLIGHT_AUTO_OFF_SEC):
                backlight_on = False
                _set_backlight(bl_pin, False)
                logger.info("Підсвітка вимкнена автоматично (%dс після ввімкнення)",
                            config.DISPLAY_BACKLIGHT_AUTO_OFF_SEC)

            if now - last_redraw >= config.DISPLAY_REFRESH_SEC:
                try:
                    _redraw(display, Image, ImageDraw, font_big, font_small)
                except Exception:
                    logger.exception("Помилка оновлення дисплея")
                last_redraw = now

            time.sleep(BUTTON_POLL_INTERVAL_SEC)
    finally:
        if button_release:
            try:
                button_release()
            except Exception:
                pass


def main():
    run_forever()


if __name__ == "__main__":
    main()
