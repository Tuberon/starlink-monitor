"""
Тести для app/pi_power.py - спільна логіка reboot/poweroff Raspberry
Pi (викликається з веб-дашборду й фізичної кнопки). Головний сценарій:
DB-сигнал (PENDING_ACTION_SETTING_KEY) МАЄ бути записаний ДО реального
виконання systemctl-команди - display.py (окремий процес) опитує цей
сигнал у швидкому циклі, щоб встигнути показати повідомлення на екрані
до того, як сам процес дисплея буде вбитий через SIGTERM.
"""
from unittest.mock import MagicMock, patch

from app import config, db, pi_power


def _fake_run_success(*args, **kwargs):
    return MagicMock(returncode=0, stderr="")


def _fake_run_failure(*args, **kwargs):
    return MagicMock(returncode=1, stderr="команда провалилась")


def test_signal_written_before_command_executes(db_path):
    """Головний сценарій: у момент виклику systemctl-команди сигнал
    для дисплея МАЄ бути вже записаний у БД - інакше display.py не
    встигне побачити його до того, як SIGTERM вб'є процес."""
    signal_at_call_time = []

    def fake_run(*args, **kwargs):
        signal_at_call_time.append(db.get_setting(pi_power.PENDING_ACTION_SETTING_KEY))
        return MagicMock(returncode=0, stderr="")

    config.DISPLAY_ENABLED = False
    with patch("subprocess.run", side_effect=fake_run):
        pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
            "тест", "успіх", "вимкнути", notify_fn=lambda t: None,
        )

    assert signal_at_call_time == ["poweroff"]


def test_reboot_action_writes_reboot_signal(db_path):
    signal_at_call_time = []

    def fake_run(*args, **kwargs):
        signal_at_call_time.append(db.get_setting(pi_power.PENDING_ACTION_SETTING_KEY))
        return MagicMock(returncode=0, stderr="")

    config.DISPLAY_ENABLED = False
    with patch("subprocess.run", side_effect=fake_run):
        pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "reboot"], "reboot", "pi_reboot",
            "тест", "успіх", "перезавантажити", notify_fn=lambda t: None,
        )

    assert signal_at_call_time == ["reboot"]


def test_success_sends_success_text_and_logs_event(db_path):
    notified = []
    config.DISPLAY_ENABLED = False
    with patch("subprocess.run", side_effect=_fake_run_success):
        ok, msg = pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
            "Подія в журналі", "Успішне повідомлення", "вимкнути",
            notify_fn=notified.append,
        )

    assert ok is True
    assert notified == ["Успішне повідомлення"]
    events = db.get_recent_events(10)
    assert any(e["kind"] == "pi_shutdown" for e in events)


def test_failure_sends_failure_text_with_reason(db_path):
    notified = []
    config.DISPLAY_ENABLED = False
    with patch("subprocess.run", side_effect=_fake_run_failure):
        ok, msg = pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
            "тест", "успіх", "вимкнути", notify_fn=notified.append,
        )

    assert ok is False
    assert len(notified) == 1
    assert "Не вдалося вимкнути" in notified[0]
    assert "команда провалилась" in notified[0]


def test_failure_clears_pending_signal(db_path):
    """Реальна мета: якщо команда провалилась (Pi НЕ вимикається/не
    перезавантажується), сигнал для дисплея МАЄ бути прибраний -
    інакше він лишиться "завислим" і покаже застаріле повідомлення
    при наступному запуску display.py."""
    config.DISPLAY_ENABLED = False
    with patch("subprocess.run", side_effect=_fake_run_failure):
        pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
            "тест", "успіх", "вимкнути", notify_fn=lambda t: None,
        )

    assert db.get_setting(pi_power.PENDING_ACTION_SETTING_KEY) == ""


def test_display_disabled_does_not_sleep(db_path):
    """DISPLAY_ENABLED=0 - немає сенсу чекати DISPLAY_SHUTDOWN_
    MESSAGE_DELAY_SEC, немає екрана, який міг би побачити сигнал."""
    config.DISPLAY_ENABLED = False
    config.DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC = 999  # мало б "зависнути" тест, якби sleep реально викликався

    with patch("subprocess.run", side_effect=_fake_run_success), patch("time.sleep") as mock_sleep:
        pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
            "тест", "успіх", "вимкнути", notify_fn=lambda t: None,
        )
    mock_sleep.assert_not_called()


def test_display_enabled_sleeps_configured_delay(db_path):
    config.DISPLAY_ENABLED = True
    config.DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC = 2.5

    with patch("subprocess.run", side_effect=_fake_run_success), patch("time.sleep") as mock_sleep:
        pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
            "тест", "успіх", "вимкнути", notify_fn=lambda t: None,
        )
    mock_sleep.assert_called_once_with(2.5)
    config.DISPLAY_ENABLED = False  # прибираємо за собою для решти тестів


def test_notify_callback_default_used_when_not_specified(db_path):
    """Без явного notify_fn - МАЄ використовуватись telegram_notify.
    send_message за замовчуванням (реальна поведінка для webapp.py/
    shutdown_button.py, які не передають власний callback)."""
    config.DISPLAY_ENABLED = False
    with patch("subprocess.run", side_effect=_fake_run_success), \
         patch("app.telegram_notify.send_message") as mock_send:
        pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
            "тест", "дефолтне сповіщення", "вимкнути",
        )
    mock_send.assert_called_once_with("дефолтне сповіщення")
