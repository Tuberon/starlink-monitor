"""
Тести для app/activity_led.py - чиста логіка (init/blink/close) через
mock app.gpio_utils.open_output_line(), без реального GPIO. Реальний
gpiod-виклик не тестується (потребує фізичного заліза, як і решта
GPIO-коду в проєкті) - лише поведінка ActivityLed навколо нього.
"""
import time
from unittest.mock import patch

from app.activity_led import ActivityLed


def _fake_open_output_line(calls):
    """Повертає (set_value, release), де set_value записує кожен
    виклик у calls-список для перевірки послідовності."""
    def set_value(v):
        calls.append(v)

    def release():
        calls.append("released")

    return set_value, release


def test_init_with_pin_zero_disables_led_without_gpio_call():
    """pin=0 - LED вимкнено навмисно (opt-in фіча), НЕ має намагатись
    відкрити GPIO взагалі."""
    with patch("app.gpio_utils.open_output_line") as mock_open:
        led = ActivityLed(pin=0, blink_ms=50)
        ok = led.init()
    assert ok is False
    mock_open.assert_not_called()


def test_init_gpio_failure_returns_false_not_raises():
    """Реальний сценарій: пін зайнятий іншим процесом чи gpiod не
    встановлено - init() МАЄ повернути False, не кидати виняток
    (виклик з run_forever() не повинен через це впасти)."""
    with patch("app.gpio_utils.open_output_line", side_effect=RuntimeError("пін зайнятий")):
        led = ActivityLed(pin=17, blink_ms=50)
        ok = led.init()
    assert ok is False


def test_blink_without_init_does_nothing():
    """blink() до успішного init() (чи після невдалого) - тихо нічого
    не робить, не кидає виняток."""
    led = ActivityLed(pin=17, blink_ms=50)
    led.blink()  # не мало кинути виняток


def test_blink_turns_on_immediately_and_off_after_delay():
    calls = []
    with patch("app.gpio_utils.open_output_line", return_value=_fake_open_output_line(calls)):
        led = ActivityLed(pin=17, blink_ms=30)
        led.init()
        calls.clear()  # прибираємо початковий set_value(0) з init()

        led.blink()
        assert calls == [1], "LED мав увімкнутись НЕГАЙНО, синхронно"

        time.sleep(0.06)
        assert calls == [1, 0], "LED мав вимкнутись через blink_ms (неблокуючим таймером)"
        led.close()


def test_rapid_successive_blinks_stay_on_without_flicker():
    """Реальний сценарій: кілька db-записів підряд (напр. dish-flush +
    system_metrics в одному циклі) - LED МАЄ лишатись рівно увімкненим
    між ними, не блимати нервово (попередній таймер скасовується)."""
    calls = []
    with patch("app.gpio_utils.open_output_line", return_value=_fake_open_output_line(calls)):
        led = ActivityLed(pin=17, blink_ms=50)
        led.init()
        calls.clear()

        led.blink()
        time.sleep(0.02)
        led.blink()  # ДО того, як перший таймер встиг вимкнути LED

        assert calls == [1, 1], "між двома blink() НЕ мало бути проміжного 0"
        time.sleep(0.07)
        assert calls == [1, 1, 0], "LED мав вимкнутись рівно ОДИН раз, від останнього таймера"
        led.close()


def test_close_cancels_pending_timer_and_releases_gpio():
    calls = []
    with patch("app.gpio_utils.open_output_line", return_value=_fake_open_output_line(calls)):
        led = ActivityLed(pin=17, blink_ms=100)
        led.init()
        calls.clear()

        led.blink()
        led.close()

        assert "released" in calls
        calls_before_wait = list(calls)
        time.sleep(0.15)
        assert calls == calls_before_wait, "скасований таймер НЕ мав спрацювати після close()"


def test_blink_callback_exception_does_not_propagate():
    """set_value() сам кидає виняток (напр. GPIO раптово відвалився) -
    blink() МАЄ це проковтнути, не ламаючи виклик з db.py."""
    def broken_set_value(v):
        raise RuntimeError("GPIO помилка")

    with patch("app.gpio_utils.open_output_line", return_value=(broken_set_value, lambda: None)):
        led = ActivityLed(pin=17, blink_ms=50)
        led.init()
        led.blink()  # не мало кинути виняток назовні
