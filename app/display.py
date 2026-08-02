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
import threading
import time
from typing import Any, Optional

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

# Компактні переклади update_state спеціально для вузького TFT-екрана
# - НЕ ті самі, що в static/dashboard.js: там розраховано на широкий
# веб-екран, ці переклади довші за оригінальні internal-коди (напр.
# "GETTING_TARGET_VERSION" 22 символи -> "перевірка наявності
# оновлення" 30 символів). Тут - навпаки, максимально стисло.
DISH_UPDATE_STATE_LABELS = {
    "SOFTWARE_UPDATE_STATE_UNKNOWN": "невідомо",
    "IDLE": "немає",
    "FETCHING": "завантаження",
    "PRE_CHECK": "перевірка",
    "WRITING": "встановлення",
    "POST_CHECK": "перевірка",
    "REBOOT_REQUIRED": "рестарт",
    "DISABLED": "вимкнено",
    "FAULTED": "помилка",
}
ROUTER_UPDATE_STATE_LABELS = {
    "NOT_RUN": "немає",
    "GETTING_TARGET_VERSION": "перевірка",
    "DOWNLOADING_UPDATE_IMAGE": "завантаження",
    "FLASHING": "встановлення",
    "NO_UPDATE_REQUIRED": "непотрібне",
    "REBOOT_PENDING": "рестарт",
    "GETTING_TARGET_VERSION_FAILED": "помилка",
    "GETTING_TARGET_VERSION_EXHAUSTED": "помилка",
    "NO_VALID_ARTIFACT": "помилка",
    "ILLEGAL_ARTIFACT": "помилка",
    # DOWNLOADING_UPDATE_IMAGE_FAILED свідомо відсутній - той самий
    # стан приховується і на веб-дашборді (static/dashboard.js) як
    # частина нормального циклу перевірки, не справжня помилка.
    "DOWNLOADING_UPDATE_IMAGE_EXHAUSTED": "помилка",
    "FLASHING_FAILED": "помилка",
}


def _fmt_uptime(uptime_s: Optional[float]) -> str:
    if not uptime_s:
        return "—"
    h = int(uptime_s) // 3600
    m = (int(uptime_s) % 3600) // 60
    return f"{h}г {m}хв"


def _status_lines(latest_metric: Optional[dict[str, Any]], router_status: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
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
    lines: list[dict[str, Any]] = [{"kind": "status", "text": status_text}]

    dish_update_state = latest_metric.get("update_state")
    if dish_update_state:
        pct = latest_metric.get("update_progress_pct") or 0
        label = DISH_UPDATE_STATE_LABELS.get(dish_update_state, dish_update_state)
        lines.append({
            "kind": "update",
            "text": f"Оновл.Т: {label} {pct:.0f}%",
            "progress": pct,
        })

    router_update_state = router_status.get("update_state") if router_status else None
    # Той самий стан приховується і на веб-дашборді - частина
    # нормального циклу перевірки роутера, не справжня помилка.
    if router_status is not None and router_update_state and router_update_state != "DOWNLOADING_UPDATE_IMAGE_FAILED":
        pct = router_status.get("update_progress_pct") or 0
        label = ROUTER_UPDATE_STATE_LABELS.get(router_update_state, router_update_state)
        lines.append({
            "kind": "update",
            "text": f"Оновл.Р: {label} {pct:.0f}%",
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


def _update_state_changed(
    prev_dish: Optional[str], prev_router: Optional[str],
    dish: Optional[str], router: Optional[str],
) -> bool:
    """Чи змінився update_state dish/router ВІДНОСНО ПОПЕРЕДНЬОГО
    опитування - чиста функція, легко тестується. prev_*=None означає
    "ще не бачили жодного значення" (перший запуск сервісу) - НЕ
    вважається зміною, інакше кожен старт сервісу спалахував би
    підсвіткою навіть без реальної зміни стану."""
    dish_changed = prev_dish is not None and dish != prev_dish
    router_changed = prev_router is not None and router != prev_router
    return dish_changed or router_changed


def _load_font(size: int) -> Any:
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


def _truncate_to_width(draw: Any, text: str, font: Any, max_width: int) -> str:
    """Обрізає текст з '…' в кінці, якщо він не влазить у max_width
    (px). Версії прошивок можуть бути довгими рядками, що фізично не
    вміщаються на вузькому (170px) екрані - без цього текст просто
    продовжувався б за межі видимої області."""
    if draw.textlength(text, font=font) <= max_width:
        return text
    while text and draw.textlength(text + "…", font=font) > max_width:
        text = text[:-1]
    return text + "…" if text else "…"


def _set_backlight(bl_pin: Any, value: bool) -> None:
    """Adafruit CircuitPython ST7789 не має вбудованого set_backlight() -
    BL керується напряму через цей окремий digitalio-пін. bl_pin=None
    (DISPLAY_BL_PIN=0, підсвітка на 3.3V напряму) - нічого не робимо."""
    if bl_pin is not None:
        bl_pin.value = value


def _redraw(display: Any, Image: Any, ImageDraw: Any, font_status: Any, font_update: Any, font_tiny: Any) -> None:
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
            font, color, line_height = font_status, ("lime" if online else "red"), 23
        elif kind == "update":
            font, color, line_height = font_update, "white", 19
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


def run_forever(stop_event: Optional[threading.Event] = None) -> None:
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
    font_status = _load_font(18)
    font_update = _load_font(15)
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
    # Відстеження зміни update_state (dish/router) для flash-сповіщення
    # підсвіткою. None на старті - перше зчитування лише ЗАПАМ'ЯТОВУЄ
    # стан, не вважається "зміною" (інакше кожен запуск сервісу
    # спалахував би підсвіткою, навіть якщо реальних змін не було).
    prev_dish_state: Optional[str] = None
    prev_router_state: Optional[str] = None
    flash_until_ts: Optional[float] = None

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
                        flash_until_ts = None  # ручна дія user - не форсувати вимкнення flash-таймером
                        logger.info("Підсвітка %s (коротке натискання GPIO%d)",
                                    "увімкнена" if backlight_on else "вимкнена", button_pin)
                    elif event == "long_press":
                        _trigger_shutdown(button_pin)
                except Exception as e:
                    logger.warning("Помилка читання кнопки: %s", e)

            if flash_until_ts is None and _should_auto_off(
                backlight_on, last_activity_ts, now, config.DISPLAY_BACKLIGHT_AUTO_OFF_SEC
            ):
                backlight_on = False
                _set_backlight(bl_pin, False)
                logger.info("Підсвітка вимкнена автоматично (%dс після ввімкнення)",
                            config.DISPLAY_BACKLIGHT_AUTO_OFF_SEC)

            # Явне вимкнення ПІСЛЯ flash-періоду - окремо від звичайного
            # auto-off (інший, зазвичай коротший, часовий проміжок).
            if flash_until_ts is not None and now >= flash_until_ts:
                backlight_on = False
                _set_backlight(bl_pin, False)
                flash_until_ts = None
                logger.info("Підсвітка вимкнена після flash-сповіщення про зміну статусу оновлення")

            if now - last_redraw >= config.DISPLAY_REFRESH_SEC:
                try:
                    latest = db.get_latest_metric()
                    router_status = db.get_router_status()
                    dish_state = latest.get("update_state") if latest else None
                    router_state = router_status.get("update_state") if router_status else None

                    # Зміна ВІДНОСНО ПОПЕРЕДНЬОГО опитування (не з
                    # моменту старту сервісу) - _update_state_changed()
                    # сама обробляє "перше зчитування ще не зміна".
                    state_changed = _update_state_changed(prev_dish_state, prev_router_state, dish_state, router_state)
                    if state_changed and config.DISPLAY_UPDATE_FLASH_SEC > 0:
                        backlight_on = True
                        _set_backlight(bl_pin, True)
                        last_activity_ts = now  # інакше _should_auto_off() (60с) міг би спрацювати РАНІШЕ за коротший flash-таймер
                        flash_until_ts = now + config.DISPLAY_UPDATE_FLASH_SEC
                        logger.info(
                            "Статус оновлення змінився (dish: %s->%s, router: %s->%s) - "
                            "підсвітка на %dс",
                            prev_dish_state, dish_state, prev_router_state, router_state,
                            config.DISPLAY_UPDATE_FLASH_SEC,
                        )
                    prev_dish_state, prev_router_state = dish_state, router_state

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


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
