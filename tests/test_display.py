"""
Тести для чистих функцій app/display.py - виявлення зміни update_state
(flash-сповіщення підсвіткою) та auto-off логіки. Реальний GPIO/SPI-
дисплей не тестується (потребує фізичного заліза) - лише чиста,
детерміністична логіка, винесена саме для тестованості.
"""
from app.display import HIDDEN_ROUTER_STATES, _fmt_uptime, _should_auto_off, _status_lines, _update_state_changed


def _dish_metric(**overrides):
    base = dict(
        online=True, uptime_s=100, software_version="v1", hardware_version="rev3",
        dish_id="d1", downlink_mbps=100.0, uplink_mbps=10.0, ping_latency_ms=30.0,
        ping_drop_ratio=0.0, obstruction_fraction=0.0, update_state="IDLE",
        update_progress_pct=0.0, active_alerts="[]", state="OKAY",
    )
    base.update(overrides)
    return base


def _router_status(**overrides):
    base = dict(
        online=True, software_version="v1", hardware_version="rev2",
        update_state="FLASHING", update_progress_pct=0.0,
        active_alerts="[]", clients="[]",
    )
    base.update(overrides)
    return base


# ---- _fmt_uptime ----

def test_fmt_uptime_none_returns_dash():
    assert _fmt_uptime(None) == "—"


def test_fmt_uptime_zero_returns_dash():
    assert _fmt_uptime(0) == "—"


def test_fmt_uptime_formats_hours_and_minutes():
    assert _fmt_uptime(3661) == "1г 1хв"


# ---- _status_lines - немає даних ----

def test_status_lines_no_metric_shows_no_data():
    assert _status_lines(None) == [{"kind": "status", "text": "Немає даних"}]


# ---- _update_state_changed ----

def test_first_reading_is_not_a_change():
    """prev=None (перший запуск сервісу) - НЕ вважається зміною,
    інакше кожен старт сервісу спалахував би підсвіткою даремно."""
    assert _update_state_changed(None, None, "DOWNLOADING", "IDLE") is False


def test_dish_state_change_detected():
    assert _update_state_changed("IDLE", "IDLE", "DOWNLOADING", "IDLE") is True


def test_router_state_change_detected():
    assert _update_state_changed("IDLE", "IDLE", "IDLE", "DOWNLOADING") is True


def test_both_changed_detected():
    assert _update_state_changed("IDLE", "IDLE", "DOWNLOADING", "DOWNLOADING") is True


def test_no_change_not_detected():
    assert _update_state_changed("IDLE", "IDLE", "IDLE", "IDLE") is False


def test_state_disappearing_is_a_change():
    """IDLE -> None (напр. router став недоступний) - теж зміна."""
    assert _update_state_changed("IDLE", None, None, None) is True


# ---- _should_auto_off (взаємодія з flash - last_activity_ts) ----

def test_auto_off_does_not_trigger_immediately_after_flash_activation():
    """Коли flash щойно активувався, last_activity_ts=now - auto-off
    (типово 60с) не мав би спрацювати одразу на тій самій ітерації."""
    now = 1000.0
    assert _should_auto_off(True, now, now, 60) is False


def test_auto_off_triggers_after_timeout():
    now = 1000.0
    assert _should_auto_off(True, now - 61, now, 60) is True


def test_auto_off_disabled_when_timeout_zero():
    now = 1000.0
    assert _should_auto_off(True, now - 1000, now, 0) is False


# ---- HIDDEN_ROUTER_STATES / _status_lines - "тимчасова хмарна помилка" не показується ----

def test_getting_target_version_failed_fully_hidden():
    """GETTING_TARGET_VERSION_FAILED - навмисно прихований стан (той
    самий підхід, що вже застосований до DOWNLOADING_UPDATE_IMAGE_
    FAILED) - "тимчасова хмарна помилка перевірки оновлення на боці
    SpaceX", не проблема моніторингу. Рядок про router-оновлення НЕ
    з'являється взагалі, не лише текст замінюється."""
    lines = _status_lines(_dish_metric(), _router_status(update_state="GETTING_TARGET_VERSION_FAILED"))
    update_lines = [l for l in lines if l["kind"] == "update" and "Оновл.Р" in l["text"]]
    assert update_lines == []


def test_downloading_update_image_failed_still_hidden():
    """Контрольний тест: раніше вже прихований стан не зачепило."""
    lines = _status_lines(_dish_metric(), _router_status(update_state="DOWNLOADING_UPDATE_IMAGE_FAILED"))
    update_lines = [l for l in lines if l["kind"] == "update" and "Оновл.Р" in l["text"]]
    assert update_lines == []


def test_other_router_states_remain_visible():
    """Контрольний тест: фікс приховує ЛИШЕ ці 2 конкретні стани, не
    всі router-стани взагалі."""
    lines = _status_lines(_dish_metric(), _router_status(update_state="FLASHING"))
    update_lines = [l for l in lines if l["kind"] == "update" and "Оновл.Р" in l["text"]]
    assert len(update_lines) == 1
    assert "встановлення" in update_lines[0]["text"]


def test_hidden_router_states_contains_exactly_two_states():
    assert set(HIDDEN_ROUTER_STATES) == {"DOWNLOADING_UPDATE_IMAGE_FAILED", "GETTING_TARGET_VERSION_FAILED"}


# ---- _draw_power_action_message() - повідомлення при reboot/poweroff ----

class _FakeDisplay:
    """Мінімальний mock реального ST7789-об'єкта - лише width/height
    (потрібні для розрахунку canvas) і image() (перевіряємо виклик)."""
    width, height = 320, 170

    def __init__(self):
        self.last_image = None

    def image(self, img):
        self.last_image = img


def _real_font():
    from PIL import ImageFont
    return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)


def test_draw_power_action_message_poweroff_renders_without_error():
    from PIL import Image, ImageDraw
    from app.display import _draw_power_action_message

    disp = _FakeDisplay()
    _draw_power_action_message(disp, Image, ImageDraw, _real_font(), "poweroff")
    assert disp.last_image is not None


def test_draw_power_action_message_reboot_renders_without_error():
    from PIL import Image, ImageDraw
    from app.display import _draw_power_action_message

    disp = _FakeDisplay()
    _draw_power_action_message(disp, Image, ImageDraw, _real_font(), "reboot")
    assert disp.last_image is not None


def test_draw_power_action_message_uses_only_verified_glyphs():
    """Реальна регресія, знайдена живим тестом під час реалізації:
    emoji-символи (⏻, 🔁) виглядали як порожні "тофу"-квадрати на
    DejaVu-шрифті, хоча автоматизована getbbox()-перевірка хибно
    показувала "гліф є". Перевіряємо, що використовується безпечний,
    перевірений символ (●), не ці конкретні emoji."""
    import inspect
    from app import display
    source = inspect.getsource(display._draw_power_action_message)
    assert "⏻" not in source, "цей emoji не відображається коректно на DejaVu-шрифті"
    assert "🔁" not in source, "цей emoji не відображається коректно на DejaVu-шрифті"
    assert "●" in source


def test_draw_power_action_message_respects_rotation():
    """Той самий підхід, що _redraw(): для rotation 90/270 canvas
    МАЄ бути транспонований (height x width), інакше зображення не
    впишеться в дисплей після повороту бібліотекою Adafruit."""
    from PIL import Image, ImageDraw
    from app import config, display

    disp = _FakeDisplay()
    old_rotation = config.DISPLAY_ROTATION
    try:
        config.DISPLAY_ROTATION = 90
        display._draw_power_action_message(disp, Image, ImageDraw, _real_font(), "poweroff")
        assert disp.last_image.size == (disp.height, disp.width)

        config.DISPLAY_ROTATION = 0
        display._draw_power_action_message(disp, Image, ImageDraw, _real_font(), "poweroff")
        assert disp.last_image.size == (disp.width, disp.height)
    finally:
        config.DISPLAY_ROTATION = old_rotation


# ---- _load_font() ----

def test_load_font_finds_real_dejavu_font():
    from app.display import _load_font
    font = _load_font(18)
    # Реальний PIL.ImageFont.FreeTypeFont, не bitmap-fallback
    assert type(font).__name__ == "FreeTypeFont"


def test_load_font_falls_back_when_no_font_found(caplog):
    """Реальний edge case: жоден шлях з FONT_PATHS не існує (напр.
    fonts-dejavu-core не встановлений) - МАЄ fallback на вбудований
    PIL bitmap-шрифт, не кидати виняток."""
    from app import display
    old_paths = display.FONT_PATHS
    try:
        display.FONT_PATHS = ["/nonexistent/font1.ttf", "/nonexistent/font2.ttf"]
        with caplog.at_level("WARNING"):
            font = display._load_font(18)
        assert "fonts-dejavu-core" in caplog.text
        assert font is not None
    finally:
        display.FONT_PATHS = old_paths


# ---- _truncate_to_width() ----

def test_truncate_to_width_short_text_unchanged():
    from PIL import Image, ImageDraw
    from app.display import _truncate_to_width
    draw = ImageDraw.Draw(Image.new("RGB", (170, 50)))
    font = _real_font()
    assert _truncate_to_width(draw, "v1.0", font, 170) == "v1.0"


def test_truncate_to_width_long_text_gets_ellipsis():
    from PIL import Image, ImageDraw
    from app.display import _truncate_to_width
    draw = ImageDraw.Draw(Image.new("RGB", (170, 50)))
    font = _real_font()
    long_text = "2026.09.10.mr99999.99-very-long-firmware-version-string"
    result = _truncate_to_width(draw, long_text, font, 100)
    assert result.endswith("…")
    assert draw.textlength(result, font=font) <= 100


def test_truncate_to_width_extremely_narrow_returns_just_ellipsis():
    """Реальний edge case: max_width настільки малий, що навіть один
    символ + '…' не влазить - МАЄ повернути хоча б '…', не порожній
    рядок і не нескінченний цикл."""
    from PIL import Image, ImageDraw
    from app.display import _truncate_to_width
    draw = ImageDraw.Draw(Image.new("RGB", (170, 50)))
    font = _real_font()
    result = _truncate_to_width(draw, "щось довге", font, 1)
    assert result == "…"


# ---- _set_backlight() ----

def test_set_backlight_none_pin_does_nothing():
    from app.display import _set_backlight
    _set_backlight(None, True)  # не мало кинути виняток


def test_set_backlight_sets_pin_value():
    from app.display import _set_backlight

    class FakePin:
        value = None

    pin = FakePin()
    _set_backlight(pin, True)
    assert pin.value is True
    _set_backlight(pin, False)
    assert pin.value is False


# ---- _redraw() - реальний PIL-рендеринг + реальна БД (db_path fixture) ----

def test_redraw_no_data_shows_message(db_path):
    from PIL import Image, ImageDraw
    from app.display import _redraw
    disp = _FakeDisplay()
    font = _real_font()
    _redraw(disp, Image, ImageDraw, font, font, font)
    assert disp.last_image is not None  # реально намалював щось (не впав на None-даних)


def test_redraw_online_dish_renders(db_path):
    from PIL import Image, ImageDraw
    from app import db
    from app.display import _redraw
    from app.starlink_client import DishStatus

    db.insert_metric(DishStatus(
        timestamp=1000.0, online=True, uptime_s=3661, software_version="v1.0",
    ).to_dict())

    disp = _FakeDisplay()
    font = _real_font()
    _redraw(disp, Image, ImageDraw, font, font, font)
    assert disp.last_image is not None


def test_redraw_respects_rotation(db_path):
    """Той самий транспонований-canvas принцип, що вже перевірено
    для _draw_power_action_message() - _redraw() МАЄ ту саму логіку
    незалежно (окремий код-шлях, не спільна функція)."""
    from PIL import Image, ImageDraw
    from app import config
    from app.display import _redraw
    disp = _FakeDisplay()
    font = _real_font()
    old_rotation = config.DISPLAY_ROTATION
    try:
        config.DISPLAY_ROTATION = 90
        _redraw(disp, Image, ImageDraw, font, font, font)
        assert disp.last_image.size == (disp.height, disp.width)

        config.DISPLAY_ROTATION = 0
        _redraw(disp, Image, ImageDraw, font, font, font)
        assert disp.last_image.size == (disp.width, disp.height)
    finally:
        config.DISPLAY_ROTATION = old_rotation


def test_redraw_update_progress_bar_renders(db_path):
    """Реальний сценарій: router у процесі оновлення ПЗ - progress-bar
    (окрема, умовна гілка коду для kind == "update") МАЄ реально
    намалюватись без винятку."""
    from PIL import Image, ImageDraw
    from app import db
    from app.display import _redraw
    from app.starlink_client import DishStatus, RouterInfo

    db.insert_metric(DishStatus(timestamp=1000.0, online=True).to_dict())
    db.set_router_status(RouterInfo(
        timestamp=1000.0, online=True, update_state="DOWNLOADING",
        update_progress_pct=45.0,
    ).to_dict())

    disp = _FakeDisplay()
    font = _real_font()
    _redraw(disp, Image, ImageDraw, font, font, font)
    assert disp.last_image is not None
