"""Тести app/display.py: чисті функції (update_state/auto-off) і реальний PIL-рендеринг (_redraw,
_draw_power_action_message, _load_font, _truncate_to_width) через fake display. Цикл run_forever()
(SPI/GPIO через fake CircuitPython) — у tests/test_display_run_forever.py.
"""
from unittest.mock import patch

import pytest

from app import config, display
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
    """GETTING_TARGET_VERSION_FAILED — навмисно прихований стан (як DOWNLOADING_UPDATE_IMAGE_FAILED):
    тимчасова хмарна помилка SpaceX, не проблема моніторингу. Рядок про router-оновлення НЕ з'являється
    взагалі, а не лише змінюється текст.
    """
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
    """Регресія: emoji (⏻, 🔁) на DejaVu — порожні "тофу"-квадрати, хоча автоматична getbbox()-перевірка
    хибно показувала "гліф є". Перевіряється, що використовується безпечний символ (●).
    """
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


# ---- _redraw(): пропуск незмінного кадру й дані без повторного читання БД ----

class _CountingDisplay(_FakeDisplay):
    def __init__(self):
        super().__init__()
        self.pushes = 0

    def image(self, img):
        self.pushes += 1
        super().image(img)


def _online(uptime_s):
    return ({"online": 1, "uptime_s": uptime_s, "update_state": "IDLE", "software_version": "v1"}, None)


def test_redraw_skips_unchanged_frame():
    """Той самий текст (uptime у межах тієї ж хвилини) - без рендерингу
    й передачі по SPI; зміна хвилини - перемальовується."""
    from PIL import Image, ImageDraw
    from app.display import _redraw
    disp, font = _CountingDisplay(), _real_font()
    frame = _redraw(disp, Image, ImageDraw, font, font, font, data=_online(3600), prev_frame=None)
    frame = _redraw(disp, Image, ImageDraw, font, font, font, data=_online(3630), prev_frame=frame)
    assert disp.pushes == 1
    _redraw(disp, Image, ImageDraw, font, font, font, data=_online(3660), prev_frame=frame)
    assert disp.pushes == 2


def test_redraw_status_change_forces_redraw():
    from PIL import Image, ImageDraw
    from app.display import _redraw
    disp, font = _CountingDisplay(), _real_font()
    frame = _redraw(disp, Image, ImageDraw, font, font, font, data=_online(3600))
    _redraw(disp, Image, ImageDraw, font, font, font, data=({"online": 0, "uptime_s": 3600}, None), prev_frame=frame)
    assert disp.pushes == 2


def test_redraw_with_data_does_not_read_db():
    """Цикл уже прочитав дані - _redraw() не читає БД повторно."""
    from unittest.mock import patch
    from PIL import Image, ImageDraw
    from app.display import _redraw
    with patch("app.db.get_latest_metric", side_effect=AssertionError("повторне читання БД")), \
         patch("app.db.get_router_status", side_effect=AssertionError("повторне читання БД")):
        _redraw(_CountingDisplay(), Image, ImageDraw, _real_font(), _real_font(), _real_font(), data=_online(60))


def test_display_and_button_processes_do_not_load_http_stack():
    """pi_power імпортує telegram_notify лише в момент сповіщення:
    процеси дисплея й кнопки не тримають requests у пам'яті."""
    import os
    import subprocess
    import sys
    code = "import sys, app.display, app.shutdown_button; print('requests' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, encoding="utf-8", errors="replace",
                         cwd=os.path.join(os.path.dirname(__file__), ".."))
    assert out.stdout.strip() == "False", out.stdout + out.stderr


# ---- DisplayController.tick(): логіка підсвітки/кнопки/перемальовування БЕЗ заліза і БЕЗ циклу ----

class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class _ScriptedButton:
    """get_value() дає значення; tracker.poll() віддає наступну подію зі сценарію."""
    def __init__(self, events):
        self.events = list(events)

    def get_value(self):
        return 0

    def poll(self, value):
        return self.events.pop(0) if self.events else None


def _controller(button_events=None, bl_pin="BL"):
    button = _ScriptedButton(button_events) if button_events is not None else None
    return display.DisplayController(
        display=object(), image_cls=object, draw_cls=object, fonts=(None, None, None), bl_pin=bl_pin,
        button_get_value=button.get_value if button else None, button_tracker=button if button else None, button_pin=27,
    )


@pytest.fixture
def ctl_env(db_path):
    """Годинник, запис підсвітки й перемальовувань - без жодного заліза."""
    clock = _Clock()
    backlight, redraws = [], []
    config.DISPLAY_REFRESH_SEC = 5
    config.DISPLAY_BACKLIGHT_AUTO_OFF_SEC = 60
    config.DISPLAY_UPDATE_FLASH_SEC = 30
    with patch("time.time", side_effect=clock), \
         patch("app.display._set_backlight", side_effect=lambda pin, v: backlight.append((clock.t, v))), \
         patch("app.display._redraw", side_effect=lambda *a, **kw: redraws.append(kw["data"]) or ("кадр", len(redraws))):
        yield clock, backlight, redraws


def test_tick_redraws_only_after_refresh_interval(ctl_env):
    clock, _, redraws = ctl_env
    ctl = _controller()
    assert ctl.tick() is False and len(redraws) == 1            # last_redraw=0 -> перший кадр одразу
    clock.t += 3
    ctl.tick()
    assert len(redraws) == 1                                     # ще рано
    clock.t += 3
    ctl.tick()
    assert len(redraws) == 2                                     # минуло >= 5 с


def test_tick_short_press_toggles_backlight_and_cancels_flash_timer(ctl_env):
    clock, backlight, _ = ctl_env
    ctl = _controller(button_events=["short_press", "short_press"])
    ctl.flash_until_ts = clock.t + 30                            # активний flash
    ctl.tick()
    assert backlight == [(1000.0, False)] and ctl.backlight_on is False
    assert ctl.flash_until_ts is None                            # ручна дія скасовує flash-таймер
    clock.t += 1
    ctl.tick()
    assert backlight[-1] == (1001.0, True) and ctl.last_activity_ts == 1001.0


def test_tick_long_press_triggers_shutdown_with_the_pin(ctl_env):
    ctl = _controller(button_events=["long_press"])
    with patch("app.pi_power.shutdown_from_button") as shutdown:
        ctl.tick()
    shutdown.assert_called_once_with(27)


def test_tick_button_read_error_is_contained(ctl_env):
    class Broken(_ScriptedButton):
        def get_value(self):
            raise OSError("gpiod зламався")
    broken = Broken([])
    ctl = display.DisplayController(display=object(), image_cls=object, draw_cls=object, fonts=(None, None, None), bl_pin="BL",
                                    button_get_value=broken.get_value, button_tracker=broken, button_pin=27)
    assert ctl.tick() is False                                   # не кидає, цикл триває


def test_tick_auto_off_after_timeout(ctl_env):
    clock, backlight, _ = ctl_env
    ctl = _controller()
    ctl.tick()
    clock.t += 61
    ctl.tick()
    assert backlight == [(1061.0, False)] and ctl.backlight_on is False


def test_tick_flash_on_state_change_then_off_and_not_on_first_reading(ctl_env):
    clock, backlight, _ = ctl_env
    ctl = _controller()
    with patch("app.display.db.get_latest_metric", return_value={"update_state": "IDLE"}), \
         patch("app.display.db.get_router_status", return_value={"update_state": "IDLE"}):
        ctl.tick()                                               # перше зчитування - не зміна
    assert backlight == [] and ctl.flash_until_ts is None
    clock.t += 6
    with patch("app.display.db.get_latest_metric", return_value={"update_state": "FETCHING"}), \
         patch("app.display.db.get_router_status", return_value={"update_state": "IDLE"}):
        ctl.tick()
    assert backlight == [(1006.0, True)] and ctl.flash_until_ts == 1036.0
    clock.t += 31
    with patch("app.display.db.get_latest_metric", return_value={"update_state": "FETCHING"}), \
         patch("app.display.db.get_router_status", return_value={"update_state": "IDLE"}):
        ctl.tick()
    assert backlight[-1] == (1037.0, False) and ctl.flash_until_ts is None


def test_tick_pending_power_action_draws_message_and_asks_to_stop(ctl_env):
    ctl = _controller()
    with patch("app.display.db.get_setting", return_value="reboot"), \
         patch("app.display._draw_power_action_message") as draw:
        assert ctl.tick() is True
    draw.assert_called_once()
    assert draw.call_args.args[-1] == "reboot"


def test_tick_pending_action_draw_failure_still_stops(ctl_env):
    ctl = _controller()
    with patch("app.display.db.get_setting", return_value="poweroff"), \
         patch("app.display._draw_power_action_message", side_effect=OSError("SPI")):
        assert ctl.tick() is True                                # повідомлення не намалювалось, але Pi все одно вимикається


def test_tick_redraw_failure_is_contained_and_not_retried_every_tick(ctl_env):
    clock, _, _ = ctl_env
    calls = []
    ctl = _controller()
    with patch("app.display._redraw", side_effect=lambda *a, **kw: calls.append(1) or (_ for _ in ()).throw(RuntimeError("PIL"))):
        assert ctl.tick() is False
        clock.t += 1
        ctl.tick()
    assert len(calls) == 1 and ctl.last_redraw == 1000.0         # повтор - через DISPLAY_REFRESH_SEC


def test_tick_uses_pending_check_before_anything_else(ctl_env):
    """Порядок ітерації: повідомлення про reboot/poweroff - ДО кнопки й підсвітки."""
    ctl = _controller(button_events=["short_press"])
    with patch("app.display.db.get_setting", return_value="reboot"), patch("app.display._draw_power_action_message"):
        ctl.tick()
    assert ctl._button_tracker.events == ["short_press"]         # кнопка не опитувалась


# ---- _open_button(): частковий стан при збої зберігається (release потрібен у finally) ----

def test_open_button_keeps_release_when_tracker_creation_fails():
    """Лінію відкрито, а трекер створити не вдалось: release() усе одно має
    дійти до finally (інакше GPIO лишився б зайнятим до перезапуску сервісу)."""
    def release():
        pass
    with patch("app.display.gpio_utils.open_input_line", return_value=(lambda: 0, release)), \
         patch("app.display.gpio_utils.ButtonPressTracker", side_effect=ValueError("поганий HOLD_SEC")):
        get_value, got_release, tracker = display._open_button(27)
    assert got_release is release and get_value is not None and tracker is None


def test_open_button_disabled_pin_and_open_failure_give_nothing():
    for pin in (0, -1):
        assert display._open_button(pin) == (None, None, None)
    with patch("app.display.gpio_utils.open_input_line", side_effect=OSError("лінія зайнята")):
        assert display._open_button(27) == (None, None, None)
