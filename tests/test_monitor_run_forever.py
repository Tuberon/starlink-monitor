"""
Тести для app/monitor.py:Watchdog.run_forever() - головний нескінченний
цикл (poll dish/router/system_metrics + періодичні prune/vacuum/
integrity-check/backup). Зупиняється в тестах через SystemExit при
mock time.sleep() (та сама техніка, що tests/test_shutdown_button.py/
test_display_run_forever.py).

Реальний прогрес часу не потрібно mock-ати окремо: усі `last_X = 0.0`
таймери в run_forever() ініціалізуються нулем навмисно (щоб перша дія
відбулась одразу після старту сервісу) - `time.time() - 0 > поріг`
істинне вже на першій ітерації для будь-якого розумного порогу
(поточний Unix timestamp завжди значно більший за 86400/3600 тощо).
"""
from unittest.mock import patch

from app import config, db, monitor


def _run_one_iteration(wd, poll_once_side_effect=None):
    """Запускає run_forever() рівно одну ітерацію while-циклу -
    poll_once/telegram_bot/LED-ініціалізація mock-ані, time.sleep()
    кидає SystemExit одразу після першої ітерації."""
    with patch.object(wd, "poll_once", side_effect=poll_once_side_effect), \
         patch("app.telegram_bot.TelegramBot.start"), \
         patch("app.telegram_bot.TelegramBot.stop"), \
         patch("time.sleep", side_effect=SystemExit()):
        try:
            wd.run_forever()
        except SystemExit:
            pass


def test_run_forever_calls_poll_once(db_path):
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    calls = []
    _run_one_iteration(wd, poll_once_side_effect=lambda: calls.append(1))
    assert calls == [1]


def test_run_forever_poll_once_exception_does_not_crash_loop(db_path):
    """Реальна мета: неочікувана помилка всередині poll_once() (напр.
    dish повернув щось несподіване) НЕ має завершити весь watchdog-
    процес - цикл продовжується до наступної ітерації."""
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    _run_one_iteration(wd, poll_once_side_effect=RuntimeError("несподівана помилка"))
    # Якщо дійшли сюди без непійманого RuntimeError - цикл реально
    # проковтнув помилку і продовжив до time.sleep()/SystemExit.


def test_run_forever_polls_system_metrics_on_first_iteration(db_path):
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    _run_one_iteration(wd)
    assert db.get_latest_system_metric() is not None


def test_run_forever_polls_router_on_first_iteration(db_path):
    from app.starlink_client import RouterInfo
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    info = RouterInfo(timestamp=1000.0, online=True, software_version="r1")
    with patch.object(wd.client, "get_router_info", return_value=info):
        _run_one_iteration(wd)
    assert db.get_router_status()["software_version"] == "r1"


def test_run_forever_runs_vacuum_on_first_iteration(db_path):
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    with patch("app.db.vacuum_and_analyze") as mock_vacuum:
        _run_one_iteration(wd)
    mock_vacuum.assert_called_once()


def test_run_forever_runs_integrity_check_on_first_iteration(db_path):
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    with patch("app.monitor.check_db_integrity_and_notify") as mock_check:
        _run_one_iteration(wd)
    mock_check.assert_called_once()


def test_run_forever_runs_auto_backup_when_enabled(db_path, tmp_path):
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    config.AUTO_BACKUP_ENABLED = True
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    _run_one_iteration(wd)
    import os
    assert os.path.exists(config.AUTO_BACKUP_DIR)
    assert len(os.listdir(config.AUTO_BACKUP_DIR)) == 1


def test_run_forever_skips_auto_backup_when_disabled(db_path, tmp_path):
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    config.AUTO_BACKUP_ENABLED = False
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups-disabled")
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    _run_one_iteration(wd)
    import os
    assert not os.path.exists(config.AUTO_BACKUP_DIR)


def test_run_forever_notifies_startup_after_real_reboot(db_path):
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = True
    wd = monitor.Watchdog()
    sent = []
    wd._notify = lambda t: sent.append(t)
    with patch("app.monitor.pi_just_booted", return_value=True):
        _run_one_iteration(wd)
    assert any("перезавантажено" in s for s in sent)


def test_run_forever_no_startup_notification_on_plain_restart(db_path):
    """systemctl restart (не реальний reboot Pi) - pi_just_booted()
    поверне False, сповіщення НЕ має надсилатись."""
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = True
    wd = monitor.Watchdog()
    sent = []
    wd._notify = lambda t: sent.append(t)
    with patch("app.monitor.pi_just_booted", return_value=False):
        _run_one_iteration(wd)
    assert sent == []
