"""
Тести для app/monitor.py - найкрихкіша, stateful-логіка проєкту:
групування reboot-спаму (часове вікно), дедублікація сповіщень про
target-версії (порівняння зі збереженим значенням, не boolean-
прапорець). Сценарії відтворюють ті самі, що верифікувались ad-hoc
живими тестами протягом розробки - тепер персистентно.
"""
import time
from unittest.mock import patch

from app import config, db, monitor


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


# ---- Дедублікація сповіщень про target-версію (_check_target_version_reached) ----

def test_target_version_no_target_set_is_silent(watchdog):
    watchdog._check_target_version_reached(
        "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1"
    )
    assert watchdog.sent == []


def test_target_version_mismatch_is_silent(watchdog):
    db.set_setting("dish_target_version", "2026.04.01")
    watchdog._check_target_version_reached(
        "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1"
    )
    assert watchdog.sent == []


def test_target_version_match_sends_notification(watchdog):
    db.set_setting("dish_target_version", "2026.04.01")
    watchdog._check_target_version_reached(
        "тарілки", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1"
    )
    assert len(watchdog.sent) == 1
    assert "2026.04.01" in watchdog.sent[0]


def test_target_version_repeat_match_does_not_spam(watchdog):
    db.set_setting("dish_target_version", "2026.04.01")
    watchdog._check_target_version_reached(
        "тарілки", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1"
    )
    watchdog._check_target_version_reached(
        "тарілки", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1"
    )
    assert len(watchdog.sent) == 1


def test_target_version_new_target_resets_dedup(watchdog):
    """Зміна target на НОВЕ значення природно скидає notified-стан
    (порівняння значень, не boolean-прапорець)."""
    db.set_setting("dish_target_version", "2026.04.01")
    watchdog._check_target_version_reached(
        "тарілки", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1"
    )
    db.set_setting("dish_target_version", "2026.05.01")
    watchdog._check_target_version_reached(
        "тарілки", "2026.05.01", "dish_target_version", "dish_target_notified", "dish1"
    )
    assert len(watchdog.sent) == 2
    assert "2026.05.01" in watchdog.sent[1]


def test_target_version_multiple_candidates_matches_any(watchdog):
    """Кілька версій через кому (різні апаратні ревізії) - матч на
    БУДЬ-ЯКУ з перелічених, не лише першу."""
    db.set_setting("dish_target_version", "2026.03.03.mr75126.1, 2026.03.03.mr75130.1")
    watchdog._check_target_version_reached(
        "тарілки", "2026.03.03.mr75130.1", "dish_target_version", "dish_target_notified", "dish1"
    )
    assert len(watchdog.sent) == 1
    assert "2026.03.03.mr75130.1" in watchdog.sent[0]


def test_target_version_multiple_candidates_none_matching_is_silent(watchdog):
    db.set_setting("dish_target_version", "v1, v2, v3")
    watchdog._check_target_version_reached(
        "тарілки", "v4", "dish_target_version", "dish_target_notified", "dish1"
    )
    assert watchdog.sent == []


def test_target_version_different_dish_id_gets_fresh_notification(watchdog):
    """Найважливіший сценарій: якщо ФІЗИЧНО ІНШИЙ Starlink (інший
    dish_id, напр. після заміни обладнання) збігається з тим самим
    target-значенням, що вже notified для ПОПЕРЕДНЬОГО dish - НЕ
    вважається дублікатом, отримує своє власне, свіже сповіщення."""
    db.set_setting("dish_target_version", "2026.03.03")
    watchdog._check_target_version_reached(
        "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish-OLD"
    )
    assert len(watchdog.sent) == 1

    # Той самий target, та сама версія, АЛЕ ІНШИЙ фізичний dish_id
    watchdog._check_target_version_reached(
        "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish-NEW"
    )
    assert len(watchdog.sent) == 2, "новий dish_id мав отримати власне сповіщення, не заблоковане дедублікацією попереднього"


def test_target_version_same_dish_id_still_deduplicates(watchdog):
    """Контрольний тест: дедублікація ВСЕ ЩЕ працює для ТОГО САМОГО
    dish_id (фіча не зламала звичайну поведінку, лише додала
    ізоляцію МІЖ різними фізичними пристроями)."""
    db.set_setting("dish_target_version", "2026.03.03")
    watchdog._check_target_version_reached(
        "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1"
    )
    watchdog._check_target_version_reached(
        "тарілки", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1"
    )
    assert len(watchdog.sent) == 1


# ---- Комбінована перевірка (_check_both_targets_reached) ----

def test_both_targets_none_set_is_silent(watchdog):
    watchdog._check_both_targets_reached()
    assert watchdog.sent == []


def test_both_targets_only_one_set_is_silent(watchdog):
    db.set_setting("dish_target_version", "v1")
    watchdog._check_both_targets_reached()
    assert watchdog.sent == []


def test_both_targets_set_but_not_matching_is_silent(watchdog):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v0").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r0").to_dict())
    watchdog._check_both_targets_reached()
    assert watchdog.sent == []


def test_both_targets_only_dish_matching_is_silent(watchdog):
    """Найважливіший граничний випадок: ЛИШЕ dish відповідає своєму
    target, router - ще ні. НЕ повинно спамити частковий стан."""
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r0").to_dict())
    watchdog._check_both_targets_reached()
    assert watchdog.sent == []


def test_both_targets_matching_simultaneously_notifies(watchdog):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())
    watchdog._check_both_targets_reached()
    assert len(watchdog.sent) == 1
    assert "v1" in watchdog.sent[0] and "r1" in watchdog.sent[0]


def test_both_targets_repeat_call_does_not_spam(watchdog):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())
    watchdog._check_both_targets_reached()
    watchdog._check_both_targets_reached()
    assert len(watchdog.sent) == 1


def test_both_targets_changing_one_target_resets_dedup(watchdog):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())
    watchdog._check_both_targets_reached()

    db.set_setting("router_target_version", "r2")
    watchdog._check_both_targets_reached()
    assert len(watchdog.sent) == 1  # router ще не оновився до r2

    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r2").to_dict())
    watchdog._check_both_targets_reached()
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
    watchdog._check_both_targets_reached()
    assert len(watchdog.sent) == 1

    watchdog.last_known_dish_id = "dish-NEW"
    watchdog._check_both_targets_reached()
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
