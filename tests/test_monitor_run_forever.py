"""Тести app/monitor.py:Watchdog.run_forever() — нескінченний цикл (poll dish/router/system_metrics +
періодичні prune/vacuum/integrity-check/backup). Зупиняється через SystemExit при mock time.sleep() (як
у test_shutdown_button.py). Час не треба mock-ати: усі таймери `last_X = 0.0` навмисно стартують з нуля,
тож `time.time() - 0 > поріг` істинне вже на першій ітерації.
"""
import contextlib
import time
from unittest.mock import MagicMock, patch

import pytest

from app import config, db, monitor


def _run_one_iteration(wd, poll_once_side_effect=None, sleep_side_effect=None):
    """Запускає run_forever() рівно одну ітерацію while-циклу -
    poll_once/telegram_bot/LED-ініціалізація mock-ані, time.sleep()
    кидає SystemExit одразу після першої ітерації."""
    with patch.object(wd, "poll_once", side_effect=poll_once_side_effect), \
         patch("app.telegram_bot.TelegramBot.start"), \
         patch("app.telegram_bot.TelegramBot.stop"), \
         patch("time.sleep", side_effect=sleep_side_effect or SystemExit()):
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
    with patch("app.services.check_db_integrity_and_notify") as mock_check:
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


def test_run_forever_starts_and_stops_background_sender(db_path):
    """Відправник працює лише всередині run_forever(); SystemExit (як з
    обробника SIGTERM) проходить через finally - потік зупинено, черга
    вичерпана, наступні _notify() знову синхронні."""
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    seen = {}
    def during_poll():
        seen["sender"] = wd._sender
        wd._notify("з циклу")
    sent = []
    with patch("app.telegram_notify.send_message", side_effect=lambda t: sent.append(t) or (True, "ok")):
        _run_one_iteration(wd, poll_once_side_effect=during_poll)
    assert seen["sender"] is not None
    assert not seen["sender"].is_alive()
    assert wd._sender is None
    assert sent == ["з циклу"]


def test_run_forever_holds_db_anchor_and_releases_it(db_path):
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    seen = {}
    _run_one_iteration(wd, poll_once_side_effect=lambda: seen.setdefault("anchor", db.__dict__["_anchor"]))
    assert seen["anchor"] is not None
    assert db._anchor is None


# ---- пауза між опитуваннями стійка до поганого значення в env ----

@pytest.mark.parametrize("bad", [0, -5, float("nan"), float("inf"), 0.5, 100000])
def test_poll_pause_falls_back_for_invalid_interval(bad, monkeypatch):
    """/settings перевіряє межі, але env можна змінити вручну: time.sleep()
    від'ємного/nan/inf валив процес монітора, нуль гнав цикл без пауз."""
    monkeypatch.setattr(config, "POLL_INTERVAL_SEC", bad)
    assert monitor._poll_pause() == 10.0


@pytest.mark.parametrize("ok", [1, 10, 3600])
def test_poll_pause_keeps_valid_interval(ok, monkeypatch):
    monkeypatch.setattr(config, "POLL_INTERVAL_SEC", ok)
    assert monitor._poll_pause() == ok


@pytest.mark.parametrize("bad", [-5, float("nan"), float("inf"), 0])
def test_run_forever_sleeps_sanely_and_warns_with_invalid_interval(db_path, monkeypatch, caplog, bad):
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    monkeypatch.setattr(config, "POLL_INTERVAL_SEC", bad)
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        raise SystemExit()

    with caplog.at_level("WARNING", logger="monitor"):
        _run_one_iteration(wd, sleep_side_effect=fake_sleep)
    assert slept == [10.0]
    assert "поза межами 1..3600" in caplog.text


# ---- РЕАЛЬНИЙ обробник SIGTERM і стійкість циклу до збою запису в БД ----

@contextlib.contextmanager
def _running_loop(wd, poll_once=None, sleep_effect=None, now=None, router_effect=None):
    """Один прохід run_forever(); патчі лишаються активними ВСЕРЕДИНІ with-блоку тесту (mock зупинки бота
    бачить виклик обробника). Обробник — той самий, що реєструє справжній код, не його копія в тесті.
    Повертає (обробники сигналів, mock зупинки бота, mock LED).
    """
    import signal
    handlers = {}
    led = MagicMock()
    led.init.return_value = True
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(wd, "poll_once", side_effect=poll_once))
        stack.enter_context(patch("app.telegram_bot.TelegramBot.start"))
        bot_stop = stack.enter_context(patch("app.telegram_bot.TelegramBot.stop"))
        stack.enter_context(patch("time.sleep", side_effect=sleep_effect or SystemExit()))
        stack.enter_context(patch("signal.signal", side_effect=lambda sig, h: handlers.__setitem__(sig, h)))
        stack.enter_context(patch("app.activity_led.ActivityLed", return_value=led))
        stack.enter_context(patch.object(wd, "poll_router", side_effect=router_effect))
        stack.enter_context(patch.object(wd, "_maybe_send_backup_to_telegram"))
        if now is not None:
            stack.enter_context(patch("time.time", side_effect=lambda: now["t"]))
        try:
            wd.run_forever()
        except SystemExit:
            pass
        assert signal.SIGTERM in handlers and signal.SIGINT in handlers
        yield handlers, bot_stop, led


def _buffered_status():
    from app.starlink_client import DishStatus
    return DishStatus(timestamp=time.time(), online=True, uptime_s=100, dish_id="sig-test").to_dict()


def test_real_sigterm_handler_flushes_buffer_closes_resources_and_exits(db_path, monkeypatch):
    import signal
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    monkeypatch.setattr(db, "_activity_callback", None)
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    with _running_loop(wd) as (handlers, bot_stop, led):
        assert db._activity_callback == led.blink      # led.init() -> True: колбек активності підключено
        wd.metrics_buffer.append(_buffered_status())
        with pytest.raises(SystemExit) as exc:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert exc.value.code == 0
        assert wd.metrics_buffer == [] and db.get_latest_metric() is not None
        led.close.assert_called_once()
        bot_stop.assert_called_once()


def test_real_sigterm_handler_still_exits_when_db_write_fails(db_path, monkeypatch):
    """Раніше збій flush у обробнику заважав вийти: виняток летів із
    обробника, а в poll_once() його ковтав би except - сервіс ігнорував би
    SIGTERM аж до SIGKILL. Тепер: SystemExit(0), ресурси закриті, дані лишились."""
    import signal
    import sqlite3
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    with _running_loop(wd) as (handlers, bot_stop, led):
        wd.metrics_buffer.append(_buffered_status())
        with patch("app.db.insert_metrics_batch", side_effect=sqlite3.OperationalError("database is locked")):
            with pytest.raises(SystemExit) as exc:
                handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert exc.value.code == 0
        led.close.assert_called_once()
        bot_stop.assert_called_once()
        assert len(wd.metrics_buffer) == 1              # нічого не втрачено - лишилось у пам'яті


def test_loop_survives_db_write_failure_when_all_timers_are_due(db_path, monkeypatch):
    """Періодичний flush стояв поза try: збій запису (БД заблокована, повна
    SD) валив увесь процес монітора - зависла тарілка не перезавантажувалась
    ніколи. Усі таймери цикла настали одночасно: цикл має дожити до паузи."""
    import sqlite3
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    now = {"t": time.time()}
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        raise SystemExit()

    def poll_and_jump():
        wd.metrics_buffer.append(_buffered_status())
        now["t"] += 10 ** 7                             # настав час УСІХ таймерів

    with patch("app.db.insert_metrics_batch", side_effect=sqlite3.OperationalError("database is locked")), \
         patch("app.db.prune_old", side_effect=sqlite3.OperationalError("database is locked")), \
         patch("app.db.vacuum_and_analyze", side_effect=sqlite3.OperationalError("database is locked")), \
         patch("app.services.check_db_integrity_and_notify", side_effect=sqlite3.OperationalError("database is locked")), \
         patch("app.services.perform_auto_backup", side_effect=OSError("диск повний")), \
         patch("app.db.insert_system_metric", side_effect=sqlite3.OperationalError("database is locked")):
        config.AUTO_BACKUP_ENABLED = True
        with _running_loop(wd, poll_once=poll_and_jump, sleep_effect=fake_sleep, now=now):
            pass
    assert slept == [monitor._poll_pause()]             # цикл дожив до паузи, не впав
    assert len(wd.metrics_buffer) == 1                  # дані лишились для повтору


def test_loop_runs_every_due_timer_once(db_path, monkeypatch):
    """Гілки, які жоден тест не виконував: усі періодичні задачі циклу."""
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    config.AUTO_BACKUP_ENABLED = True
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    now = {"t": time.time()}
    calls = []

    def poll_and_jump():
        wd.metrics_buffer.append(_buffered_status())
        now["t"] += 10 ** 7

    with patch("app.db.prune_old", side_effect=lambda *a, **k: calls.append("prune")), \
         patch("app.db.vacuum_and_analyze", side_effect=lambda: calls.append("vacuum")), \
         patch("app.services.check_db_integrity_and_notify", side_effect=lambda n: calls.append("integrity")), \
         patch("app.services.perform_auto_backup", side_effect=lambda: calls.append("backup")), \
         patch.object(wd, "poll_system_metrics", side_effect=lambda: calls.append("system")):
        with _running_loop(wd, poll_once=poll_and_jump, now=now):
            pass
    assert sorted(calls) == ["backup", "integrity", "prune", "system", "vacuum"]
    assert wd.metrics_buffer == [] and db.get_latest_metric() is not None      # пакетний запис теж відбувся


# ---- _Periodic: "якщо минув інтервал - виконати, не впавши" (замість 6 ручних блоків) ----

def _periodic(action, **kw):
    kw.setdefault("error", "помилка задачі")
    kw.setdefault("interval", 10)
    return monitor._Periodic(action, **kw)


def test_periodic_runs_on_first_call_then_waits_for_interval():
    """last=0 -> перше спрацювання одразу після старту; далі - лише через інтервал."""
    now = {"t": 1000.0}
    calls = []
    task = _periodic(lambda: calls.append(now["t"]), interval=10)
    with patch("time.time", side_effect=lambda: now["t"]):
        assert task.run_if_due() is True
        now["t"] += 5
        assert task.run_if_due() is False           # ще рано
        now["t"] += 6
        assert task.run_if_due() is True            # 11 с > 10 с
    assert calls == [1000.0, 1011.0]


def test_periodic_failure_is_logged_not_raised_and_retried_only_after_interval(caplog):
    now = {"t": 1000.0}
    attempts = []

    def boom():
        attempts.append(now["t"])
        raise RuntimeError("БД заблокована")

    task = _periodic(boom, interval=10, error="Помилка задачі X")
    with patch("time.time", side_effect=lambda: now["t"]), caplog.at_level("ERROR", logger="monitor"):
        assert task.run_if_due() is True            # НЕ кидає
        now["t"] += 1
        assert task.run_if_due() is False           # повтор - через інтервал, не щоітерації
    assert attempts == [1000.0]
    assert "Помилка задачі X" in caplog.text and "БД заблокована" in caplog.text


def test_periodic_interval_is_read_on_every_check_so_config_changes_apply():
    now = {"t": 1000.0}
    interval = {"v": 100}
    task = _periodic(lambda: None, interval=lambda: interval["v"])
    with patch("time.time", side_effect=lambda: now["t"]):
        task.run_if_due()
        now["t"] += 50
        assert task.run_if_due() is False
        interval["v"] = 10                           # /settings змінив інтервал
        assert task.run_if_due() is True


def test_periodic_with_interval_none_runs_every_time_even_with_frozen_clock():
    """Задача з власним таймером всередині (відправка backup у Telegram)."""
    calls = []
    task = _periodic(lambda: calls.append(1), interval=None)
    with patch("time.time", side_effect=lambda: 1000.0):
        for _ in range(3):
            assert task.run_if_due() is True
    assert len(calls) == 3


def test_periodic_disabled_task_does_not_run_or_touch_the_clock():
    task = _periodic(lambda: pytest.fail("не мала виконуватись"), enabled=lambda: False)
    with patch("time.time", side_effect=AssertionError("годинник не мав читатись")):
        assert task.run_if_due() is False


def test_periodic_nan_interval_never_fires():
    """Те саме порівняння, що й раніше: NaN -> умова хибна (не крах і не щоітерації)."""
    task = _periodic(lambda: pytest.fail("не мала виконуватись"), interval=float("nan"))
    task.last = 1.0
    assert task.run_if_due() is False


def test_periodic_tasks_keep_the_original_order():
    """Порядок мав значення (метрики Pi -> роутер -> prune -> VACUUM -> цілісність -> backup -> Telegram-backup)."""
    wd = monitor.Watchdog()
    errors = [t._error for t in wd._periodic_tasks()]
    assert errors == ["Помилка опитування метрик Pi", "Помилка опитування роутера", "Помилка очищення старих записів",
                      "Помилка VACUUM/ANALYZE", "Помилка перевірки цілісності БД", "Помилка автоматичного backup",
                      "Помилка відправки backup у Telegram"]


def test_loop_survives_unexpected_exception_in_router_and_system_metrics_polls(db_path, monkeypatch, caplog):
    """Єдина навмисна зміна поведінки: poll_system_metrics()/poll_router() стояли
    БЕЗ try - виняток звідти валив увесь цикл монітора."""
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    now = {"t": time.time()}
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        raise SystemExit()

    with patch.object(wd, "poll_system_metrics", side_effect=RuntimeError("psutil зламався")), \
         caplog.at_level("ERROR", logger="monitor"):
        with _running_loop(wd, poll_once=lambda: now.update(t=now["t"] + 10 ** 7), sleep_effect=fake_sleep, now=now,
                           router_effect=ValueError("роутер віддав сміття")):
            pass
    assert slept == [monitor._poll_pause()]                       # цикл дожив до паузи
    assert "Помилка опитування метрик Pi" in caplog.text and "Помилка опитування роутера" in caplog.text


# ---- зсув настінного годинника НАЗАД (ручна зміна часу, RTC, збій NTP) ----

def test_loop_runs_periodic_tasks_and_flush_after_clock_steps_backwards(db_path):
    """Раніше після зсуву на 2 год 6 з 7 періодичних задач і пакетний запис
    метрик мовчали, поки годинник не "наздожене" мітки з майбутнього."""
    config.ACTIVITY_LED_PIN = 0
    config.NOTIFY_PI_STARTUP = False
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    now = {"t": 2_000_000_000.0}
    real_tasks = wd._periodic_tasks

    def tasks_whose_last_run_is_in_the_future():
        tasks = real_tasks()
        for task in tasks:
            task.last = now["t"] + 7200          # виконувались "в майбутньому" відносно годинника після стрибка
        return tasks

    wd.last_batch_flush_ts = now["t"] + 7200
    with patch.object(wd, "_periodic_tasks", side_effect=tasks_whose_last_run_is_in_the_future), \
         patch.object(wd, "flush_metrics_buffer") as flush, patch.object(wd, "poll_system_metrics") as system_poll, \
         patch("app.db.prune_old") as prune:
        with _running_loop(wd, poll_once=lambda: None, now=now):
            pass
    assert system_poll.called and prune.called and flush.called


def test_periodic_task_runs_when_last_run_is_in_the_future():
    task = monitor._Periodic(lambda: calls.append(1), error="x", interval=3600)
    calls = []
    task.last = 5000.0
    with patch("time.time", side_effect=lambda: 1000.0):         # годинник на 4000 с НАЗАД відносно last
        assert task.run_if_due() is True
        assert task.last == 1000.0                               # мітка скинута на "зараз"
        assert task.run_if_due() is False                        # далі - звичайний інтервал
