"""
Тести для app/shutdown_button.py.

watch_button() має нескінченний `while True`-цикл (реальний polling
GPIO) - зупиняється в тестах через SystemExit (НЕ Exception-підклас,
тому внутрішній `except Exception` циклу, що обробляє помилки читання
GPIO, його не ловить - на відміну від звичайного власного винятку,
який був би пійманий цим самим блоком і призвів до реального
нескінченного циклу в тесті). Реальний прогрес часу для ButtonPress
Tracker контролюється через mock time.time(), не time.sleep() (яка
теж mock-ається, але лише щоб не сповільнювати тест).
"""
import sys
from unittest.mock import patch

import pytest

from app import config, shutdown_button


@pytest.fixture(autouse=True)
def _fake_gpiod_module():
    """watch_button() робить `import gpiod` лише для перевірки
    наявності бібліотеки (except ImportError) - без fake-модуля кожен
    тест реального циклу провалився б на цій перевірці, не досягнувши
    коду, що тестується."""
    sys.modules["gpiod"] = type("FakeGpiod", (), {})()
    yield
    sys.modules.pop("gpiod", None)


def test_watch_button_pin_zero_returns_immediately(caplog):
    config.SHUTDOWN_BUTTON_GPIO_PIN = 0
    with caplog.at_level("INFO"):
        shutdown_button.watch_button()
    assert "вимкнена" in caplog.text


def test_watch_button_display_enabled_defers_to_display_py(caplog):
    """Реальна мета: коли TFT-дисплей увімкнено, ТА САМА кнопка
    обробляється всередині display.py - цей сервіс не повинен
    конкурувати за GPIO-пін, тому одразу завершується."""
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.DISPLAY_ENABLED = True
    try:
        with caplog.at_level("INFO"):
            shutdown_button.watch_button()
        assert "display.py" in caplog.text
    finally:
        config.DISPLAY_ENABLED = False


def test_watch_button_gpiod_not_installed_returns(caplog):
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.DISPLAY_ENABLED = False
    sys.modules.pop("gpiod", None)  # прибираємо fake з fixture - симулюємо ImportError
    with caplog.at_level("ERROR"):
        shutdown_button.watch_button()
    assert "не встановлена" in caplog.text


def test_watch_button_gpio_init_failure_returns(caplog):
    """Реальний edge case: пін уже зайнятий іншим процесом чи
    недоступний - open_input_line() кидає виняток, МАЄ бути пійманий,
    не поширюватись назовні як crash сервісу."""
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.DISPLAY_ENABLED = False
    with patch("app.gpio_utils.open_input_line", side_effect=RuntimeError("пін зайнятий")):
        with caplog.at_level("ERROR"):
            shutdown_button.watch_button()
    assert "Не вдалося ініціалізувати" in caplog.text


def test_watch_button_long_press_triggers_shutdown():
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.DISPLAY_ENABLED = False
    config.SHUTDOWN_BUTTON_HOLD_SEC = 3.0
    config.SHUTDOWN_BUTTON_POLL_INTERVAL_SEC = 0.1

    fake_times = iter([0.0, 3.5])
    values = iter([0, 0])
    released = []

    def fake_get_value():
        try:
            return next(values)
        except StopIteration:
            raise SystemExit()

    triggered = []
    with patch("app.gpio_utils.open_input_line",
               return_value=(fake_get_value, lambda: released.append(1))), \
         patch("app.shutdown_button._trigger_shutdown", side_effect=lambda pin: triggered.append(pin)), \
         patch("time.sleep"), \
         patch("time.time", side_effect=lambda: next(fake_times, 100.0)):
        with pytest.raises(SystemExit):
            shutdown_button.watch_button()

    assert triggered == [27]
    assert released == [1]  # release() реально викликається у finally


def test_watch_button_short_press_does_not_trigger_shutdown():
    """Контрольний тест: коротке натискання (відпущено ДО порогу
    утримання) НЕ має викликати _trigger_shutdown."""
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.DISPLAY_ENABLED = False
    config.SHUTDOWN_BUTTON_HOLD_SEC = 3.0
    config.SHUTDOWN_BUTTON_POLL_INTERVAL_SEC = 0.1

    fake_times = iter([0.0, 0.5])
    values = iter([0, 1])  # натиснуто, потім відпущено (коротке)

    def fake_get_value():
        try:
            return next(values)
        except StopIteration:
            raise SystemExit()

    triggered = []
    with patch("app.gpio_utils.open_input_line", return_value=(fake_get_value, lambda: None)), \
         patch("app.shutdown_button._trigger_shutdown", side_effect=lambda pin: triggered.append(pin)), \
         patch("time.sleep"), \
         patch("time.time", side_effect=lambda: next(fake_times, 100.0)):
        with pytest.raises(SystemExit):
            shutdown_button.watch_button()

    assert triggered == []


def test_watch_button_gpio_read_error_does_not_crash_loop():
    """Реальний edge case: тимчасова помилка читання GPIO (напр.
    короткочасна проблема з ядром) - МАЄ логуватись і цикл МАЄ
    продовжуватись, не крашитись повністю."""
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.DISPLAY_ENABLED = False

    call_count = {"n": 0}

    def flaky_get_value():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("тимчасова помилка читання")
        raise SystemExit()  # зупиняємо цикл на 2-й спробі

    with patch("app.gpio_utils.open_input_line", return_value=(flaky_get_value, lambda: None)), \
         patch("time.sleep"), \
         patch("time.time", return_value=0.0):
        with pytest.raises(SystemExit):
            shutdown_button.watch_button()

    assert call_count["n"] == 2  # цикл реально продовжився після помилки


def test_watch_button_release_called_even_if_loop_raises_unexpected_error():
    """release() у finally МАЄ викликатись навіть якщо цикл завершився
    неочікуваним (не SystemExit) винятком - ресурс GPIO не має
    лишитись незвільненим при crash."""
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.DISPLAY_ENABLED = False
    released = []

    def crashing_tracker_poll(*a, **kw):
        raise ValueError("неочікувана помилка всередині tracker.poll")

    with patch("app.gpio_utils.open_input_line", return_value=(lambda: 1, lambda: released.append(1))), \
         patch("app.gpio_utils.ButtonPressTracker.poll", side_effect=crashing_tracker_poll), \
         patch("time.sleep"):
        with pytest.raises(ValueError):
            shutdown_button.watch_button()

    assert released == [1]


# ---- _trigger_shutdown() ----

def test_trigger_shutdown_calls_pi_power_with_poweroff_command(db_path):
    calls = []
    with patch("app.pi_power.execute_pi_power_action", side_effect=lambda *a, **kw: calls.append((a, kw))):
        shutdown_button._trigger_shutdown(27)

    assert len(calls) == 1
    args = calls[0][0]
    assert args[0] == ["sudo", "systemctl", "poweroff"]
    assert args[1] == "poweroff"
    assert "GPIO27" in args[3]


def test_trigger_shutdown_db_init_failure_does_not_block_poweroff():
    """Реальна мета: навіть якщо db.init_db() провалюється (напр.
    пошкоджена БД чи заповнена SD-картка), реальний poweroff МАЄ все
    одно виконатись - фізичне вимкнення важливіше за журналювання."""
    calls = []
    with patch("app.db.init_db", side_effect=RuntimeError("БД пошкоджена")), \
         patch("app.pi_power.execute_pi_power_action", side_effect=lambda *a, **kw: calls.append(a)):
        shutdown_button._trigger_shutdown(27)
    assert len(calls) == 1


def test_watch_button_release_exception_does_not_propagate():
    """Реальний edge case: release() сам кидає виняток при завершенні
    (напр. GPIO вже звільнений іншим шляхом) - НЕ має перекривати
    оригінальну причину завершення циклу (SystemExit тут)."""
    config.SHUTDOWN_BUTTON_GPIO_PIN = 27
    config.DISPLAY_ENABLED = False

    def broken_release():
        raise RuntimeError("вже звільнено")

    with patch("app.gpio_utils.open_input_line", return_value=(lambda: (_ for _ in ()).throw(SystemExit()), broken_release)), \
         patch("time.sleep"):
        with pytest.raises(SystemExit):
            shutdown_button.watch_button()
