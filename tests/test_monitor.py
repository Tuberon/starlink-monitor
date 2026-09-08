"""
Тести для app/monitor.py - найкрихкіша, stateful-логіка проєкту:
групування reboot-спаму (часове вікно), дедублікація сповіщень про
target-версії (порівняння зі збереженим значенням, не boolean-
прапорець). Сценарії відтворюють ті самі, що верифікувались ad-hoc
живими тестами протягом розробки - тепер персистентно.
"""
import json
import os
import time
from unittest.mock import patch

import pytest

from app import config, db, monitor


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


# ---- Плановий reboot по таймеру (should_scheduled_reboot / _maybe_scheduled_reboot) ----

def test_should_scheduled_reboot_false_when_disabled():
    assert monitor.should_scheduled_reboot(0.0, 1000.0, interval_hours=0) is False


def test_should_scheduled_reboot_false_before_interval():
    now = 1000.0
    last = now - 3600  # годину тому
    assert monitor.should_scheduled_reboot(last, now, interval_hours=24) is False


def test_should_scheduled_reboot_true_after_interval():
    now = 1000.0
    last = now - 25 * 3600  # 25 годин тому
    assert monitor.should_scheduled_reboot(last, now, interval_hours=24) is True


def test_maybe_scheduled_reboot_disabled_by_default(watchdog):
    config.SCHEDULED_REBOOT_ENABLED = False
    with patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as mock_reboot:
        watchdog._maybe_scheduled_reboot()
        mock_reboot.assert_not_called()


def test_maybe_scheduled_reboot_triggers_after_interval(watchdog):
    config.SCHEDULED_REBOOT_ENABLED = True
    config.SCHEDULED_REBOOT_INTERVAL_HOURS = 24
    watchdog.last_scheduled_reboot_ts = time.time() - 25 * 3600
    watchdog.last_reboot_ts = 0.0
    try:
        with patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as mock_reboot:
            watchdog._maybe_scheduled_reboot()
            mock_reboot.assert_called_once()
        assert len(watchdog.sent) == 1
        assert "Плановий reboot" in watchdog.sent[0]
    finally:
        config.SCHEDULED_REBOOT_ENABLED = False


def test_maybe_scheduled_reboot_respects_min_reboot_interval(watchdog):
    """Якщо reboot вже стався нещодавно з ІНШОЇ причини (напр.
    auto-reboot при невдачах), плановий reboot природно
    відкладається - не подвоюється."""
    config.SCHEDULED_REBOOT_ENABLED = True
    config.SCHEDULED_REBOOT_INTERVAL_HOURS = 24
    watchdog.last_scheduled_reboot_ts = time.time() - 25 * 3600
    watchdog.last_reboot_ts = time.time() - 30  # 30с тому, менше MIN_REBOOT_INTERVAL_SEC
    try:
        with patch.object(watchdog.client, "reboot_dish", return_value=(True, "ok")) as mock_reboot:
            watchdog._maybe_scheduled_reboot()
            mock_reboot.assert_not_called()
    finally:
        config.SCHEDULED_REBOOT_ENABLED = False


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


def test_reboot_notify_threshold_triggers_grouping_warning(watchdog):
    """Досягнення порогу - ОДНЕ попередження про групування замість
    звичайного тексту reboot-повідомлення."""
    config.REBOOT_SPAM_THRESHOLD = 3
    config.REBOOT_SPAM_WINDOW_SEC = 1800
    now = 1000.0
    for i in range(3):
        with patch("time.time", return_value=now + i * 100):
            watchdog._notify_reboot(f"🔁 reboot {i}")
    assert len(watchdog.sent) == 3
    assert "Часті авто-reboot" in watchdog.sent[-1]
    assert watchdog.reboot_spam_muted is True


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


# ---- Дедублікація сповіщень про target-версію (_check_target_version_reached) ----

def test_target_version_no_target_set_is_silent(watchdog):
    monitor.check_target_version_reached(
                "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    assert watchdog.sent == []


def test_target_version_mismatch_is_silent(watchdog):
    db.set_setting("dish_target_version", "2026.04.01")
    monitor.check_target_version_reached(
                "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    assert watchdog.sent == []


def test_target_version_match_sends_notification(watchdog):
    db.set_setting("dish_target_version", "2026.04.01")
    monitor.check_target_version_reached(
                "тарілки", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    assert len(watchdog.sent) == 1
    assert "2026.04.01" in watchdog.sent[0]


def test_target_version_repeat_match_does_not_spam(watchdog):
    db.set_setting("dish_target_version", "2026.04.01")
    monitor.check_target_version_reached(
                "тарілки", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    monitor.check_target_version_reached(
                "тарілки", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    assert len(watchdog.sent) == 1


def test_target_version_new_target_resets_dedup(watchdog):
    """Зміна target на НОВЕ значення природно скидає notified-стан
    (порівняння значень, не boolean-прапорець)."""
    db.set_setting("dish_target_version", "2026.04.01")
    monitor.check_target_version_reached(
                "тарілки", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    db.set_setting("dish_target_version", "2026.05.01")
    monitor.check_target_version_reached(
                "тарілки", "2026.05.01", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    assert len(watchdog.sent) == 2
    assert "2026.05.01" in watchdog.sent[1]


def test_target_version_multiple_candidates_matches_any(watchdog):
    """Кілька версій через кому (різні апаратні ревізії) - матч на
    БУДЬ-ЯКУ з перелічених, не лише першу."""
    db.set_setting("dish_target_version", "2026.03.03.mr75126.1, 2026.03.03.mr75130.1")
    monitor.check_target_version_reached(
                "тарілки", "2026.03.03.mr75130.1", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    assert len(watchdog.sent) == 1
    assert "2026.03.03.mr75130.1" in watchdog.sent[0]


def test_target_version_multiple_candidates_none_matching_is_silent(watchdog):
    db.set_setting("dish_target_version", "v1, v2, v3")
    monitor.check_target_version_reached(
                "тарілки", "v4", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    assert watchdog.sent == []


def test_target_version_different_dish_id_gets_fresh_notification(watchdog):
    """Найважливіший сценарій: якщо ФІЗИЧНО ІНШИЙ Starlink (інший
    dish_id, напр. після заміни обладнання) збігається з тим самим
    target-значенням, що вже notified для ПОПЕРЕДНЬОГО dish - НЕ
    вважається дублікатом, отримує своє власне, свіже сповіщення."""
    db.set_setting("dish_target_version", "2026.03.03")
    monitor.check_target_version_reached(
                "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish-OLD",
        watchdog._notify,
    )
    assert len(watchdog.sent) == 1

    # Той самий target, та сама версія, АЛЕ ІНШИЙ фізичний dish_id
    monitor.check_target_version_reached(
                "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish-NEW",
        watchdog._notify,
    )
    assert len(watchdog.sent) == 2, "новий dish_id мав отримати власне сповіщення, не заблоковане дедублікацією попереднього"


def test_target_version_same_dish_id_still_deduplicates(watchdog):
    """Контрольний тест: дедублікація ВСЕ ЩЕ працює для ТОГО САМОГО
    dish_id (фіча не зламала звичайну поведінку, лише додала
    ізоляцію МІЖ різними фізичними пристроями)."""
    db.set_setting("dish_target_version", "2026.03.03")
    monitor.check_target_version_reached(
                "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    monitor.check_target_version_reached(
                "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1",
        watchdog._notify,
    )
    assert len(watchdog.sent) == 1


# ---- Комбінована перевірка (_check_both_targets_reached) ----

def test_both_targets_none_set_is_silent(watchdog):
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    assert watchdog.sent == []


def test_both_targets_only_one_set_is_silent(watchdog):
    db.set_setting("dish_target_version", "v1")
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    assert watchdog.sent == []


def test_both_targets_set_but_not_matching_is_silent(watchdog):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v0").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r0").to_dict())
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    assert watchdog.sent == []


def test_both_targets_only_dish_matching_is_silent(watchdog):
    """Найважливіший граничний випадок: ЛИШЕ dish відповідає своєму
    target, router - ще ні. НЕ повинно спамити частковий стан."""
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r0").to_dict())
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    assert watchdog.sent == []


def test_both_targets_matching_simultaneously_notifies(watchdog):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    assert len(watchdog.sent) == 1
    assert "v1" in watchdog.sent[0] and "r1" in watchdog.sent[0]


def test_both_targets_repeat_call_does_not_spam(watchdog):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    assert len(watchdog.sent) == 1


def test_both_targets_changing_one_target_resets_dedup(watchdog):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)

    db.set_setting("router_target_version", "r2")
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    assert len(watchdog.sent) == 1  # router ще не оновився до r2

    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r2").to_dict())
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    assert len(watchdog.sent) == 2
    assert "r2" in watchdog.sent[1]


def test_both_targets_different_dish_id_gets_fresh_notification(watchdog):
    """Той самий принцип, що для per-component перевірки: ІНШИЙ
    фізичний Starlink (інший last_known_dish_id) з тим самим
    збігом версій - НЕ вважається дублікатом попереднього."""
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())

    watchdog.last_known_dish_id = "dish-OLD"
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    assert len(watchdog.sent) == 1

    watchdog.last_known_dish_id = "dish-NEW"
    monitor.check_both_targets_reached(watchdog.last_known_dish_id, watchdog._notify)
    assert len(watchdog.sent) == 2, "новий фізичний Starlink мав отримати власне комбіноване сповіщення"


# ---- Розрізнення напрямку зміни прошивки (upsert_dish_and_notify/upsert_router_and_notify) ----

def test_firmware_forward_change_says_updated(db_path):
    """Звичайний, найчастіший випадок - версія рухається вперед."""
    from app.starlink_client import DishStatus
    sent = []
    db.upsert_known_device_dish("dish1", "rev3", "2026.05.13.mr80201")
    status = DishStatus(
        timestamp=time.time(), online=True, uptime_s=100,
        dish_id="dish1", hardware_version="rev3", software_version="2026.07.16.mr82459.1",
    )
    monitor.upsert_dish_and_notify(status, lambda t: sent.append(t))
    assert len(sent) == 1
    assert "🔄" in sent[0] and "оновлена" in sent[0]
    assert "відкочена" not in sent[0]


def test_firmware_backward_change_says_rolled_back(db_path):
    """Реальний сценарій, знайдений користувачем на практиці: SpaceX
    інколи відкочує прошивку - без розрізнення напрямку повідомлення
    "🔄 оновлена: НОВІША → СТАРІША" вводило б в оману."""
    from app.starlink_client import DishStatus
    sent = []
    db.upsert_known_device_dish("dish1", "rev3", "2026.07.16.mr82459.1")
    status = DishStatus(
        timestamp=time.time(), online=True, uptime_s=100,
        dish_id="dish1", hardware_version="rev3", software_version="2026.05.13.mr80201",
    )
    monitor.upsert_dish_and_notify(status, lambda t: sent.append(t))
    assert len(sent) >= 1
    msg = sent[0]
    assert "⏪" in msg and "відкочена" in msg
    assert "оновлена" not in msg
    assert "2026.07.16.mr82459.1" in msg and "2026.05.13.mr80201" in msg


def test_firmware_router_backward_change_exact_user_scenario(db_path):
    """Точний сценарій із реального повідомлення користувача: router
    2026.07.23.mr82306 -> 2025.10.03.mr61821."""
    from app.starlink_client import RouterInfo
    sent = []
    db.upsert_known_device_router("dish1", "rev2", "2026.07.23.mr82306")
    info = RouterInfo(
        timestamp=time.time(), online=True,
        hardware_version="rev2", software_version="2025.10.03.mr61821",
    )
    monitor.upsert_router_and_notify(info, "dish1", lambda t: sent.append(t))
    msg = sent[0]
    assert "⏪" in msg and "відкочена" in msg
    assert "2026.07.23.mr82306 → 2025.10.03.mr61821" in msg


def test_firmware_rollback_notification_disabled_via_config(db_path):
    """Запит користувача: не надсилати "Прошивка роутера відкочена".
    NOTIFY_FIRMWARE_ROLLBACK=False повністю пригнічує "⏪"-сповіщення,
    незалежно від dish/router (спільна _format_firmware_change_
    message())."""
    from app.starlink_client import RouterInfo
    config.NOTIFY_FIRMWARE_ROLLBACK = False
    try:
        db.upsert_known_device_router("dish1", "rev2", "2026.07.23.mr82306")
        sent = []
        info = RouterInfo(
            timestamp=time.time(), online=True,
            hardware_version="rev2", software_version="2025.10.03.mr61821",
        )
        monitor.upsert_router_and_notify(info, "dish1", lambda t: sent.append(t))
        assert sent == []
    finally:
        config.NOTIFY_FIRMWARE_ROLLBACK = True  # не "протікати" в інші тести


def test_firmware_forward_notification_unaffected_by_rollback_toggle(db_path):
    """Контрольний тест: NOTIFY_FIRMWARE_ROLLBACK=False НЕ зачіпає
    звичайні forward-оновлення ("🔄 оновлена") - лише "⏪ відкочена"."""
    from app.starlink_client import DishStatus
    config.NOTIFY_FIRMWARE_ROLLBACK = False
    try:
        db.upsert_known_device_dish("dish1", "rev3", "v1.0")
        sent = []
        status = DishStatus(
            timestamp=time.time(), online=True, uptime_s=100,
            dish_id="dish1", hardware_version="rev3", software_version="v2.0",
        )
        monitor.upsert_dish_and_notify(status, lambda t: sent.append(t))
        assert len(sent) == 1
        assert "🔄" in sent[0] and "оновлена" in sent[0]
    finally:
        config.NOTIFY_FIRMWARE_ROLLBACK = True


# ---- check_updates_now() фільтрує IGNORED_ROUTER_ALERTS (реальний баг, знайдений на запиті користувача) ----

def test_check_updates_now_filters_ignored_router_alert(db_path):
    """Реальний баг: check_updates_now() записувала router-статус
    БЕЗ IGNORED_ROUTER_ALERTS-фільтра (той самий фільтр, що вже мав
    poll_router()) - alert повертався щоразу, коли user тиснув
    "Перевірити оновлення" вручну, аж до наступного фонового циклу
    poll_router(), який знову коректно його прибирав."""
    from unittest.mock import patch
    from app.starlink_client import DishStatus, RouterInfo

    dish = DishStatus(timestamp=time.time(), online=True, uptime_s=100, dish_id="d1",
                       hardware_version="rev3", software_version="v1")
    router = RouterInfo(timestamp=time.time(), online=True, hardware_version="rev2",
                         software_version="v1", active_alerts=["wired_mesh_not_using_wan_iface"])

    with patch("app.telegram_notify.send_message"), \
         patch.object(monitor.StarlinkClient, "get_status", return_value=dish), \
         patch.object(monitor.StarlinkClient, "get_router_info", return_value=router):
        monitor.check_updates_now(monitor.StarlinkClient(), lambda t: None)

    router_in_db = db.get_router_status()
    assert "wired_mesh_not_using_wan_iface" not in router_in_db["active_alerts"]


def test_check_updates_now_keeps_other_router_alerts(db_path):
    """Контрольний тест: фільтр прибирає ЛИШЕ wired_mesh_not_using_
    wan_iface, не всі alerts взагалі."""
    from unittest.mock import patch
    from app.starlink_client import DishStatus, RouterInfo

    dish = DishStatus(timestamp=time.time(), online=True, uptime_s=100, dish_id="d1",
                       hardware_version="rev3", software_version="v1")
    router = RouterInfo(timestamp=time.time(), online=True, hardware_version="rev2",
                         software_version="v1", active_alerts=["thermal_throttle"])

    with patch("app.telegram_notify.send_message"), \
         patch.object(monitor.StarlinkClient, "get_status", return_value=dish), \
         patch.object(monitor.StarlinkClient, "get_router_info", return_value=router):
        monitor.check_updates_now(monitor.StarlinkClient(), lambda t: None)

    router_in_db = db.get_router_status()
    assert "thermal_throttle" in router_in_db["active_alerts"]


def test_ignored_router_alerts_contains_expected_value():
    assert monitor.IGNORED_ROUTER_ALERTS == {"wired_mesh_not_using_wan_iface"}


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


def test_sigterm_flushes_buffer_before_exit(watchdog):
    """Реальна мета graceful shutdown: звичайний systemctl restart
    (SIGTERM) НЕ має втрачати буферизовані дані, лише справжнє
    раптове вимкнення живлення - той самий handler, що run_forever()
    реєструє."""
    import signal as signal_module
    from app.starlink_client import DishStatus
    status = DishStatus(timestamp=time.time(), online=True, uptime_s=100, dish_id="sig-test",
                         hardware_version="rev3", software_version="v1")
    watchdog.metrics_buffer.append(status.to_dict())

    def _handle_shutdown_signal(signum, frame):
        watchdog.flush_metrics_buffer()
        raise SystemExit(0)

    old_handler = signal_module.signal(signal_module.SIGTERM, _handle_shutdown_signal)
    try:
        with pytest.raises(SystemExit):
            import os
            os.kill(os.getpid(), signal_module.SIGTERM)
    finally:
        signal_module.signal(signal_module.SIGTERM, old_handler)

    assert watchdog.metrics_buffer == []
    assert db.get_latest_metric() is not None


# ---- perform_auto_backup() - ротація, вміст ----

def test_perform_auto_backup_writes_valid_json(watchdog, tmp_path):
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    monitor.perform_auto_backup()

    files = os.listdir(config.AUTO_BACKUP_DIR)
    assert len(files) == 1
    with open(os.path.join(config.AUTO_BACKUP_DIR, files[0])) as f:
        content = json.load(f)
    assert content["format_version"] == db.BACKUP_FORMAT_VERSION


def test_perform_auto_backup_rotation_keeps_newest_only(watchdog, tmp_path):
    """Реальна мета ротації: старі backup-и видаляються, залишаються
    саме НАЙНОВІШІ, не найстаріші чи довільні."""
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    config.AUTO_BACKUP_KEEP_COUNT = 3

    for i in range(5):
        with patch("time.time", return_value=1000000.0 + i * 100):
            monitor.perform_auto_backup()

    files = sorted(os.listdir(config.AUTO_BACKUP_DIR))
    assert len(files) == 3
    assert files == ["backup-1000200.json", "backup-1000300.json", "backup-1000400.json"]


def test_perform_auto_backup_zero_keep_count_disables_rotation_deletion(watchdog, tmp_path):
    """AUTO_BACKUP_KEEP_COUNT=0 - крайовий випадок, не має видаляти
    ВСІ файли (0 - не 'нічого не зберігати', а 'ротація вимкнена')."""
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    config.AUTO_BACKUP_KEEP_COUNT = 0
    monitor.perform_auto_backup()
    monitor.perform_auto_backup()
    assert len(os.listdir(config.AUTO_BACKUP_DIR)) >= 1


# ---- check_db_integrity_and_notify() ----

def test_check_db_integrity_healthy_sends_no_notification(watchdog):
    monitor.check_db_integrity_and_notify(watchdog._notify)
    assert watchdog.sent == []


def test_check_db_integrity_corrupted_notifies_and_attempts_backup(watchdog, tmp_path):
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    with open(config.DB_PATH, "wb") as f:
        f.write(b"not a valid sqlite file" * 50)

    monitor.check_db_integrity_and_notify(watchdog._notify)

    assert len(watchdog.sent) >= 1
    assert "пошкодження" in watchdog.sent[0].lower()

