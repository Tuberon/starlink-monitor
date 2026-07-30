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


def _status_lines(latest_metric: dict, router_status: dict = None) -> list:
    """Формує структуровані рядки для відображення - чиста функція
    без залежності від самого дисплея, легко тестується окремо.
    Кожен рядок - dict {"kind": ..., "text": ...} (+ "progress" для
    kind="update") - уникає крихких позиційних індексів, бо рядки
    оновлення опційні (плавав би індекс прошивок після них). Свідомо
    НЕ показує downlink/uplink/ping/drop%/obstruction% (замалий
    екран для змістовних числових метрик, це вже є на веб-дашборді)."""
    if not latest_metric:
        return [{"kind": "status", "text": "Немає даних"}]

    online = bool(latest_metric.get("online"))
    status_text = ("● ONLINE" if online else "○ OFFLINE") + \
        f"  Uptime: {_fmt_uptime(latest_metric.get('uptime_s'))}"
    lines = [{"kind": "status", "text": status_text}]

    dish_update_state = latest_metric.get("update_state")
    if dish_update_state:
        pct = latest_metric.get("update_progress_pct") or 0
        lines.append({
            "kind": "update",
            "text": f"Оновлення тарілки: {dish_update_state} {pct:.0f}%",
            "progress": pct,
        })

    router_update_state = router_status.get("update_state") if router_status else None
    if router_update_state:
        pct = router_status.get("update_progress_pct") or 0
        lines.append({
            "kind": "update",
            "text": f"Оновлення роутера: {router_update_state} {pct:.0f}%",
            "progress": pct,
        })

    dish_fw = latest_metric.get("software_version")
    lines.append({"kind": "firmware", "text": f"Тарілка: {dish_fw if dish_fw else '—'}"})

    router_fw = router_status.get("software_version") if router_status else None
    lines.append({"kind": "firmware", "text": f"Роутер: {router_fw if router_fw else '—'}"})

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
    logger.warning(
        "Жоден шрифт з %s не знайдено - fallback на вбудований bitmap-шрифт "
        "PIL (НЕ підтримує кирилицю). Встанови пакет 'fonts-dejavu-core'.",
        FONT_PATHS,
    )
    return ImageFont.load_default()


def _truncate_to_width(draw, text: str, font, max_width: int) -> str:
    """Обрізає текст з '…' в кінці, якщо він не влазить у max_width
    (px). Версії прошивок можуть бути довгими рядками, що фізично не
    вміщаються на вузькому (170px) екрані - без цього текст просто
    продовжувався б за межі видимої області."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…" if text else "…"


def _set_backlight(bl_pin, value: bool):
    """Adafruit CircuitPython ST7789 не має вбудованого set_backlight() -
    BL керується напряму через цей окремий digitalio-пін. bl_pin=None
    (DISPLAY_BL_PIN=0, підсвітка на 3.3V напряму) - нічого не робимо."""
    if bl_pin is not None:
        bl_pin.value = value


def _redraw(display, Image, ImageDraw, font_status, font_update, font_tiny):
    latest = db.get_latest_metric()
    router_status = db.get_router_status()
    lines = _status_lines(latest, router_status)
    online = bool(latest and latest.get("online"))

    # Бібліотека Adafruit застосовує rotation через img.rotate(),
    # ПІСЛЯ чого перевіряє розмір результату проти display.width/
    # display.height (які самі НЕ змінюються параметром rotation).
    # Для 90/270 треба створювати полотно з транспонованими
    # розмірами (height x width) - після повороту воно стане
    # width x height і коректно впишеться в дисплей.
    if config.DISPLAY_ROTATION in (90, 270):
        canvas_size = (display.height, display.width)
    else:
        canvas_size = (display.width, display.height)

    img = Image.new("RGB", canvas_size, "black")
    draw = ImageDraw.Draw(img)
    max_width = canvas_size[0] - 20  # відступи по 10px з кожного боку
    y = 10
    for line in lines:
        kind = line["kind"]
        if kind == "status":
            font, color, line_height = font_status, ("lime" if online else "red"), 26
        elif kind == "update":
            font, color, line_height = font_update, "white", 24
        else:  # "firmware"
            font, color, line_height = font_tiny, "white", 20

        text = _truncate_to_width(draw, line["text"], font, max_width)
        draw.text((10, y), text, font=font, fill=color)
        y += line_height

        if kind == "update":
            bar_h = 8
            draw.rectangle([10, y, 10 + max_width, y + bar_h], outline="white")
            pct = max(0, min(100, line.get("progress", 0) or 0))
            filled = int(max_width * pct / 100)
            if filled > 0:
                draw.rectangle([10, y, 10 + filled, y + bar_h], fill="lime")
            y += bar_h + 8

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
    font_status = _load_font(20)
    font_update = _load_font(19)
    font_tiny = _load_font(16)

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
                    _redraw(display, Image, ImageDraw, font_status, font_update, font_tiny)
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
