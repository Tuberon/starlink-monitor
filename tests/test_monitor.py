"""Тести app/monitor.py — найкрихкіша stateful-логіка: групування reboot-спаму (часове вікно), дедублікація
сповіщень про target-версії (порівняння зі збереженим значенням, не boolean-прапорець). Сценарії
відтворюють ті, що раніше перевірялись ad-hoc живими тестами.
"""
import os
import time
from unittest.mock import patch


from app import config, db, monitor, starlink_client


# ---- Визначення "Pi щойно завантажився" (pi_just_booted) ----

def test_pi_just_booted_true_when_recently_started():
    with patch("time.time", return_value=1000.0), patch("psutil.boot_time", return_value=990.0):
        assert monitor.pi_just_booted() is True


def test_pi_just_booted_false_when_running_long():
    with patch("time.time", return_value=1000.0), patch("psutil.boot_time", return_value=1000.0 - 3600):
        assert monitor.pi_just_booted() is False


def test_pi_just_booted_respects_custom_threshold():
    with patch("time.time", return_value=1000.0), patch("psutil.boot_time", return_value=1000.0 - 200):
        assert monitor.pi_just_booted(threshold_sec=120.0) is False
        assert monitor.pi_just_booted(threshold_sec=300.0) is True


# ---- Групування спаму reboot-сповіщень (_notify_reboot) ----

def test_reboot_notify_below_threshold_sends_normally(watchdog):
    """Менше REBOOT_SPAM_THRESHOLD сповіщень поспіль - надсилаються
    без групування."""
    config.REBOOT_SPAM_THRESHOLD = 3
    with patch("time.time", return_value=1000.0):
        watchdog._notify_reboot("🔁 reboot 1")
    with patch("time.time", return_value=1100.0):
        watchdog._notify_reboot("🔁 reboot 2")
    assert watchdog.sent == ["🔁 reboot 1", "🔁 reboot 2"]


def test_reboot_notify_threshold_reached_mutes_silently(watchdog):
    """Досягнення порогу - подальші reboot-сповіщення приглушуються
    МОВЧКИ, без окремого попередження про початок групування (щоб не
    додавати ще одне сповіщення до вже частих)."""
    config.REBOOT_SPAM_THRESHOLD = 3
    config.REBOOT_SPAM_WINDOW_SEC = 1800
    now = 1000.0
    for i in range(3):
        with patch("time.time", return_value=now + i * 100):
            watchdog._notify_reboot(f"🔁 reboot {i}")
    # Лише перші 2 (до порогу) реально надіслані - 3-й (що досяг
    # порогу) НЕ надсилає жодного повідомлення взагалі.
    assert watchdog.sent == ["🔁 reboot 0", "🔁 reboot 1"]
    assert watchdog.reboot_spam_muted is True
    assert watchdog.muted_reboot_count == 1


def test_reboot_notify_further_reboots_silently_counted(watchdog):
    """Після досягнення порогу - подальші reboot НЕ надсилають нових
    Telegram-повідомлень, лише тихо рахуються (muted_reboot_count)."""
    config.REBOOT_SPAM_THRESHOLD = 3
    now = 1000.0
    for i in range(3):
        with patch("time.time", return_value=now + i * 100):
            watchdog._notify_reboot(f"🔁 reboot {i}")
    count_before = len(watchdog.sent)

    with patch("time.time", return_value=now + 300):
        watchdog._notify_reboot("🔁 reboot 4")
    with patch("time.time", return_value=now + 400):
        watchdog._notify_reboot("🔁 reboot 5")

    assert len(watchdog.sent) == count_before  # без нових повідомлень
    assert watchdog.muted_reboot_count == 3  # 1 (перше) + 2 нових


def test_reboot_spam_recovery_sends_summary_and_resets(watchdog):
    """Після затишшя (> REBOOT_SPAM_WINDOW_SEC без нового reboot) -
    підсумкове повідомлення, і стан повністю скидається."""
    config.REBOOT_SPAM_THRESHOLD = 3
    config.REBOOT_SPAM_WINDOW_SEC = 1800
    now = 1000.0
    for i in range(3):
        with patch("time.time", return_value=now + i * 100):
            watchdog._notify_reboot(f"🔁 reboot {i}")
    assert watchdog.reboot_spam_muted is True
    # muted_reboot_count рахує ЛИШЕ reboot ПІСЛЯ активації групування
    # (включно з тим, що її активував) - ще 2 після порогу дають 3.
    with patch("time.time", return_value=now + 300):
        watchdog._notify_reboot("🔁 reboot 3")
    with patch("time.time", return_value=now + 400):
        watchdog._notify_reboot("🔁 reboot 4")

    with patch("time.time", return_value=now + 400 + config.REBOOT_SPAM_WINDOW_SEC + 10):
        watchdog._check_reboot_spam_recovery()

    assert "припинились" in watchdog.sent[-1]
    assert "3" in watchdog.sent[-1]
    assert watchdog.reboot_spam_muted is False
    assert watchdog.reboot_notify_ts == []


def test_reboot_spam_new_cycle_after_recovery_behaves_normally(watchdog):
    """Після recovery - новий reboot знову надсилається нормально
    (стан справді скинутий, не залишковий muted-режим)."""
    config.REBOOT_SPAM_THRESHOLD = 3
    config.REBOOT_SPAM_WINDOW_SEC = 1800
    now = 1000.0
    for i in range(3):
        with patch("time.time", return_value=now + i * 100):
            watchdog._notify_reboot(f"🔁 reboot {i}")
    with patch("time.time", return_value=now + 200 + config.REBOOT_SPAM_WINDOW_SEC + 10):
        watchdog._check_reboot_spam_recovery()

    with patch("time.time", return_value=now + 200 + config.REBOOT_SPAM_WINDOW_SEC + 20):
        watchdog._notify_reboot("🔁 reboot новий цикл")

    assert watchdog.sent[-1] == "🔁 reboot новий цикл"


# ---- Watchdog auto-reboot при недоступності dish (_maybe_reboot) ----

def test_maybe_reboot_below_failure_threshold_does_nothing(watchdog):
    """Менше MAX_CONSECUTIVE_FAILURES невдалих спроб - жодного reboot."""
    watchdog.consecutive_failures = config.MAX_CONSECUTIVE_FAILURES - 1
    with patch.object(watchdog.client, "reboot_dish") as mock_reboot:
        watchdog._maybe_reboot()
        mock_reboot.assert_not_called()
    assert watchdog.last_reboot_ts == 0.0


def test_maybe_reboot_respects_min_interval(watchdog):
    """Досягнуто поріг невдач, АЛЕ MIN_REBOOT_INTERVAL_SEC ще не минув
    з попереднього reboot - захист від reboot-loop, пропускає."""
    watchdog.consecutive_failures = config.MAX_CONSECUTIVE_FAILURES
    with patch("time.time", return_value=1000.0):
        watchdog.last_reboot_ts = 1000.0 - (config.MIN_REBOOT_INTERVAL_SEC - 10)
        with patch.object(watchdog.client, "reboot_dish") as mock_reboot:
            watchdog._maybe_reboot()
            mock_reboot.assert_not_called()


def test_maybe_reboot_triggers_reboot_and_resets_failures(watchdog):
    """Поріг досягнуто, достатньо часу минуло з попереднього reboot -
    реально викликає client.reboot_dish(), оновлює last_reboot_ts,
    скидає consecutive_failures при успіху (той самий сценарій, що в
    оригінальній пропозиції: 6 невдач -> reboot)."""
    watchdog.consecutive_failures = config.MAX_CONSECUTIVE_FAILURES
    watchdog.last_reboot_ts = 0.0
    with patch("time.time", return_value=10_000.0):
        with patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as mock_reboot:
            watchdog._maybe_reboot()
            mock_reboot.assert_called_once()
    assert watchdog.last_reboot_ts == 10_000.0
    assert watchdog.consecutive_failures == 0


def test_maybe_reboot_failed_attempt_keeps_failure_count(watchdog):
    """Невдала спроба reboot (grpcurl провалився) - last_reboot_ts і
    далі оновлюється (щоб не спамити reboot-спробами щоцикл), АЛЕ
    consecutive_failures НЕ скидається (dish і далі недоступний)."""
    watchdog.consecutive_failures = config.MAX_CONSECUTIVE_FAILURES
    watchdog.last_reboot_ts = 0.0
    with patch("time.time", return_value=10_000.0):
        with patch.object(watchdog.client, "reboot_dish", return_value=(False, "timeout")):
            watchdog._maybe_reboot()
    assert watchdog.last_reboot_ts == 10_000.0
    assert watchdog.consecutive_failures == config.MAX_CONSECUTIVE_FAILURES


# ---- Вимкнення сповіщення "Dish знову online" (STARLINK_NOTIFY_DISH_RECOVERY) ----

def test_dish_recovery_notification_sent_by_default(watchdog):
    from app.starlink_client import DishStatus
    config.NOTIFY_DISH_RECOVERY = True
    watchdog.consecutive_failures = 3
    watchdog.first_failure_ts = time.time() - 10
    status = DishStatus(timestamp=time.time(), online=True, uptime_s=100, dish_id="dish1",
                         hardware_version="rev3", software_version="v1")
    watchdog.client.get_status = lambda: status
    watchdog.poll_once()
    recovery_msgs = [s for s in watchdog.sent if "знову online" in s]
    assert len(recovery_msgs) == 1


def test_dish_recovery_notification_disabled_via_config(watchdog):
    """Основний сценарій запиту користувача: NOTIFY_DISH_RECOVERY=False
    повністю вимикає це сповіщення, незалежно від muted/unmuted гілки."""
    from app.starlink_client import DishStatus
    config.NOTIFY_DISH_RECOVERY = False
    try:
        watchdog.consecutive_failures = 3
        watchdog.first_failure_ts = time.time() - 10
        status = DishStatus(timestamp=time.time(), online=True, uptime_s=100, dish_id="dish1",
                             hardware_version="rev3", software_version="v1")
        watchdog.client.get_status = lambda: status
        watchdog.poll_once()
        recovery_msgs = [s for s in watchdog.sent if "знову online" in s]
        assert recovery_msgs == []
    finally:
        config.NOTIFY_DISH_RECOVERY = True  # не "протікати" в інші тести


# ---- Буферизація dish-метрик (SD-card-wear reduction) ----

def test_poll_once_buffers_instead_of_writing_immediately(watchdog):
    """Реальна мета зміни: замість негайного окремого запису кожні
    10с, зчитування накопичуються в пам'яті - БД лишається порожньою
    до явного flush."""
    from app.starlink_client import DishStatus
    status = DishStatus(timestamp=time.time(), online=True, uptime_s=100, dish_id="d1",
                         hardware_version="rev3", software_version="v1")
    with patch.object(watchdog.client, "get_status", return_value=status):
        watchdog.poll_once()
        watchdog.poll_once()

    assert len(watchdog.metrics_buffer) == 2
    assert db.get_latest_metric() is None, "БД мала лишатись порожньою до flush"


def test_flush_metrics_buffer_writes_all_and_clears_buffer(watchdog):
    from app.starlink_client import DishStatus
    status = DishStatus(timestamp=time.time(), online=True, uptime_s=100, dish_id="d1",
                         hardware_version="rev3", software_version="v1")
    watchdog.metrics_buffer = [status.to_dict(), status.to_dict(), status.to_dict()]

    watchdog.flush_metrics_buffer()

    assert watchdog.metrics_buffer == []
    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) as c FROM metrics").fetchone()["c"]
    assert count == 3, "усі буферизовані записи мали потрапити в БД одним flush"


def test_flush_empty_buffer_does_not_error(watchdog):
    """Порожній буфер - flush не має падати чи писати порожній рядок."""
    watchdog.metrics_buffer = []
    watchdog.flush_metrics_buffer()
    assert db.get_latest_metric() is None


# ---- _notifications_muted() ----

def test_notifications_not_muted_when_no_failures(watchdog):
    watchdog.first_failure_ts = None
    assert watchdog._notifications_muted() is False


def test_notifications_not_muted_before_threshold(watchdog):
    config.NOTIFICATIONS_MUTE_AFTER_SEC = 900
    watchdog.first_failure_ts = time.time() - 100
    assert watchdog._notifications_muted() is False


def test_notifications_muted_after_threshold(watchdog):
    config.NOTIFICATIONS_MUTE_AFTER_SEC = 900
    watchdog.first_failure_ts = time.time() - 1000
    assert watchdog._notifications_muted() is True


# ---- poll_system_metrics() ----

def test_poll_system_metrics_writes_to_db(watchdog):
    watchdog.poll_system_metrics()
    latest = db.get_latest_system_metric()
    assert latest is not None


def test_poll_system_metrics_error_does_not_raise(watchdog):
    with patch("app.monitor.get_system_metrics", side_effect=RuntimeError("psutil помилка")):
        watchdog.poll_system_metrics()  # не мало кинути виняток


# ---- _log_update_state_change() ----

def test_log_update_state_change_first_call_idle_is_silent(watchdog):
    from app.starlink_client import DishStatus
    status = DishStatus(timestamp=time.time(), online=True, update_state="IDLE")
    watchdog._log_update_state_change(status)
    events = db.get_recent_events(10)
    assert events == []


def test_log_update_state_change_first_call_non_idle_is_logged(watchdog):
    from app.starlink_client import DishStatus
    status = DishStatus(timestamp=time.time(), online=True, update_state="FETCHING")
    watchdog._log_update_state_change(status)
    events = db.get_recent_events(10)
    assert len(events) == 1


def test_log_update_state_change_reboot_required_notifies(watchdog):
    from app.starlink_client import DishStatus
    watchdog.prev_update_state = "PRE_CHECK"
    status = DishStatus(timestamp=time.time(), online=True, update_state="REBOOT_REQUIRED")
    watchdog._log_update_state_change(status)
    assert any("готове" in s for s in watchdog.sent)


def test_log_update_state_change_faulted_notifies(watchdog):
    from app.starlink_client import DishStatus
    watchdog.prev_update_state = "WRITING"
    status = DishStatus(timestamp=time.time(), online=True, update_state="FAULTED")
    watchdog._log_update_state_change(status)
    assert any("Помилка оновлення" in s for s in watchdog.sent)
    events = db.get_recent_events(10)
    assert events[0]["success"] == 0


def test_log_update_state_change_download_started_notifies(watchdog):
    from app.starlink_client import DishStatus
    watchdog.prev_update_state = "IDLE"
    status = DishStatus(timestamp=time.time(), online=True, update_state="FETCHING", update_progress_pct=10.0)
    watchdog._log_update_state_change(status)
    assert any("Розпочато оновлення" in s for s in watchdog.sent)


def test_log_update_state_change_completed_notifies(watchdog):
    from app.starlink_client import DishStatus
    watchdog.prev_update_state = "WRITING"
    status = DishStatus(timestamp=time.time(), online=True, update_state="IDLE")
    watchdog._log_update_state_change(status)
    assert any("завершено" in s for s in watchdog.sent)


def test_log_update_state_change_same_state_is_silent(watchdog):
    from app.starlink_client import DishStatus
    watchdog.prev_update_state = "FETCHING"
    status = DishStatus(timestamp=time.time(), online=True, update_state="FETCHING")
    watchdog._log_update_state_change(status)
    events = db.get_recent_events(10)
    assert events == []


# ---- _log_alerts_change() ----

def test_log_alerts_change_first_call_does_not_notify(watchdog):
    from app.starlink_client import DishStatus
    watchdog.prev_alerts = None
    status = DishStatus(timestamp=time.time(), online=True, active_alerts=["thermal_throttle"])
    watchdog._log_alerts_change(status)
    assert watchdog.sent == []
    assert watchdog.prev_alerts == {"thermal_throttle"}


def test_log_alerts_change_new_alert_notifies(watchdog):
    from app.starlink_client import DishStatus
    watchdog.prev_alerts = set()
    status = DishStatus(timestamp=time.time(), online=True, active_alerts=["thermal_throttle"])
    watchdog._log_alerts_change(status)
    assert any("Нове попередження" in s for s in watchdog.sent)


def test_log_alerts_change_muted_alert_logs_but_no_notify(watchdog):
    from app.starlink_client import DishStatus
    """roaming - у MUTED_DISH_ALERTS: подія пишеться в журнал (для
    дашборду), але Telegram-сповіщення НЕ надсилається (шумний
    сценарій для звичайного використання)."""
    watchdog.prev_alerts = set()
    status = DishStatus(timestamp=time.time(), online=True, active_alerts=["roaming"])
    watchdog._log_alerts_change(status)
    assert watchdog.sent == []
    events = db.get_recent_events(10)
    assert len(events) == 1


def test_log_alerts_change_resolved_alert_logs_event(watchdog):
    from app.starlink_client import DishStatus
    watchdog.prev_alerts = {"thermal_throttle"}
    status = DishStatus(timestamp=time.time(), online=True, active_alerts=[])
    watchdog._log_alerts_change(status)
    events = db.get_recent_events(10)
    assert any(e["kind"] == "dish_alert_resolved" for e in events)


def test_log_alerts_change_obstruction_map_reset_resolved_not_logged(watchdog):
    """Реальна мета: Starlink періодично скидає карту перешкод сам по
    собі як частину нормальної роботи (не аварійна подія) - зникнення
    цього конкретного alert'а НЕ має створювати запис у журналі,
    на відміну від решти resolved-алертів."""
    from app.starlink_client import DishStatus
    watchdog.prev_alerts = {"obstruction_map_reset"}
    status = DishStatus(timestamp=time.time(), online=True, active_alerts=[])
    watchdog._log_alerts_change(status)
    events = db.get_recent_events(10)
    assert events == []


def test_log_alerts_change_obstruction_map_reset_does_not_block_other_alerts(watchdog):
    """Контрольний тест: точкове виключення obstruction_map_reset НЕ
    має вплинути на журналювання ІНШИХ resolved-алертів у тій самій
    групі змін."""
    from app.starlink_client import DishStatus
    watchdog.prev_alerts = {"obstruction_map_reset", "thermal_throttle"}
    status = DishStatus(timestamp=time.time(), online=True, active_alerts=[])
    watchdog._log_alerts_change(status)
    events = db.get_recent_events(10)
    assert len(events) == 1
    assert "обмеження через перегрів" in events[0]["message"]
    assert not any("карта перешкод" in e["message"] for e in events)


# ---- poll_router() ----

def test_poll_router_online_updates_status(watchdog):
    from app.starlink_client import RouterInfo
    info = RouterInfo(timestamp=time.time(), online=True, software_version="r1")
    with patch.object(watchdog.client, "get_router_info", return_value=info):
        watchdog.poll_router()
    assert db.get_router_status()["software_version"] == "r1"


def test_poll_router_offline_does_not_raise(watchdog):
    from app.starlink_client import RouterInfo
    info = RouterInfo(timestamp=time.time(), online=False, error="timeout")
    with patch.object(watchdog.client, "get_router_info", return_value=info):
        watchdog.poll_router()  # не мало кинути виняток


def test_poll_router_exception_does_not_raise(watchdog):
    with patch.object(watchdog.client, "get_router_info", side_effect=RuntimeError("мережа впала")):
        watchdog.poll_router()  # не мало кинути виняток


def test_log_router_update_state_first_call_not_run_is_silent(watchdog):
    from app.starlink_client import RouterInfo
    info = RouterInfo(timestamp=time.time(), online=True, update_state=None)
    watchdog._log_router_update_state_change(info)
    assert db.get_recent_events(10) == []


def test_log_router_update_state_reboot_pending_notifies(watchdog):
    from app.starlink_client import RouterInfo
    watchdog.prev_router_update_state = "DOWNLOADING_UPDATE_IMAGE"
    info = RouterInfo(timestamp=time.time(), online=True, update_state="REBOOT_PENDING")
    watchdog._log_router_update_state_change(info)
    assert any("готове" in s for s in watchdog.sent)


def test_log_router_update_state_failure_notifies_and_logs_failure(watchdog):
    # FLASHING_FAILED, не DOWNLOADING_UPDATE_IMAGE_FAILED: останній свідомо
    # прихований (HIDDEN_ROUTER_UPDATE_STATES) - див. окремий тест нижче
    from app.starlink_client import RouterInfo
    watchdog.prev_router_update_state = "FLASHING"
    info = RouterInfo(timestamp=time.time(), online=True, update_state="FLASHING_FAILED")
    watchdog._log_router_update_state_change(info)
    assert any("Помилка оновлення" in s for s in watchdog.sent)
    events = db.get_recent_events(10)
    assert events[0]["success"] == 0


def test_log_router_update_state_muted_failure_does_not_notify(watchdog):
    """GETTING_TARGET_VERSION_FAILED - у MUTED_ROUTER_UPDATE_STATES:
    подія пишеться, але Telegram-сповіщення НЕ надсилається."""
    from app.starlink_client import RouterInfo
    watchdog.prev_router_update_state = "NOT_RUN"
    info = RouterInfo(timestamp=time.time(), online=True, update_state="GETTING_TARGET_VERSION_FAILED")
    watchdog._log_router_update_state_change(info)
    assert watchdog.sent == []
    assert len(db.get_recent_events(10)) == 1


# ---- _log_router_alerts_change() ----

def test_log_router_alerts_new_alert_notifies(watchdog):
    from app.starlink_client import RouterInfo
    watchdog.prev_router_alerts = set()
    info = RouterInfo(timestamp=time.time(), online=True, active_alerts=["thermal_shutdown"])
    watchdog._log_router_alerts_change(info)
    assert any("Нове попередження роутера" in s for s in watchdog.sent)


def test_log_router_alerts_muted_alert_no_notify(watchdog):
    from app.starlink_client import RouterInfo
    watchdog.prev_router_alerts = set()
    info = RouterInfo(timestamp=time.time(), online=True, active_alerts=["install_pending"])
    watchdog._log_router_alerts_change(info)
    assert watchdog.sent == []
    assert len(db.get_recent_events(10)) == 1


def test_log_router_alerts_resolved_logs_event(watchdog):
    from app.starlink_client import RouterInfo
    watchdog.prev_router_alerts = {"thermal_shutdown"}
    info = RouterInfo(timestamp=time.time(), online=True, active_alerts=[])
    watchdog._log_router_alerts_change(info)
    events = db.get_recent_events(10)
    assert any(e["kind"] == "router_alert_resolved" for e in events)


# ---- _reboot_for_update_ready() / _maybe_reboot_for_update() / _maybe_reboot_for_router_update() ----

def test_reboot_for_update_ready_respects_min_interval(watchdog):
    watchdog.last_reboot_ts = time.time()
    config.MIN_REBOOT_INTERVAL_SEC = 180
    with patch.object(watchdog.client, "reboot_dish") as mock_reboot:
        watchdog._reboot_for_update_ready("dish", "REBOOT_REQUIRED")
    mock_reboot.assert_not_called()


def test_reboot_for_update_ready_success_notifies(watchdog):
    watchdog.last_reboot_ts = 0
    with patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")):
        watchdog._reboot_for_update_ready("dish", "REBOOT_REQUIRED")
    assert any("автоматично перезавантажено" in s for s in watchdog.sent)


def test_reboot_for_update_ready_failure_notifies_and_updates_ts(db_path):
    """last_reboot_ts МАЄ оновитись НАВІТЬ при провалі reboot -
    захист від reboot-loop (той самий принцип, що в _maybe_reboot)."""
    from app.monitor import Watchdog
    wd = Watchdog()
    wd.sent = []
    wd._notify = lambda t: wd.sent.append(t)
    wd.last_reboot_ts = 0
    with patch.object(wd.client, "reboot_dish", return_value=(False, "timeout")):
        wd._reboot_for_update_ready("dish", "REBOOT_REQUIRED")
    assert any("Не вдалося" in s for s in wd.sent)
    assert wd.last_reboot_ts > 0


def test_maybe_reboot_for_update_disabled_does_nothing(watchdog):
    from app.starlink_client import DishStatus
    db.set_auto_reboot_enabled(False)
    status = DishStatus(timestamp=time.time(), online=True, update_state="REBOOT_REQUIRED")
    with patch.object(watchdog, "_reboot_for_update_ready") as mock_reboot:
        watchdog._maybe_reboot_for_update(status)
    mock_reboot.assert_not_called()


def test_maybe_reboot_for_update_triggers_when_ready(watchdog):
    from app.starlink_client import DishStatus
    db.set_auto_reboot_enabled(True)
    status = DishStatus(timestamp=time.time(), online=True, update_state="REBOOT_REQUIRED")
    with patch.object(watchdog, "_reboot_for_update_ready") as mock_reboot:
        watchdog._maybe_reboot_for_update(status)
    mock_reboot.assert_called_once_with("dish", "REBOOT_REQUIRED")


def test_maybe_reboot_for_router_update_triggers_when_ready(watchdog):
    from app.starlink_client import RouterInfo
    db.set_auto_reboot_enabled(True)
    info = RouterInfo(timestamp=time.time(), online=True, update_state="REBOOT_PENDING")
    with patch.object(watchdog, "_reboot_for_update_ready") as mock_reboot:
        watchdog._maybe_reboot_for_router_update(info)
    mock_reboot.assert_called_once_with("router", "REBOOT_PENDING")


def test_maybe_reboot_for_router_update_install_pending_triggers(watchdog):
    from app.starlink_client import RouterInfo
    db.set_auto_reboot_enabled(True)
    info = RouterInfo(timestamp=time.time(), online=True, update_state=None, update_install_pending=True)
    with patch.object(watchdog, "_reboot_for_update_ready") as mock_reboot:
        watchdog._maybe_reboot_for_router_update(info)
    mock_reboot.assert_called_once_with("router", "install_pending")


# ---- poll_once() ----

def test_poll_once_recovery_after_long_downtime_shows_duration(watchdog):
    from app.starlink_client import DishStatus
    config.NOTIFICATIONS_MUTE_AFTER_SEC = 900
    watchdog.consecutive_failures = 5
    watchdog.first_failure_ts = time.time() - 1000  # довше за MUTE_AFTER
    status = DishStatus(timestamp=time.time(), online=True, uptime_s=100)
    with patch.object(watchdog.client, "get_status", return_value=status):
        watchdog.poll_once()
    assert any("хв" in s and "відновлено" in s for s in watchdog.sent)


def test_poll_once_recovery_short_downtime_shows_attempt_count(watchdog):
    config.NOTIFICATIONS_MUTE_AFTER_SEC = 900
    from app.starlink_client import DishStatus
    watchdog.consecutive_failures = 3
    watchdog.first_failure_ts = time.time() - 10
    status = DishStatus(timestamp=time.time(), online=True, uptime_s=100)
    with patch.object(watchdog.client, "get_status", return_value=status):
        watchdog.poll_once()
    assert any("невдалих спроб" in s for s in watchdog.sent)


def test_poll_once_recovery_disabled_via_config(watchdog):
    from app.starlink_client import DishStatus
    config.NOTIFY_DISH_RECOVERY = False
    watchdog.consecutive_failures = 3
    watchdog.first_failure_ts = time.time() - 10
    status = DishStatus(timestamp=time.time(), online=True, uptime_s=100)
    try:
        with patch.object(watchdog.client, "get_status", return_value=status):
            watchdog.poll_once()
        assert watchdog.sent == []
    finally:
        config.NOTIFY_DISH_RECOVERY = True


def test_poll_once_offline_increments_failures_and_calls_maybe_reboot(watchdog):
    from app.starlink_client import DishStatus
    status = DishStatus(timestamp=time.time(), online=False, error="timeout")
    with patch.object(watchdog.client, "get_status", return_value=status), \
         patch.object(watchdog, "_maybe_reboot") as mock_maybe_reboot:
        watchdog.poll_once()
    assert watchdog.consecutive_failures == 1
    assert watchdog.first_failure_ts is not None
    mock_maybe_reboot.assert_called_once()


def test_poll_once_obstruction_warning_logs_event(watchdog):
    from app.starlink_client import DishStatus
    config.OBSTRUCTION_WARN_FRACTION = 0.05
    status = DishStatus(timestamp=time.time(), online=True, uptime_s=100, obstruction_fraction=0.5)
    with patch.object(watchdog.client, "get_status", return_value=status):
        watchdog.poll_once()
    events = db.get_recent_events(10)
    assert any(e["kind"] == "obstruction_warning" for e in events)


# ---- _maybe_send_backup_to_telegram() ----

def test_maybe_send_backup_disabled_does_nothing(watchdog, tmp_path):
    config.TELEGRAM_BACKUP_ENABLED = False
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    with patch("app.telegram_notify.send_document") as mock_send:
        watchdog._maybe_send_backup_to_telegram()
    mock_send.assert_not_called()


def test_maybe_send_backup_before_interval_does_nothing(watchdog, tmp_path):
    config.TELEGRAM_BACKUP_ENABLED = True
    config.TELEGRAM_BACKUP_INTERVAL_HOURS = 168
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    watchdog.last_telegram_backup_sent_ts = time.time()  # щойно
    with patch("app.telegram_notify.send_document") as mock_send:
        watchdog._maybe_send_backup_to_telegram()
    mock_send.assert_not_called()


def test_maybe_send_backup_no_directory_does_not_crash(watchdog, tmp_path):
    config.TELEGRAM_BACKUP_ENABLED = True
    config.AUTO_BACKUP_DIR = str(tmp_path / "nonexistent-backups")
    watchdog.last_telegram_backup_sent_ts = 0
    with patch("app.telegram_notify.send_document") as mock_send:
        watchdog._maybe_send_backup_to_telegram()  # не мало кинути виняток
    mock_send.assert_not_called()


def test_maybe_send_backup_no_files_does_not_crash(watchdog, tmp_path):
    config.TELEGRAM_BACKUP_ENABLED = True
    config.AUTO_BACKUP_DIR = str(tmp_path / "empty-backups")
    os.makedirs(config.AUTO_BACKUP_DIR)
    watchdog.last_telegram_backup_sent_ts = 0
    with patch("app.telegram_notify.send_document") as mock_send:
        watchdog._maybe_send_backup_to_telegram()
    mock_send.assert_not_called()


def test_maybe_send_backup_sends_newest_file(watchdog, tmp_path):
    config.TELEGRAM_BACKUP_ENABLED = True
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    os.makedirs(config.AUTO_BACKUP_DIR)
    watchdog.last_telegram_backup_sent_ts = 0

    old_path = os.path.join(config.AUTO_BACKUP_DIR, "backup-1000.json")
    new_path = os.path.join(config.AUTO_BACKUP_DIR, "backup-2000.json")
    with open(old_path, "w") as f:
        f.write("{}")
    os.utime(old_path, (1000, 1000))
    with open(new_path, "w") as f:
        f.write("{}")
    os.utime(new_path, (2000, 2000))

    with patch("app.telegram_notify.send_document", return_value=(True, "надіслано")) as mock_send:
        watchdog._maybe_send_backup_to_telegram()

    mock_send.assert_called_once()
    assert mock_send.call_args[0][0] == new_path


def test_maybe_send_backup_logs_event_on_success(watchdog, tmp_path):
    config.TELEGRAM_BACKUP_ENABLED = True
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    os.makedirs(config.AUTO_BACKUP_DIR)
    watchdog.last_telegram_backup_sent_ts = 0
    with open(os.path.join(config.AUTO_BACKUP_DIR, "backup-1.json"), "w") as f:
        f.write("{}")

    with patch("app.telegram_notify.send_document", return_value=(True, "надіслано")):
        watchdog._maybe_send_backup_to_telegram()

    events = db.get_recent_events(10)
    assert any(e["kind"] == "telegram_backup_sent" for e in events)


def test_maybe_send_backup_updates_timer_even_on_failure(watchdog, tmp_path):
    """Реальна мета: провал відправки (напр. Telegram тимчасово
    недоступний) НЕ має спричиняти повторні спроби щоцикл опитування
    (~10с) - таймер оновлюється БЕЗУМОВНО, до самої спроби."""
    config.TELEGRAM_BACKUP_ENABLED = True
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    os.makedirs(config.AUTO_BACKUP_DIR)
    watchdog.last_telegram_backup_sent_ts = 0
    with open(os.path.join(config.AUTO_BACKUP_DIR, "backup-1.json"), "w") as f:
        f.write("{}")

    before = watchdog.last_telegram_backup_sent_ts
    with patch("app.telegram_notify.send_document", return_value=(False, "мережева помилка")):
        watchdog._maybe_send_backup_to_telegram()

    assert watchdog.last_telegram_backup_sent_ts > before


# ---- Starlink вимкнено (ніч / відключення світла): роутер теж недоступний ----

def _offline_status():
    from app.starlink_client import DishStatus
    return DishStatus(timestamp=time.time(), online=False, error="no route to host")


def _online_status():
    from app.starlink_client import DishStatus
    return DishStatus(timestamp=time.time(), online=True, uptime_s=100)


def _events(kind):
    return [e for e in db.get_recent_events(500) if e["kind"] == kind]


def _go_offline(wd, t0):
    """Роутер мовчить STARLINK_OFFLINE_CONFIRM_POLLS опитувань поспіль
    (крок 10 с, починаючи з t0) - підтверджене вимкнення Starlink."""
    wd.client.router_reachable = lambda timeout=2.0: False
    for i in range(wd.STARLINK_OFFLINE_CONFIRM_POLLS):
        with patch("time.time", return_value=t0 + i * 10), \
             patch.object(wd.client, "get_status", return_value=_offline_status()):
            wd.poll_once()
    assert wd.starlink_offline_since == t0


def test_starlink_off_no_reboot_no_failure_count(watchdog):
    """Роутер недоступний -> це вимкнений Starlink, не зависання тарілки:
    жодної спроби reboot навіть після багатьох опитувань, лічильник не
    росте, у журналі рівно ОДНА подія starlink_offline."""
    watchdog.client.router_reachable = lambda timeout=2.0: False
    with patch.object(watchdog.client, "get_status", return_value=_offline_status()), \
         patch.object(watchdog.client, "reboot_dish") as mock_reboot:
        for _ in range(config.MAX_CONSECUTIVE_FAILURES * 3):
            watchdog.poll_once()
    mock_reboot.assert_not_called()
    assert watchdog.consecutive_failures == 0
    assert watchdog.starlink_offline_since is not None
    assert len(_events("starlink_offline")) == 1


def test_starlink_off_no_per_poll_warning(watchdog, caplog):
    watchdog.client.router_reachable = lambda timeout=2.0: False
    with patch.object(watchdog.client, "get_status", return_value=_offline_status()), \
         caplog.at_level("INFO", logger="monitor"):
        for _ in range(20):
            watchdog.poll_once()
    assert "Dish недоступний" not in caplog.text
    assert caplog.text.count("Starlink недоступний") == 1


def test_starlink_back_after_long_off_notifies_once_with_duration(watchdog):
    t0 = 1_000_000.0
    _go_offline(watchdog, t0)
    watchdog.client.router_reachable = lambda timeout=2.0: True
    with patch("time.time", return_value=t0 + 8 * 3600 + 20 * 60), \
         patch.object(watchdog.client, "get_status", return_value=_online_status()):
        watchdog.poll_once()
        watchdog.poll_once()
    assert watchdog.sent == ["✅ Starlink знову доступний, був вимкнений 8 год 20 хв"]
    assert watchdog.starlink_offline_since is None
    assert len(_events("starlink_online")) == 1


def test_starlink_back_after_short_off_no_telegram(watchdog):
    """Коротке зникнення (перезавантаження роутера при оновленні
    прошивки) - лише запис у журнал, без Telegram."""
    t0 = 1_000_000.0
    _go_offline(watchdog, t0)
    watchdog.client.router_reachable = lambda timeout=2.0: True
    with patch("time.time", return_value=t0 + 90), \
         patch.object(watchdog.client, "get_status", return_value=_online_status()):
        watchdog.poll_once()
    assert watchdog.sent == []
    assert len(_events("starlink_online")) == 1


def test_starlink_back_message_in_english(watchdog):
    db.set_setting("ui_language", "en")
    _go_offline(watchdog, 1000.0)
    watchdog.client.router_reachable = lambda timeout=2.0: True
    with patch("time.time", return_value=1000.0 + 2 * 3600), \
         patch.object(watchdog.client, "get_status", return_value=_online_status()):
        watchdog.poll_once()
    assert watchdog.sent == ["✅ Starlink is available again, was off for 2 h 0 min"]


def test_router_back_before_dish_resumes_normal_watchdog_from_zero(watchdog):
    """Роутер повернувся, dish ще ні (завантажується) - вихід зі стану,
    далі звичайний watchdog з нульового лічильника."""
    watchdog.client.router_reachable = lambda timeout=2.0: False
    with patch.object(watchdog.client, "get_status", return_value=_offline_status()):
        for _ in range(5):
            watchdog.poll_once()
    watchdog.client.router_reachable = lambda timeout=2.0: True
    with patch.object(watchdog.client, "get_status", return_value=_offline_status()), \
         patch.object(watchdog, "_maybe_reboot") as mock_maybe:
        watchdog.poll_once()
    assert watchdog.starlink_offline_since is None
    assert watchdog.consecutive_failures == 1
    mock_maybe.assert_called_once()


def test_dish_hang_with_router_up_still_reboots(watchdog):
    """Регресія: роутер відповідає, dish ні - це зависання тарілки,
    watchdog і далі перезавантажує (попередня поведінка збережена)."""
    watchdog.last_reboot_ts = 0.0
    with patch.object(watchdog.client, "get_status", return_value=_offline_status()), \
         patch.object(watchdog.client, "reboot_dish", return_value=(False, "timeout")) as mock_reboot:
        for _ in range(config.MAX_CONSECUTIVE_FAILURES):
            watchdog.poll_once()
    mock_reboot.assert_called_once()
    assert watchdog.starlink_offline_since is None


def test_reboot_skip_logged_once_per_wait_window(watchdog, caplog):
    watchdog.consecutive_failures = config.MAX_CONSECUTIVE_FAILURES
    with patch("time.time", return_value=10_000.0), caplog.at_level("INFO", logger="monitor"):
        watchdog.last_reboot_ts = 10_000.0 - 10
        for _ in range(25):
            watchdog._maybe_reboot()
        assert caplog.text.count("Пропускаю авто-reboot") == 1
        watchdog.last_reboot_ts = 10_000.0 - 5   # нове вікно (нова спроба reboot)
        watchdog._maybe_reboot()
        assert caplog.text.count("Пропускаю авто-reboot") == 2


def test_format_duration():
    from app import i18n
    from app.monitor import format_duration
    uk = i18n.translator("uk")
    assert format_duration(45 * 60, uk) == "45 хв"
    assert format_duration(8 * 3600 + 20 * 60, uk) == "8 год 20 хв"
    assert format_duration(-5, uk) == "0 хв"


def test_short_wifi_blip_is_silent(watchdog, caplog):
    """Розрив WiFi коротший за підтвердження (1-2 опитування) - жодних
    подій, логів "Starlink недоступний", Telegram чи лічильника збоїв."""
    for blip_len in (1, watchdog.STARLINK_OFFLINE_CONFIRM_POLLS - 1):
        watchdog.client.router_reachable = lambda timeout=2.0: False
        with caplog.at_level("INFO", logger="monitor"):
            with patch.object(watchdog.client, "get_status", return_value=_offline_status()):
                for _ in range(blip_len):
                    watchdog.poll_once()
            watchdog.client.router_reachable = lambda timeout=2.0: True
            with patch.object(watchdog.client, "get_status", return_value=_online_status()):
                watchdog.poll_once()
        assert watchdog.starlink_offline_since is None
        assert watchdog.consecutive_failures == 0
    assert _events("starlink_offline") == [] and _events("starlink_online") == []
    assert "Starlink" not in caplog.text
    assert watchdog.sent == []


def test_blip_then_dish_hang_counts_normally(watchdog):
    """Короткий провал роутера, далі роутер є, а dish ні - звичайний
    watchdog; провал не відкриває стан "Starlink вимкнено"."""
    watchdog.client.router_reachable = lambda timeout=2.0: False
    with patch.object(watchdog.client, "get_status", return_value=_offline_status()):
        watchdog.poll_once()
    watchdog.client.router_reachable = lambda timeout=2.0: True
    with patch.object(watchdog.client, "get_status", return_value=_offline_status()), \
         patch.object(watchdog, "_maybe_reboot") as mock_maybe:
        watchdog.poll_once()
    assert watchdog.starlink_offline_since is None
    assert watchdog.consecutive_failures == 1
    assert watchdog._router_down_polls == 0
    mock_maybe.assert_called_once()
    assert _events("starlink_offline") == []


# ---- приховані події (рішення користувача): не пишуться й не показуються ----

def test_hidden_router_update_state_not_logged_nor_notified(watchdog):
    """DOWNLOADING_UPDATE_IMAGE_FAILED: без події, без Telegram, попередній
    стан не змінюється - тож повернення до завантаження теж тихе."""
    from app.starlink_client import RouterInfo
    watchdog.prev_router_update_state = "DOWNLOADING_UPDATE_IMAGE"
    for st in ("DOWNLOADING_UPDATE_IMAGE_FAILED", "DOWNLOADING_UPDATE_IMAGE"):
        watchdog._log_router_update_state_change(RouterInfo(timestamp=time.time(), online=True, update_state=st))
    assert db.get_recent_events(10) == []
    assert watchdog.sent == []
    assert watchdog.prev_router_update_state == "DOWNLOADING_UPDATE_IMAGE"


def test_hidden_router_state_does_not_swallow_next_real_change(watchdog):
    """Контроль: після прихованого стану справжня зміна (REBOOT_PENDING)
    і далі пишеться й сповіщається."""
    from app.starlink_client import RouterInfo
    watchdog.prev_router_update_state = "DOWNLOADING_UPDATE_IMAGE"
    for st in ("DOWNLOADING_UPDATE_IMAGE_FAILED", "REBOOT_PENDING"):
        watchdog._log_router_update_state_change(RouterInfo(timestamp=time.time(), online=True, update_state=st))
    events = db.get_recent_events(10)
    assert len(events) == 1
    assert "помилка завантаження" not in events[0]["message"]
    assert any("очікує перезавантаження" in m for m in watchdog.sent)


def test_getting_target_version_failed_still_logged_but_not_notified(watchdog):
    """Рішення користувача: у /status і на дашборді - "немає оновлень",
    але в ЖУРНАЛ подій стан і далі пишеться (без Telegram)."""
    from app.starlink_client import RouterInfo
    watchdog.prev_router_update_state = "NOT_RUN"
    watchdog._log_router_update_state_change(
        RouterInfo(timestamp=time.time(), online=True, update_state="GETTING_TARGET_VERSION_FAILED"))
    assert any("помилка перевірки оновлення" in e["message"] for e in db.get_recent_events(10))
    assert watchdog.sent == []


# ---- зміна стану скидає буфер метрик у БД одразу ----

def _st(online=True, update_state="IDLE"):
    from app.starlink_client import DishStatus
    return DishStatus(timestamp=time.time(), online=online, uptime_s=100, update_state=update_state)


def test_state_change_flushes_buffer_immediately(watchdog):
    """Заміна Starlink на станції: offline -> online має з'явитись у БД
    (а отже на дашборді й дисплеї) на тому ж опитуванні, не через 30 с."""
    watchdog.client.router_reachable = lambda timeout=2.0: True
    for status in (_st(online=False), _st(online=True)):
        with patch.object(watchdog.client, "get_status", return_value=status):
            watchdog.poll_once()
    assert watchdog.metrics_buffer == []
    assert db.get_latest_metric()["online"] == 1


def test_update_state_change_flushes_buffer(watchdog):
    for status in (_st(update_state="IDLE"), _st(update_state="FETCHING")):
        with patch.object(watchdog.client, "get_status", return_value=status):
            watchdog.poll_once()
    assert db.get_latest_metric()["update_state"] == "FETCHING"


def test_unchanged_state_stays_batched(watchdog):
    """Контроль: однакові опитування й далі йдуть пакетом (SD-картка)."""
    for _ in range(5):
        with patch.object(watchdog.client, "get_status", return_value=_st()):
            watchdog.poll_once()
    assert len(watchdog.metrics_buffer) == 5
    assert db.get_latest_metric() is None


def test_db_failure_on_state_change_does_not_block_watchdog(watchdog):
    """Регресія: негайний запис зміни стану падав разом із БД і обривав
    poll_once() ДО лічильника збоїв - зависла тарілка не
    перезавантажувалась, поки БД недоступна."""
    import sqlite3
    watchdog.last_reboot_ts = 0.0
    with patch.object(watchdog.client, "get_status", return_value=_st(online=True)):
        watchdog.poll_once()
    with patch("app.db.insert_metrics_batch", side_effect=sqlite3.OperationalError("database is locked")), \
         patch.object(watchdog.client, "get_status", return_value=_st(online=False)), \
         patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as mock_reboot:
        for _ in range(config.MAX_CONSECUTIVE_FAILURES):
            watchdog.poll_once()
    mock_reboot.assert_called_once()
    assert len(watchdog.metrics_buffer) > 0   # дані не втрачені - чекають планового запису


# ---- watchdog працює при недоступній БД (журнал не блокує reboot) ----

def _break_db(tmp_path):
    import sqlite3
    p = tmp_path / "broken.db"
    sqlite3.connect(p).close()          # файл є, таблиць немає - падає будь-яка операція
    config.DB_PATH = str(p)


def test_broken_db_does_not_prevent_watchdog_reboot(watchdog, tmp_path):
    """Раніше подія watchdog_trigger писалась ДО reboot_dish() і падала
    разом із БД - зависла тарілка не перезавантажувалась ніколи."""
    watchdog.last_reboot_ts = 0.0
    watchdog.consecutive_failures = config.MAX_CONSECUTIVE_FAILURES
    _break_db(tmp_path)
    with patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as mock_reboot:
        watchdog._maybe_reboot()
    mock_reboot.assert_called_once()


def test_broken_db_keeps_min_reboot_interval(watchdog, tmp_path):
    """Раніше подія dish_reboot писалась ДО last_reboot_ts = now - при
    зламаній БД це дало б reboot на кожному опитуванні."""
    watchdog.last_reboot_ts = 0.0
    _break_db(tmp_path)
    with patch.object(watchdog.client, "reboot_dish", return_value=(False, "timeout")) as mock_reboot, \
         patch("time.time", return_value=10_000.0):
        for _ in range(5):
            watchdog.consecutive_failures = config.MAX_CONSECUTIVE_FAILURES
            watchdog._maybe_reboot()
    assert mock_reboot.call_count == 1
    assert watchdog.last_reboot_ts == 10_000.0


def test_broken_db_does_not_prevent_update_ready_reboot(watchdog, tmp_path):
    watchdog.last_reboot_ts = 0.0
    _break_db(tmp_path)
    with patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as mock_reboot:
        watchdog._reboot_for_update_ready("dish", "REBOOT_REQUIRED")
    mock_reboot.assert_called_once()
    assert watchdog.last_reboot_ts > 0


# ---- фонова відправка сповіщень: цикл монітора не чекає на мережу ----

def test_notify_with_sender_does_not_block_on_dead_network(db_path):
    """Без інтернету одне сповіщення раніше блокувало цикл монітора до
    ~2 хв (watchdog стояв, healthcheck перезапускав монітор)."""
    import threading
    from app.monitor import Watchdog, _BackgroundSender
    release, sent = threading.Event(), []
    def slow_send(text):
        release.wait(10)
        sent.append(text)
        return True, "ok"
    with patch("app.telegram_notify.send_message", side_effect=slow_send):
        wd = Watchdog()
        wd._sender = _BackgroundSender()
        t0 = time.monotonic()
        wd._notify("x")
        assert time.monotonic() - t0 < 0.5
        release.set()
        wd._sender.stop(timeout=5)
    assert sent == ["x"]


def test_sender_preserves_order_and_drains_on_stop(db_path):
    from app.monitor import _BackgroundSender
    got = []
    sender = _BackgroundSender()
    for i in range(10):
        sender.submit(lambda i=i: got.append(i), str(i))
    sender.stop(timeout=5)
    assert got == list(range(10))
    assert not sender.is_alive()


def test_sender_full_queue_drops_without_blocking(db_path, caplog):
    import threading
    from app.monitor import _BackgroundSender
    gate = threading.Event()
    sender = _BackgroundSender(maxsize=1)
    sender.submit(lambda: gate.wait(10), "займає потік")
    time.sleep(0.1)                                     # потік узяв перше завдання
    sender.submit(lambda: None, "у черзі")
    t0 = time.monotonic()
    with caplog.at_level("WARNING", logger="monitor"):
        sender.submit(lambda: None, "зайве")            # черга повна
    assert time.monotonic() - t0 < 0.5
    assert "переповнена" in caplog.text
    gate.set()
    sender.stop(timeout=5)


def test_notify_without_sender_stays_synchronous(db_path):
    """Прямі виклики й тести (без run_forever) - синхронно, як раніше."""
    from app.monitor import Watchdog
    sent = []
    with patch("app.telegram_notify.send_message", side_effect=lambda t: sent.append(t) or (True, "ok")):
        Watchdog()._notify("x")
        assert sent == ["x"]


def test_sender_job_exception_does_not_kill_worker(db_path):
    from app.monitor import _BackgroundSender
    got = []
    sender = _BackgroundSender()
    sender.submit(lambda: 1 / 0, "падає")
    sender.submit(lambda: got.append("далі"), "наступне")
    sender.stop(timeout=5)
    assert got == ["далі"]


# ---- _send_now() і flush_metrics_buffer(): мова й стійкість до збоїв БД/мережі ----


def test_send_now_quiet_for_unconfigured_telegram_in_any_language(db_path, caplog):
    """Раніше монітор порівнював УКРАЇНСЬКІ тексти ("Telegram сповіщення
    вимкнені"...), щоб не логувати очікувану ситуацію; з перекладом в
    англійському інтерфейсі це порівняння перестало б спрацьовувати -
    лог засмічувався б хибними попередженнями. Тепер - код стану."""
    from app.monitor import Watchdog
    from app import telegram_notify
    db.set_setting("ui_language", "en")
    wd = Watchdog()
    with caplog.at_level("WARNING", logger="monitor"):
        wd._send_now("x")                                           # Telegram вимкнено
        telegram_notify.set_telegram_config(token=None, chat_ids=["1"], enabled=True)
        wd._send_now("x")                                           # без токена
    assert "не надіслано" not in caplog.text


def test_send_now_warns_on_real_failure(db_path, caplog):
    from app.monitor import Watchdog
    from app import telegram_notify
    telegram_notify.set_telegram_config(token="T", chat_ids=["1"], enabled=True)
    with patch("app.telegram_notify.send_message", return_value=(False, "network down")), \
         caplog.at_level("WARNING", logger="monitor"):
        Watchdog()._send_now("x")
    assert "network down" in caplog.text


def test_flush_failure_keeps_buffer_retries_later_and_caps_memory(watchdog, caplog):
    import sqlite3
    watchdog.metrics_buffer = [{"timestamp": float(i), "online": 1} for i in range(monitor._METRICS_BUFFER_MAX + 50)]
    watchdog.last_batch_flush_ts = 0.0
    with patch("app.db.insert_metrics_batch", side_effect=sqlite3.OperationalError("database is locked")), \
         caplog.at_level("WARNING", logger="monitor"):
        watchdog.flush_metrics_buffer()                       # НЕ кидає
    assert len(watchdog.metrics_buffer) == monitor._METRICS_BUFFER_MAX
    assert watchdog.metrics_buffer[0]["timestamp"] == 50.0    # відкинуто найстаріші, а не найновіші
    assert watchdog.last_batch_flush_ts > 0                   # повтор через інтервал, не щоітерації
    assert "відкинуто 50 найстаріших" in caplog.text and "database is locked" in caplog.text


def test_flush_recovers_after_db_comes_back(watchdog):
    import sqlite3
    watchdog.metrics_buffer = [{"timestamp": time.time(), "online": 1, "uptime_s": 5}]
    with patch("app.db.insert_metrics_batch", side_effect=sqlite3.OperationalError("locked")):
        watchdog.flush_metrics_buffer()
    assert len(watchdog.metrics_buffer) == 1
    watchdog.flush_metrics_buffer()                           # БД знову доступна
    assert watchdog.metrics_buffer == [] and db.get_latest_metric() is not None


# ---- перемикач авто-перезавантаження (/settings) і рідкісні гілки логування reboot ----

def _dish_update_ready():
    from app.starlink_client import DishStatus
    return DishStatus(timestamp=time.time(), online=True, update_state="REBOOT_REQUIRED")


def _router_update_ready():
    from app.starlink_client import RouterInfo
    return RouterInfo(timestamp=time.time(), online=True, update_state="REBOOT_PENDING")


def test_update_ready_reboot_respects_auto_reboot_toggle(watchdog):
    """Перемикач перевіряють ОБИДВІ обгортки (тарілка й роутер), спільна
    функція дії - ні. Гілка "вимкнено" не була покрита жодним тестом, хоча
    це перемикач, який користувач натискає на дашборді."""
    for wrapper, info in ((watchdog._maybe_reboot_for_update, _dish_update_ready()),
                          (watchdog._maybe_reboot_for_router_update, _router_update_ready())):
        watchdog.last_reboot_ts = 0.0
        db.set_auto_reboot_enabled(False)
        with patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as rb:
            wrapper(info)
        rb.assert_not_called()
        db.set_auto_reboot_enabled(True)
        with patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as rb:
            wrapper(info)
        rb.assert_called_once()


def test_watchdog_reboot_is_not_gated_by_update_toggle(watchdog):
    """Перемикач - "авто-reboot при готовому оновленні"; аварійне
    перезавантаження зависшої тарілки від нього не залежить."""
    db.set_auto_reboot_enabled(False)
    watchdog.last_reboot_ts = 0.0
    watchdog.consecutive_failures = config.MAX_CONSECUTIVE_FAILURES
    with patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as rb:
        watchdog._maybe_reboot()
    rb.assert_called_once()


def test_reboot_trigger_logging_stops_after_limit_with_one_final_marker(watchdog):
    """Перші MAX_LOGGED збоїв пишуться в журнал, наступний - один підсумковий
    рядок "Понад N...", далі мовчки (не сотні рядків за ніч на SD)."""
    watchdog.client.reboot_dish = lambda: (False, "timeout")
    limit = config.MAX_LOGGED_CONSECUTIVE_FAILURES
    for failures, expected in ((limit + 1, 1), (limit + 2, 0)):
        with db.get_conn() as c:
            c.execute("DELETE FROM events")
        watchdog.last_reboot_ts = 0.0
        watchdog.consecutive_failures = failures
        watchdog._maybe_reboot()
        triggers = [e for e in db.get_recent_events(20) if e["kind"] == "watchdog_trigger"]
        assert len(triggers) == expected, (failures, triggers)
        if expected:
            assert f"Понад {limit}" in triggers[0]["message"]


# ---- локальна несправність не перезавантажує тарілку ----

def test_local_fault_never_reboots_dish_and_reports_once(watchdog, caplog):
    """Симуляція до виправлення: без модуля starlink_grpc watchdog вважав
    тарілку зависшою й перезавантажував її кожні MIN_REBOOT_INTERVAL (22 рази
    за 67 хв) - пошкоджений ЛОКАЛЬНИЙ файл відключав би інтернет."""
    from app.starlink_client import StarlinkClient
    clock = {"t": time.time()}
    reboots = []
    watchdog.client.reboot_dish = lambda: (reboots.append(1), (True, "ok"))[1]
    with patch.object(starlink_client, "starlink_grpc", None), \
         patch("time.time", side_effect=lambda: clock["t"]), caplog.at_level("ERROR", logger="monitor"):
        for _ in range(400):                                   # ~67 хв опитувань по 10 с
            clock["t"] += 10
            watchdog.poll_once()
    assert reboots == []
    assert watchdog.consecutive_failures == 0
    assert len([m for m in watchdog.sent if "ЛОКАЛЬНА" in m]) == 1          # одне сповіщення, не 400
    events = [e for e in db.get_recent_events(20) if e["kind"] == "local_fault"]
    assert len(events) == 1 and events[0]["success"] == 0
    assert caplog.text.count("Локальна несправність") == 1
    assert StarlinkClient is not None


def test_genuine_dish_failure_still_reboots(watchdog):
    """Контроль: реальний збій тарілки (не local_fault) працює як раніше."""
    from app.starlink_client import DishStatus
    watchdog.client.router_reachable = lambda timeout=2.0: True
    watchdog.last_reboot_ts = 0.0
    with patch.object(watchdog.client, "get_status",
                      return_value=DishStatus(timestamp=time.time(), online=False, error="timeout")), \
         patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as rb:
        for _ in range(config.MAX_CONSECUTIVE_FAILURES):
            watchdog.poll_once()
    rb.assert_called_once()


# ---- авто-reboot не блокується на години після зсуву годинника назад ----

def _watchdog_with_hung_dish(monkeypatch, clock):
    from app import config
    monkeypatch.setattr(config, "MAX_CONSECUTIVE_FAILURES", 3)
    monkeypatch.setattr(config, "MIN_REBOOT_INTERVAL_SEC", 300)
    wd = monitor.Watchdog()
    wd._notify = lambda t: None
    reboots = []
    wd.client.reboot_dish = lambda: reboots.append(clock["t"]) or (True, "ok")
    wd.consecutive_failures = 3
    return wd, reboots


def test_auto_reboot_waits_min_interval_after_clock_step_back_not_hours(db_path, monkeypatch):
    clock = {"t": 1_800_000_000.0}
    wd, reboots = _watchdog_with_hung_dish(monkeypatch, clock)
    with patch("time.time", side_effect=lambda: clock["t"]):
        wd.last_reboot_ts = clock["t"]                    # щойно перезавантажили
        clock["t"] -= 7200                                # годинник стрибнув на 2 години назад
        wd._maybe_reboot()
        assert reboots == [] and wd.last_reboot_ts == clock["t"]    # захист від reboot-loop зберігся: вікно від ЗАРАЗ
        clock["t"] += 301                                 # MIN_REBOOT_INTERVAL_SEC минув
        wd._maybe_reboot()
    assert len(reboots) == 1                              # раніше - лише через 2 год 5 хв


def test_update_ready_reboot_also_recovers_after_clock_step_back(db_path, monkeypatch):
    clock = {"t": 1_800_000_000.0}
    wd, reboots = _watchdog_with_hung_dish(monkeypatch, clock)
    with patch("time.time", side_effect=lambda: clock["t"]):
        wd.last_reboot_ts = clock["t"]
        clock["t"] -= 7200
        wd._reboot_for_update_ready("dish", "тест")
        assert reboots == []
        clock["t"] += 301
        wd._reboot_for_update_ready("dish", "тест")
    assert len(reboots) == 1


def test_clamp_future_timestamps_only_touches_future_marks():
    wd = monitor.Watchdog()
    wd.last_reboot_ts, wd.last_telegram_backup_sent_ts = 5000.0, 900.0
    wd._clamp_future_timestamps(1000.0)
    assert wd.last_reboot_ts == 1000.0 and wd.last_telegram_backup_sent_ts == 900.0     # минулі мітки не чіпаємо
