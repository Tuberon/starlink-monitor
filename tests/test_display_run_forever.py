"""Тести app/display.py:run_forever() — цикл, що ініціалізує SPI/GPIO
(board/digitalio/busio/adafruit_rgb_display). Апаратні бібліотеки підмінюються fake-модулями в
sys.modules ПЕРЕД викликом (run_forever() імпортує їх лише всередині — як gpiod у test_gpio_utils.py).
Функція має stop_event — чистіший спосіб зупинки, ніж SystemExit-трюк (він потрібен лише коли функція
завершується через return, напр. pending shutdown-сигнал).
"""
import sys
import threading
from unittest.mock import patch

import pytest

from app import config, db, display


class _FakePin:
    def __init__(self, *a, **kw):
        self.value = None

    def switch_to_output(self, value=True):
        self.value = value


class _FakeHardwareDisplay:
    width, height = 170, 320

    def __init__(self):
        self.images = []

    def image(self, img):
        self.images.append(img)


@pytest.fixture(autouse=True)
def _restore_display_enabled(monkeypatch):
    """Тести присвоюють config.DISPLAY_ENABLED напряму: monkeypatch.setattr відновлює початкове значення
    після кожного тесту, інакше True протікав би в інші файли (pi_power.py і shutdown_button.py читають
    прапорець) і залежав від порядку під pytest-randomly.
    """
    monkeypatch.setattr(config, "DISPLAY_ENABLED", config.DISPLAY_ENABLED)


@pytest.fixture
def fake_hardware():
    """Встановлює fake board/digitalio/busio/adafruit_rgb_display в
    sys.modules і повертає fake-дисплей (щоб перевіряти реально
    намальовані кадри) - прибирає підміну після тесту, інакше вона
    протікала б у решту тестового набору."""
    fake_display_instance = _FakeHardwareDisplay()

    fake_board = type("board", (), {
        "SCK": 1, "MOSI": 2, "MISO": 3,
        "D8": 8, "D25": 25, "D24": 24, "D18": 18,
    })()
    fake_digitalio = type("digitalio", (), {"DigitalInOut": _FakePin})()
    fake_busio = type("busio", (), {"SPI": lambda *a, **kw: object()})()
    fake_st7789_mod = type("st7789", (), {
        "ST7789": lambda *a, **kw: fake_display_instance,
    })()
    fake_rgb_display_pkg = type("adafruit_rgb_display", (), {"st7789": fake_st7789_mod})()

    sys.modules["board"] = fake_board
    sys.modules["digitalio"] = fake_digitalio
    sys.modules["busio"] = fake_busio
    sys.modules["adafruit_rgb_display"] = fake_rgb_display_pkg
    sys.modules["adafruit_rgb_display.st7789"] = fake_st7789_mod

    yield fake_display_instance

    for m in ("board", "digitalio", "busio", "adafruit_rgb_display", "adafruit_rgb_display.st7789"):
        sys.modules.pop(m, None)


# ---- Early-return гілки (без fake_hardware - МАЮТЬ повернутись до спроби import) ----

def test_run_forever_disabled_returns_immediately(caplog):
    config.DISPLAY_ENABLED = False
    with caplog.at_level("INFO"):
        display.run_forever()
    assert "вимкнено" in caplog.text


def test_run_forever_missing_hardware_libs_returns(caplog, monkeypatch):
    """Пакети adafruit-blinka/adafruit-circuitpython-rgb-display не встановлені при DISPLAY_ENABLED=1 — МАЄ
    логувати помилку й завершитись без винятку. sys.modules[m] = None (не .pop()) змушує `import m`
    кидати ImportError незалежно від наявності пакета на диску; .pop() лише чистив кеш, і на машині з
    повним requirements.txt тест перевіряв середовище, а не код.
    """
    config.DISPLAY_ENABLED = True
    for m in ("board", "digitalio", "busio", "adafruit_rgb_display"):
        monkeypatch.setitem(sys.modules, m, None)
    with caplog.at_level("ERROR"):
        display.run_forever()
    assert "не встановлено" in caplog.text


# ---- Повна ініціалізація через fake hardware ----

def test_run_forever_initializes_and_stops_via_stop_event(fake_hardware, db_path, caplog):
    config.DISPLAY_ENABLED = True
    config.SHUTDOWN_BUTTON_GPIO_PIN = 0  # без кнопки для цього тесту

    stop_event = threading.Event()
    stop_event.set()  # зупиняємо на першій перевірці циклу

    with caplog.at_level("INFO"):
        display.run_forever(stop_event=stop_event)
    assert "ініціалізовано" in caplog.text


def test_run_forever_spi_init_failure_returns(caplog):
    """Реальний edge case: SPI/GPIO-піни зайняті іншим процесом чи
    неправильно налаштовані - виняток під час ініціалізації МАЄ бути
    пійманий, не поширюватись назовні як crash сервісу."""
    config.DISPLAY_ENABLED = True
    fake_board = type("board", (), {"SCK": 1, "MOSI": 2, "MISO": 3})()
    fake_digitalio = type("digitalio", (), {"DigitalInOut": _FakePin})()

    def broken_spi(*a, **kw):
        raise RuntimeError("SPI зайнятий іншим процесом")

    fake_busio = type("busio", (), {"SPI": broken_spi})()
    fake_st7789_mod = type("st7789", (), {"ST7789": lambda *a, **kw: None})()
    fake_rgb_display_pkg = type("adafruit_rgb_display", (), {"st7789": fake_st7789_mod})()

    sys.modules["board"] = fake_board
    sys.modules["digitalio"] = fake_digitalio
    sys.modules["busio"] = fake_busio
    sys.modules["adafruit_rgb_display"] = fake_rgb_display_pkg
    sys.modules["adafruit_rgb_display.st7789"] = fake_st7789_mod
    try:
        with caplog.at_level("ERROR"):
            display.run_forever()
        assert "Не вдалося ініціалізувати дисплей" in caplog.text
    finally:
        for m in ("board", "digitalio", "busio", "adafruit_rgb_display", "adafruit_rgb_display.st7789"):
            sys.modules.pop(m, None)


def test_run_forever_pending_shutdown_signal_draws_message_and_returns(fake_hardware, db_path):
    """Реальна мета: pi_power.py записує DB-сигнал ПЕРЕД реальним
    systemctl reboot/poweroff - цикл МАЄ виявити його майже одразу
    (не чекаючи звичайний DISPLAY_REFRESH_SEC), намалювати повідомлення
    і завершитись (не через stop_event - через власний return)."""
    from app import pi_power
    config.DISPLAY_ENABLED = True
    config.SHUTDOWN_BUTTON_GPIO_PIN = 0
    db.set_setting(pi_power.PENDING_ACTION_SETTING_KEY, "poweroff")

    with patch("time.sleep"):
        display.run_forever()  # МАЄ сам завершитись через return, без stop_event

    assert len(fake_hardware.images) >= 1  # повідомлення реально намальоване


def test_run_forever_button_short_press_toggles_backlight(fake_hardware, db_path):
    config.DISPLAY_ENABLED = True
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.SHUTDOWN_BUTTON_HOLD_SEC = 3.0
    config.DISPLAY_REFRESH_SEC = 9999  # не форсуємо redraw під час цього тесту

    stop_event = threading.Event()
    values = iter([0, 1])  # натиснуто, потім відпущено (коротке) -> short_press
    backlight_calls = []

    def fake_get_value():
        try:
            return next(values)
        except StopIteration:
            stop_event.set()
            return 1

    with patch("app.gpio_utils.open_input_line", return_value=(fake_get_value, lambda: None)), \
         patch("app.display._set_backlight", side_effect=lambda pin, v: backlight_calls.append(v)), \
         patch("time.sleep"):
        display.run_forever(stop_event=stop_event)

    assert False in backlight_calls  # short_press реально перемкнув підсвітку (True->False)


def test_run_forever_button_long_press_triggers_shutdown(fake_hardware, db_path):
    config.DISPLAY_ENABLED = True
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.SHUTDOWN_BUTTON_HOLD_SEC = 3.0
    config.DISPLAY_REFRESH_SEC = 9999

    stop_event = threading.Event()
    fake_times = iter([0.0, 0.0, 3.5])  # last_activity_ts, now(1), now(2)>=hold_sec
    values = iter([0, 0])

    def fake_get_value():
        try:
            return next(values)
        except StopIteration:
            stop_event.set()
            return 1

    triggered = []
    with patch("app.gpio_utils.open_input_line", return_value=(fake_get_value, lambda: None)), \
         patch("app.pi_power.shutdown_from_button", side_effect=lambda pin: (triggered.append(pin), stop_event.set())), \
         patch("time.sleep"), \
         patch("time.time", side_effect=lambda: next(fake_times, 100.0)):
        display.run_forever(stop_event=stop_event)

    assert triggered == [27]


def test_run_forever_button_release_called_on_exit(fake_hardware, db_path):
    config.DISPLAY_ENABLED = True
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.DISPLAY_REFRESH_SEC = 9999
    released = []

    stop_event = threading.Event()
    stop_event.set()

    with patch("app.gpio_utils.open_input_line", return_value=(lambda: 1, lambda: released.append(1))), \
         patch("time.sleep"):
        display.run_forever(stop_event=stop_event)

    assert released == [1]


def test_run_forever_button_init_failure_continues_without_button(fake_hardware, db_path, caplog):
    """Реальний edge case: кнопка налаштована (SHUTDOWN_BUTTON_GPIO_
    PIN>0), але GPIO-пін зайнятий - дисплей МАЄ продовжити працювати
    без кнопки, не crashнутись повністю через це."""
    config.DISPLAY_ENABLED = True
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.DISPLAY_REFRESH_SEC = 9999

    stop_event = threading.Event()
    stop_event.set()

    with patch("app.gpio_utils.open_input_line", side_effect=RuntimeError("зайнятий")), \
         caplog.at_level("ERROR"), \
         patch("time.sleep"):
        display.run_forever(stop_event=stop_event)

    assert "Не вдалося ініціалізувати кнопку" in caplog.text


def test_run_forever_redraws_after_refresh_interval(fake_hardware, db_path):
    config.DISPLAY_ENABLED = True
    config.SHUTDOWN_BUTTON_GPIO_PIN = 0
    config.DISPLAY_REFRESH_SEC = 5

    stop_event = threading.Event()
    fake_times = iter([0.0, 10.0])  # перша перевірка last_redraw=0, друга - вже минуло 10с > REFRESH_SEC

    def fake_sleep(_):
        stop_event.set()  # зупиняємось після першого проходу через redraw

    with patch("time.sleep", side_effect=fake_sleep), \
         patch("time.time", side_effect=lambda: next(fake_times, 100.0)):
        display.run_forever(stop_event=stop_event)

    assert len(fake_hardware.images) >= 1


# ---- спалах підсвітки при зміні стану оновлення (раніше не виконувався жодним тестом) ----

def _run_flash_scenario(fake_hardware, flash_sec):
    """Скриптований годинник: початковий стан -> з'являється новий
    update_state -> спливає час спалаху. Повертає [(момент, значення)]
    усіх перемикань підсвітки."""
    config.DISPLAY_ENABLED = True
    config.SHUTDOWN_BUTTON_GPIO_PIN = 0
    config.DISPLAY_REFRESH_SEC = 5
    config.DISPLAY_UPDATE_FLASH_SEC = flash_sec
    config.DISPLAY_BACKLIGHT_AUTO_OFF_SEC = 0        # ізолюємо спалах від звичайного автовимкнення

    clock = {"t": 1000.0}
    switched = []
    stop_event = threading.Event()
    db.insert_metric({"timestamp": 1000.0, "online": True, "update_state": "IDLE"})

    def new_state_appears():
        db.insert_metric({"timestamp": 1005.0, "online": True, "update_state": "FETCHING"})
        clock["t"] += 10

    script = [new_state_appears, lambda: clock.__setitem__("t", clock["t"] + 10),
              lambda: clock.__setitem__("t", clock["t"] + 25)]

    def fake_sleep(_):
        if script:
            script.pop(0)()
        else:
            stop_event.set()

    with patch("time.sleep", side_effect=fake_sleep), \
         patch("time.time", side_effect=lambda: clock["t"]), \
         patch("app.display._set_backlight", side_effect=lambda pin, v: switched.append((clock["t"], v))):
        display.run_forever(stop_event=stop_event)
    return switched


def test_backlight_flashes_on_update_state_change_then_turns_off(fake_hardware, db_path):
    switched = _run_flash_scenario(fake_hardware, flash_sec=30)
    # спалах: увімкнено в момент зміни (1010), вимкнено, коли минуло 30 с (1045 >= 1010+30)
    assert switched == [(1010.0, True), (1045.0, False)]


def test_no_flash_when_disabled_by_zero_seconds(fake_hardware, db_path):
    assert _run_flash_scenario(fake_hardware, flash_sec=0) == []


def test_first_reading_after_start_is_not_a_state_change(fake_hardware, db_path):
    """Після запуску сервісу перший стан - не "зміна": екран не спалахує."""
    config.DISPLAY_ENABLED = True
    config.SHUTDOWN_BUTTON_GPIO_PIN = 0
    config.DISPLAY_REFRESH_SEC = 5
    config.DISPLAY_UPDATE_FLASH_SEC = 30
    config.DISPLAY_BACKLIGHT_AUTO_OFF_SEC = 0
    db.insert_metric({"timestamp": 1000.0, "online": True, "update_state": "FETCHING"})
    stop_event = threading.Event()
    switched = []
    with patch("time.sleep", side_effect=lambda _: stop_event.set()), \
         patch("time.time", side_effect=lambda: 1000.0), \
         patch("app.display._set_backlight", side_effect=lambda pin, v: switched.append(v)):
        display.run_forever(stop_event=stop_event)
    assert switched == []
