"""
Тести для чистих функцій app/display.py - виявлення зміни update_state
(flash-сповіщення підсвіткою) та auto-off логіки. Реальний GPIO/SPI-
дисплей не тестується (потребує фізичного заліза) - лише чиста,
детерміністична логіка, винесена саме для тестованості.
"""
from app.display import _should_auto_off, _update_state_changed


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
