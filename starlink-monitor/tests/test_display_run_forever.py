"""
Тести для app/display.py:run_forever() - головний цикл, що ініціалізує
реальне SPI/GPIO залізо (board/digitalio/busio/adafruit_rgb_display) і
опитує його в нескінченному циклі.

Апаратні бібліотеки підмінюються fake-модулями в sys.modules ПЕРЕД
викликом (run_forever() сам робить `import board` тощо лише
всередині себе, не на рівні модуля - тому підміна в sys.modules
реально підхоплюється, той самий підхід, що вже застосований для
gpiod у test_gpio_utils.py/test_shutdown_button.py).

На відміну від shutdown_button.py:watch_button(), ця функція вже має
stop_event-параметр - чистіший спосіб зупинки циклу в тестах, ніж
SystemExit-трюк (потрібен лише для сценаріїв, де сама функція
завершується через return, не чекаючи stop_event - напр. pending
shutdown-сигнал).
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


def test_run_forever_missing_hardware_libs_returns(caplog):
    """Реальний сценарій: пакети adafruit-blinka/adafruit-circuitpython-
    rgb-display не встановлені (DISPLAY_ENABLED=1 виставлено, але
    пакети не поставлені) - МАЄ логувати помилку і завершитись, не
    кидати виняток назовні."""
    config.DISPLAY_ENABLED = True
    for m in ("board", "digitalio", "busio", "adafruit_rgb_display"):
        sys.modules.pop(m, None)
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
         patch("app.display._trigger_shutdown", side_effect=lambda pin: (triggered.append(pin), stop_event.set())), \
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
