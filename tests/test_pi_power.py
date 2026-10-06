"""Тести app/pi_power.py (reboot/poweroff для веб-дашборда й кнопки). Головне: DB-сигнал
(PENDING_ACTION_SETTING_KEY) МАЄ бути записаний ДО виконання systemctl-команди, щоб display.py (окремий
процес) встиг показати повідомлення до SIGTERM.
"""
from unittest.mock import MagicMock, patch

from app import config, db, pi_power


def _fake_run_success(*args, **kwargs):
    return MagicMock(returncode=0, stderr="")


def _fake_run_failure(*args, **kwargs):
    return MagicMock(returncode=1, stderr="команда провалилась")


def test_signal_cleared_before_command_executes(db_path):
    """Баг: сигнал очищався ПІСЛЯ run_system_command(), але функція виконується в starlink-webui.service,
    який systemctl reboot сам і зупиняє — SIGTERM міг прийти раніше за очищення, і після
    перезавантаження display.py малював застаріле повідомлення. На момент виконання команди сигнал уже
    виконав свою мету (display.py встиг його побачити під час затримки), тож МАЄ бути очищеним, а не
    'poweroff'.
    """
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

    assert signal_at_call_time == [""]


def test_reboot_action_signal_cleared_before_command(db_path):
    """Той самий принцип, що test_signal_cleared_before_command_
    executes, для reboot-дії."""
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

    assert signal_at_call_time == [""]


def test_signal_written_before_delay_when_display_enabled(db_path):
    """Контрольний тест: коли DISPLAY_ENABLED=True, сигнал МАЄ бути
    записаний ("reboot"/"poweroff") ще ДО затримки (щоб display.py
    реально мав що побачити протягом неї) - перевіряється напряму
    між записом сигналу і сном, обходячи time.sleep()."""
    config.DISPLAY_ENABLED = True
    config.DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC = 0.01
    with patch("time.sleep") as mock_sleep:
        def check_signal_during_sleep(*args, **kwargs):
            assert db.get_setting(pi_power.PENDING_ACTION_SETTING_KEY) == "reboot"
        mock_sleep.side_effect = check_signal_during_sleep
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stderr="")):
            pi_power.execute_pi_power_action(
                ["sudo", "systemctl", "reboot"], "reboot", "pi_reboot",
                "тест", "успіх", "перезавантажити", notify_fn=lambda t: None,
            )
    config.DISPLAY_ENABLED = False


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


def test_success_also_clears_pending_signal(db_path):
    """Баг: сигнал очищався ЛИШЕ при провалі команди, тож при успішному reboot/poweroff лишався в БД
    назавжди (systemctl reboot не миттєвий — є кілька секунд до вимкнення). Після перезавантаження
    display.py бачив застарілий сигнал, малював повідомлення й завершувався; Restart=on-failure не
    перезапускає чисте завершення, сервіс лишався мертвим, а екран тримав останній кадр у framebuffer —
    "текст висить безкінечно".
    """
    config.DISPLAY_ENABLED = False
    with patch("subprocess.run", side_effect=_fake_run_success):
        pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "reboot"], "reboot", "pi_reboot",
            "тест", "успіх", "перезавантажити", notify_fn=lambda t: None,
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


def test_run_system_command_exception_returns_false(db_path):
    """Реальний edge case: subprocess.run() кидає виняток (напр. sudo
    взагалі не знайдений у PATH) - МАЄ бути пійманий, повертати
    (False, повідомлення), не поширюватись назовні."""
    config.DISPLAY_ENABLED = False
    with patch("subprocess.run", side_effect=FileNotFoundError("sudo не знайдено")), \
         patch("app.telegram_notify.send_message"):
        ok, msg = pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
            "тест", "успіх", "вимкнути", notify_fn=lambda t: None,
        )
    assert ok is False
    assert "sudo не знайдено" in msg


def test_pending_signal_write_failure_does_not_block_real_action(db_path):
    """Реальна мета: навіть якщо запис pending-сигналу для дисплея
    провалюється (напр. БД тимчасово заблокована) - реальний reboot/
    poweroff МАЄ все одно відбутись, це критичніше за сигнал екрана."""
    config.DISPLAY_ENABLED = False
    executed = []
    with patch("app.db.set_setting", side_effect=RuntimeError("БД заблокована")), \
         patch("subprocess.run", side_effect=lambda *a, **kw: (executed.append(1), _fake_run_success(*a, **kw))[1]):
        ok, msg = pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
            "тест", "успіх", "вимкнути", notify_fn=lambda t: None,
        )
    assert ok is True
    assert executed == [1]


def test_pending_signal_cleanup_failure_after_failed_action_does_not_raise(db_path):
    """Реальний edge case: команда провалилась (потрібно прибрати
    pending-сигнал), АЛЕ й саме прибирання сигналу теж провалюється -
    не має поширювати виняток назовні (вже й так неуспішний випадок,
    не варто робити його ще гіршим crash'ем)."""
    config.DISPLAY_ENABLED = False
    call_count = {"n": 0}

    def flaky_set_setting(key, value):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return  # перший виклик (запис pending) - успішний
        raise RuntimeError("БД заблокована при спробі очищення")

    with patch("app.db.set_setting", side_effect=flaky_set_setting), \
         patch("subprocess.run", side_effect=lambda *a, **kw: type("R", (), {"returncode": 1, "stderr": "провал"})()), \
         patch("app.telegram_notify.send_message"):
        ok, msg = pi_power.execute_pi_power_action(
            ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
            "тест", "успіх", "вимкнути", notify_fn=lambda t: None,
        )  # не мало кинути виняток
    assert ok is False


def test_run_system_command_timeout_returns_failure_not_exception():
    import subprocess
    from unittest.mock import patch
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("systemctl", 10)):
        assert pi_power.run_system_command(["sudo", "systemctl", "reboot"]) == (False, "timeout")


# ---- shutdown_from_button() (виключення по кнопці; спільне для shutdown_button і display) ----

def test_shutdown_from_button_calls_pi_power_with_poweroff_command(db_path):
    calls = []
    with patch("app.pi_power.execute_pi_power_action", side_effect=lambda *a, **kw: calls.append((a, kw))):
        pi_power.shutdown_from_button(27)

    assert len(calls) == 1
    args = calls[0][0]
    assert args[0] == ["sudo", "systemctl", "poweroff"]
    assert args[1] == "poweroff"
    assert "GPIO27" in args[3]


def test_shutdown_from_button_db_init_failure_does_not_block_poweroff():
    """Реальна мета: навіть якщо db.init_db() провалюється (напр.
    пошкоджена БД чи заповнена SD-картка), реальний poweroff МАЄ все
    одно виконатись - фізичне вимкнення важливіше за журналювання."""
    calls = []
    with patch("app.db.init_db", side_effect=RuntimeError("БД пошкоджена")), \
         patch("app.pi_power.execute_pi_power_action", side_effect=lambda *a, **kw: calls.append(a)):
        pi_power.shutdown_from_button(27)
    assert len(calls) == 1
