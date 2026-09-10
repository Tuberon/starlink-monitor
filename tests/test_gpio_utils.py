"""
Тести для app/gpio_utils.py.

ButtonPressTracker - чиста, стейтфул логіка без залежності від
реального GPIO (як явно документовано в самому класі) - тестується
подачею довільної послідовності значень через poll(), без mock.

open_input_line()/open_output_line() - реальний gpiod (v1/v2) на
цьому середовищі відсутній (hardware-специфічна бібліотека), але
логіка вибору v1-vs-v2 API (`hasattr(gpiod.Chip, "get_line")`) і
викликів усередину gpiod-об'єкта - чиста, тестується підстановкою
fake-модуля в sys.modules['gpiod'] ПЕРЕД імпортом функції (яка сама
робить `import gpiod` лише всередині себе, не на рівні модуля -
тому підміна в sys.modules реально підхоплюється).
"""
import sys
import time
from unittest.mock import patch

import pytest

from app.gpio_utils import ButtonPressTracker, find_gpio_chip


# ---- find_gpio_chip() ----

def test_find_gpio_chip_returns_first_existing():
    with patch("os.path.exists", side_effect=lambda p: p == "/dev/gpiochip2"):
        assert find_gpio_chip() == "/dev/gpiochip2"


def test_find_gpio_chip_prefers_lowest_number():
    with patch("os.path.exists", side_effect=lambda p: p in ("/dev/gpiochip0", "/dev/gpiochip3")):
        assert find_gpio_chip() == "/dev/gpiochip0"


def test_find_gpio_chip_falls_back_when_none_exist():
    """Реальний edge case: жоден /dev/gpiochipN не існує (напр. тест
    без реального заліза чи неочікувана конфігурація ядра) - МАЄ
    повернути дефолт, не кидати виняток."""
    with patch("os.path.exists", return_value=False):
        assert find_gpio_chip() == "/dev/gpiochip0"


# ---- ButtonPressTracker - чиста стейтфул-логіка ----

def test_button_idle_returns_none():
    tracker = ButtonPressTracker(hold_sec=3.0)
    assert tracker.poll(1, now=0.0) is None  # не натиснута (pull-up, 1=released)


def test_button_short_press():
    tracker = ButtonPressTracker(hold_sec=3.0)
    assert tracker.poll(0, now=0.0) is None  # натиснута
    assert tracker.poll(1, now=1.0) == "short_press"  # відпущена ДО порогу утримання


def test_button_long_press_fires_exactly_at_threshold():
    tracker = ButtonPressTracker(hold_sec=3.0)
    tracker.poll(0, now=0.0)
    assert tracker.poll(0, now=2.9) is None  # ще не досяг порогу
    assert tracker.poll(0, now=3.0) == "long_press"  # рівно на порозі


def test_button_long_press_fires_only_once_while_held():
    """Реальна мета: утримання кнопки ДОВШЕ порогу (напр. користувач
    тримає 5с замість мінімальних 3с) НЕ має повторно спрацьовувати
    long_press на кожному наступному poll()."""
    tracker = ButtonPressTracker(hold_sec=3.0)
    tracker.poll(0, now=0.0)
    assert tracker.poll(0, now=3.0) == "long_press"
    assert tracker.poll(0, now=3.5) is None
    assert tracker.poll(0, now=5.0) is None


def test_button_release_after_long_press_does_not_fire_short_press():
    """Реальна мета: після long_press спрацював - НАСТУПНЕ відпущення
    кнопки НЕ має додатково спрацювати short_press (уже виконано
    довгу дію, коротка дія була б помилковою подвійною командою)."""
    tracker = ButtonPressTracker(hold_sec=3.0)
    tracker.poll(0, now=0.0)
    tracker.poll(0, now=3.0)  # long_press спрацював
    assert tracker.poll(1, now=4.0) is None  # відпущено - НЕ short_press


def test_button_new_press_cycle_after_release_works_again():
    """Контрольний тест: після повного циклу (натиснуто->long_press->
    відпущено) НАСТУПНЕ натискання МАЄ знову працювати звично (стан
    реально скидається, не залишається "залипшим" у triggered)."""
    tracker = ButtonPressTracker(hold_sec=3.0)
    tracker.poll(0, now=0.0)
    tracker.poll(0, now=3.0)
    tracker.poll(1, now=4.0)

    tracker.poll(0, now=10.0)
    assert tracker.poll(1, now=10.5) == "short_press"


def test_button_uses_real_time_when_now_not_provided():
    """poll() без явного now= МАЄ використовувати реальний time.time()
    (не кидати виняток, не вимагати now= обов'язково) - параметр
    існує лише для детермінізму в тестах."""
    tracker = ButtonPressTracker(hold_sec=0.05)
    tracker.poll(0)
    time.sleep(0.1)
    assert tracker.poll(0) == "long_press"


# ---- open_input_line()/open_output_line() через fake gpiod (v1 і v2) ----

def _install_fake_gpiod_v2():
    """v2 API: gpiod.Chip без get_line (звідси is_v2 detection),
    request_lines() повертає request-об'єкт з get_value/set_value/
    release."""
    calls = {"requests": []}

    class FakeRequest:
        def get_value(self, pin):
            return calls.get("input_value", 0)

        def set_value(self, pin, value):
            calls["last_set"] = (pin, value)

        def release(self):
            calls["released"] = True

    class FakeChip:
        pass  # немає get_line - це і є v2-детекція

    class FakeLineSettings:
        def __init__(self, direction=None, bias=None):
            pass

    class FakeGpiod:
        Chip = FakeChip
        LineSettings = FakeLineSettings

        def request_lines(self, chip_path, consumer, config):
            calls["requests"].append((chip_path, consumer))
            return FakeRequest()

    fake_line_mod = type("line_mod", (), {
        "Direction": type("Direction", (), {"INPUT": 1, "OUTPUT": 2}),
        "Bias": type("Bias", (), {"PULL_UP": 1}),
        "Value": type("Value", (), {"ACTIVE": 1, "INACTIVE": 0}),
    })

    fake_gpiod = FakeGpiod()
    sys.modules["gpiod"] = fake_gpiod
    sys.modules["gpiod.line"] = fake_line_mod
    return fake_gpiod, calls


def _install_fake_gpiod_v1():
    """v1 (застарілий) API: gpiod.Chip.get_line() існує (детекція
    v1), line.request()/get_value()/set_value()/release()."""
    calls = {}

    class FakeLine:
        def request(self, consumer, type, flags=None):
            calls["requested"] = (consumer, type, flags)

        def get_value(self):
            return calls.get("input_value", 0)

        def set_value(self, v):
            calls["last_set"] = v

        def release(self):
            calls["released"] = True

    class FakeChip:
        def __init__(self, path):
            calls["chip_path"] = path

        def get_line(self, pin):
            calls["pin"] = pin
            return FakeLine()

    fake_gpiod = type("FakeGpiodV1", (), {
        "Chip": FakeChip,
        "LINE_REQ_DIR_IN": "IN",
        "LINE_REQ_DIR_OUT": "OUT",
        "LINE_REQ_FLAG_BIAS_PULL_UP": "PULL_UP",
    })()
    sys.modules["gpiod"] = fake_gpiod
    return fake_gpiod, calls


@pytest.fixture(autouse=True)
def _cleanup_fake_gpiod():
    """Прибирає fake sys.modules['gpiod'] після кожного тесту - інакше
    підміна протікала б у решту тестового набору (інші тести, що
    імпортують щось пов'язане з gpiod, отримали б цей fake-модуль)."""
    yield
    sys.modules.pop("gpiod", None)
    sys.modules.pop("gpiod.line", None)


def test_open_input_line_v2_reads_value():
    from app import gpio_utils
    fake_gpiod, calls = _install_fake_gpiod_v2()
    calls["input_value"] = 0  # Value.INACTIVE -> get_value() має повернути 0

    get_value, release = gpio_utils.open_input_line(17, "test-consumer")
    assert get_value() == 0
    assert calls["requests"] == [("/dev/gpiochip0", "test-consumer")] or len(calls["requests"]) == 1
    release()
    assert calls["released"] is True


def test_open_input_line_v1_reads_value():
    from app import gpio_utils
    fake_gpiod, calls = _install_fake_gpiod_v1()
    calls["input_value"] = 1

    get_value, release = gpio_utils.open_input_line(17, "test-consumer")
    assert get_value() == 1
    assert calls["pin"] == 17
    release()
    assert calls["released"] is True


def test_open_output_line_v2_sets_value():
    from app import gpio_utils
    fake_gpiod, calls = _install_fake_gpiod_v2()

    set_value, release = gpio_utils.open_output_line(17, "test-consumer")
    set_value(1)
    assert calls["last_set"] == (17, 1)
    release()
    assert calls["released"] is True


def test_open_output_line_v1_sets_value():
    from app import gpio_utils
    fake_gpiod, calls = _install_fake_gpiod_v1()

    set_value, release = gpio_utils.open_output_line(17, "test-consumer")
    set_value(0)
    assert calls["last_set"] == 0
    release()
    assert calls["released"] is True
