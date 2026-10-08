"""Фізичний TFT-дисплей (ST7789, SPI): live-статус Starlink Mini (online/offline, uptime) без веб-дашборда.
Adafruit CircuitPython (adafruit-circuitpython-rgb-display + adafruit-blinka). Особливості: reset()
викликається в конструкторі до SPI-ініціалізації; rotation 0/90/180/270 для будь-якого aspect ratio
програмно (PIL img.rotate()); підсвітка не має set_backlight() — BL-пін керується напряму через
digitalio.DigitalInOut (_set_backlight()). Кнопку виключення Pi (SHUTDOWN_BUTTON_GPIO_PIN) обробляє теж
цей процес, бо лише він тримає BL-пін: коротке натискання перемикає підсвітку, довге
(SHUTDOWN_BUTTON_HOLD_SEC) вимикає Pi; shutdown_button.py при DISPLAY_ENABLED=1 сам завершується, щоб не
конкурувати за GPIO. Окремий процес, періодично перемальовує кадр через Pillow. Вимкнено за
замовчуванням (DISPLAY_ENABLED=0).
"""
import logging
import threading
import time
from typing import Any, Callable, Optional

from app import config, db, gpio_utils, labels, log_redact, pi_power

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log_redact.install()   # токен бота ніколи не потрапляє в журнал (див. app/log_redact.py)
logger = logging.getLogger("display")

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]

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
    # DOWNLOADING_UPDATE_IMAGE_FAILED і GETTING_TARGET_VERSION_FAILED свідомо відсутні: це тимчасові
    # хмарні помилки перевірки оновлення на боці SpaceX, а не проблема моніторингу; ті самі стани
    # приховано й на веб-дашборді (static/dashboard.js).
    "GETTING_TARGET_VERSION_EXHAUSTED": "помилка",
    "NO_VALID_ARTIFACT": "помилка",
    "ILLEGAL_ARTIFACT": "помилка",
    "DOWNLOADING_UPDATE_IMAGE_EXHAUSTED": "помилка",
    "FLASHING_FAILED": "помилка",
}
# Стани, повністю приховані з дисплея (не лише текст мітки, а й сам
# рядок update_state) - "тимчасова хмарна помилка перевірки/
# завантаження оновлення на боці SpaceX", не проблема моніторингу.
HIDDEN_ROUTER_STATES = labels.ROUTER_STATES_SHOWN_AS_NO_UPDATES


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
    if router_status is not None and router_update_state and router_update_state not in HIDDEN_ROUTER_STATES:
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


def _create_canvas(display: Any, Image: Any, ImageDraw: Any) -> tuple[tuple[int, int], Any, Any]:
    """Чорне PIL-полотно під поточний DISPLAY_ROTATION: бібліотека Adafruit застосовує rotation через
    img.rotate() ПІСЛЯ малювання, тож для 90/270 полотно МАЄ бути транспонованим (height x width),
    інакше не впишеться в display.width/height. Спільне для _redraw() і _draw_power_action_message().
    """
    if config.DISPLAY_ROTATION in (90, 270):
        canvas_size = (display.height, display.width)
    else:
        canvas_size = (display.width, display.height)
    img = Image.new("RGB", canvas_size, "black")
    draw = ImageDraw.Draw(img)
    return canvas_size, img, draw


def _redraw(
    display: Any, Image: Any, ImageDraw: Any, font_status: Any, font_update: Any, font_tiny: Any,
    data: Optional[tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]] = None,
    prev_frame: Optional[tuple[Any, ...]] = None,
) -> tuple[Any, ...]:
    """Малює стан на екрані й повертає "кадр" - те, що на ньому видно.
    Якщо кадр той самий, що prev_frame, рендеринг і передача по SPI
    (~109 КБ) пропускаються: uptime показується з точністю до хвилини,
    тож при оновленні кожні 5 с ~11 з 12 перемальовувань були марними,
    а вночі (Starlink вимкнено) екран годинами не змінюється. data -
    (latest, router_status), уже прочитані циклом (без повторного
    читання БД); без data - читає сама (зворотна сумісність)."""
    latest, router_status = data if data is not None else (db.get_latest_metric(), db.get_router_status())
    lines = _status_lines(latest, router_status)
    online = bool(latest and latest.get("online"))
    frame = (online, tuple((ln["kind"], ln["text"], ln.get("progress")) for ln in lines))
    if frame == prev_frame:
        return frame

    canvas_size, img, draw = _create_canvas(display, Image, ImageDraw)
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
    return frame


def _draw_power_action_message(display: Any, Image: Any, ImageDraw: Any, font: Any, action: str) -> None:
    """Повноекранне повідомлення "Вимикається.../Перезавантажується..."
    - викликається ОДИН раз, ПЕРЕД тим, як сам процес дисплея буде
    вбитий через SIGTERM від systemctl reboot/poweroff (див.
    app/pi_power.py: затримка ПЕРЕД реальним викликом команди дає
    час цьому кадру реально відобразитись на екрані)."""
    canvas_size, img, draw = _create_canvas(display, Image, ImageDraw)
    text = "● Вимикається..." if action == "poweroff" else "● Перезавантажується..."
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w, text_h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = max(0, (canvas_size[0] - text_w) // 2)
    y = max(0, (canvas_size[1] - text_h) // 2)
    draw.text((x, y), text, font=font, fill="yellow")
    display.image(img)


def _open_button(pin: int) -> tuple[Optional[Callable[[], int]], Optional[Callable[[], None]], Optional[gpio_utils.ButtonPressTracker]]:
    """Відкриває лінію кнопки: (get_value, release, tracker). Частково
    заповнений стан при збої ЗБЕРЕЖЕНО навмисно: якщо лінію відкрито, а
    трекер створити не вдалось, release() усе одно потрібен у finally."""
    get_value = release = tracker = None
    if pin and pin > 0:
        try:
            get_value, release = gpio_utils.open_input_line(pin, "starlink-display-button")
            tracker = gpio_utils.ButtonPressTracker(config.SHUTDOWN_BUTTON_HOLD_SEC)
            logger.info("Слухаю кнопку на GPIO%d (коротке=підсвітка, довге=вимкнення Pi)", pin)
        except Exception as e:
            logger.error("Не вдалося ініціалізувати кнопку GPIO%d: %s", pin, e)
    return get_value, release, tracker


class DisplayController:
    """Машина станів дисплея: підсвітка (кнопка, автовимкнення, flash при зміні стану оновлення) і
    перемальовування кадру; одна ітерація — `tick()`. Винесено з run_forever() (складність 34), щоб
    логіка тестувалась без скриптованого годинника й фейкового заліза: run_forever() лише ініціалізує
    залізо й крутить цикл, а `tick()` виконує рівно одну ітерацію в тому ж порядку. Допоміжні функції
    (`_set_backlight`, `_redraw`, ...) викликаються як глобальні імена модуля, тож патч-цілі в тестах
    чинні.
    """

    def __init__(
        self,
        *,
        display: Any,
        image_cls: Any,
        draw_cls: Any,
        fonts: tuple[Any, Any, Any],
        bl_pin: Any,
        button_get_value: Optional[Callable[[], int]],
        button_tracker: Optional[gpio_utils.ButtonPressTracker],
        button_pin: int,
    ) -> None:
        self._display = display
        self._image_cls = image_cls
        self._draw_cls = draw_cls
        self._font_status, self._font_update, self._font_tiny = fonts
        self._bl_pin = bl_pin
        self._button_get_value = button_get_value
        self._button_tracker = button_tracker
        self._button_pin = button_pin
        self.backlight_on = True
        self.last_activity_ts = time.time()
        self.last_redraw = 0.0
        # Відстеження зміни update_state (dish/router) для flash-сповіщення
        # підсвіткою. None на старті - перше зчитування лише ЗАПАМ'ЯТОВУЄ
        # стан, не вважається "зміною" (інакше кожен запуск сервісу
        # спалахував би підсвіткою, навіть якщо реальних змін не було).
        self.prev_dish_state: Optional[str] = None
        self.prev_router_state: Optional[str] = None
        self.frame: Optional[tuple[Any, ...]] = None
        self.flash_until_ts: Optional[float] = None

    def tick(self) -> bool:
        """Одна ітерація циклу. True - дисплею пора завершити роботу (показано
        повідомлення про reboot/poweroff)."""
        if self._show_pending_power_action():
            return True
        now = time.time()
        self._poll_button(now)
        self._apply_backlight_timers(now)
        if now - self.last_redraw >= config.DISPLAY_REFRESH_SEC:
            self._refresh(now)
        return False

    def _show_pending_power_action(self) -> bool:
        # Перевіряємо ЩОРАЗУ (швидкий ~100мс цикл, не звичайний
        # 5-секундний REFRESH_SEC) - reboot/poweroff від pi_power.py
        # чекає лише DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC (типово 2с)
        # перед реальним systemctl-викликом, тому потрібно виявити
        # сигнал майже одразу, а не з затримкою до 5с.
        try:
            pending_action = db.get_setting(pi_power.PENDING_ACTION_SETTING_KEY)
        except Exception:
            pending_action = None
        if pending_action not in ("reboot", "poweroff"):
            return False
        try:
            _draw_power_action_message(self._display, self._image_cls, self._draw_cls, self._font_status, pending_action)
        except Exception:
            logger.exception("Не вдалося намалювати повідомлення про %s", pending_action)
        return True

    def _poll_button(self, now: float) -> None:
        if not (self._button_get_value and self._button_tracker):
            return
        try:
            value = self._button_get_value()
            event = self._button_tracker.poll(value)
            if event == "short_press":
                self.backlight_on = not self.backlight_on
                _set_backlight(self._bl_pin, self.backlight_on)
                if self.backlight_on:
                    self.last_activity_ts = now
                self.flash_until_ts = None  # ручна дія user - не форсувати вимкнення flash-таймером
                logger.info("Підсвітка %s (коротке натискання GPIO%d)",
                            "увімкнена" if self.backlight_on else "вимкнена", self._button_pin)
            elif event == "long_press":
                pi_power.shutdown_from_button(self._button_pin)
        except Exception as e:
            logger.warning("Помилка читання кнопки: %s", e)

    def _apply_backlight_timers(self, now: float) -> None:
        if self.flash_until_ts is None and _should_auto_off(
            self.backlight_on, self.last_activity_ts, now, config.DISPLAY_BACKLIGHT_AUTO_OFF_SEC
        ):
            self.backlight_on = False
            _set_backlight(self._bl_pin, False)
            logger.info("Підсвітка вимкнена автоматично (%dс після ввімкнення)",
                        config.DISPLAY_BACKLIGHT_AUTO_OFF_SEC)

        # Явне вимкнення ПІСЛЯ flash-періоду - окремо від звичайного
        # auto-off (інший, зазвичай коротший, часовий проміжок).
        if self.flash_until_ts is not None and now >= self.flash_until_ts:
            self.backlight_on = False
            _set_backlight(self._bl_pin, False)
            self.flash_until_ts = None
            logger.info("Підсвітка вимкнена після flash-сповіщення про зміну статусу оновлення")

    def _refresh(self, now: float) -> None:
        try:
            latest = db.get_latest_metric()
            router_status = db.get_router_status()
            dish_state = latest.get("update_state") if latest else None
            router_state = router_status.get("update_state") if router_status else None

            # Зміна ВІДНОСНО ПОПЕРЕДНЬОГО опитування (не з
            # моменту старту сервісу) - _update_state_changed()
            # сама обробляє "перше зчитування ще не зміна".
            state_changed = _update_state_changed(self.prev_dish_state, self.prev_router_state, dish_state, router_state)
            if state_changed and config.DISPLAY_UPDATE_FLASH_SEC > 0:
                self.backlight_on = True
                _set_backlight(self._bl_pin, True)
                self.last_activity_ts = now  # інакше _should_auto_off() (60с) міг би спрацювати РАНІШЕ за коротший flash-таймер
                self.flash_until_ts = now + config.DISPLAY_UPDATE_FLASH_SEC
                logger.info(
                    "Статус оновлення змінився (dish: %s->%s, router: %s->%s) - "
                    "підсвітка на %dс",
                    self.prev_dish_state, dish_state, self.prev_router_state, router_state,
                    config.DISPLAY_UPDATE_FLASH_SEC,
                )
            self.prev_dish_state, self.prev_router_state = dish_state, router_state

            self.frame = _redraw(self._display, self._image_cls, self._draw_cls,
                                 self._font_status, self._font_update, self._font_tiny,
                                 data=(latest, router_status), prev_frame=self.frame)
        except Exception:
            logger.exception("Помилка оновлення дисплея")
        self.last_redraw = now


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

    button_pin = config.SHUTDOWN_BUTTON_GPIO_PIN
    button_get_value, button_release, button_tracker = _open_button(button_pin)

    controller = DisplayController(
        display=display, image_cls=Image, draw_cls=ImageDraw,
        fonts=(font_status, font_update, font_tiny), bl_pin=bl_pin,
        button_get_value=button_get_value, button_tracker=button_tracker, button_pin=button_pin,
    )

    try:
        while True:
            if stop_event and stop_event.is_set():
                return
            if controller.tick():
                return
            time.sleep(config.DISPLAY_BUTTON_POLL_INTERVAL_SEC)
    finally:
        if button_release:
            try:
                button_release()
            except Exception as e:
                # Не перекриваємо оригінальну причину завершення функції
                # (напр. коректний SIGTERM) - лише debug-слід на випадок
                # рідкісного edge-case (напр. баг у gpiod-бібліотеці),
                # без підняття рівня логування до warning/error.
                logger.debug("Не вдалося звільнити GPIO кнопки при завершенні: %s", e)


def main() -> None:
    run_forever()


if __name__ == "__main__":
    main()
