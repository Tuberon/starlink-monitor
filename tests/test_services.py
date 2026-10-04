"""Сервісна логіка (app/services.py): відстеження версій прошивок і бекапи.

Раніше ці тести жили в test_monitor.py і брали фікстуру `watchdog` лише як
приймач сповіщень. Тепер - легка фікстура `sink` (conftest): той самий
інтерфейс (`_notify`, `sent`, `last_known_dish_id`), але без створення
Watchdog - сервісні функції від нього не залежать.
"""
import json
import os
import time
from unittest.mock import patch

import pytest

from app import config, db, services
from app.starlink_client import StarlinkClient


# ---- Дедублікація сповіщень про target-версію (check_target_version_reached) ----

def test_target_version_no_target_set_is_silent(sink):
    services.check_target_version_reached(
                "dish", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    assert sink.sent == []


def test_target_version_mismatch_is_silent(sink):
    db.set_setting("dish_target_version", "2026.04.01")
    services.check_target_version_reached(
                "dish", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    assert sink.sent == []


def test_target_version_match_sends_notification(sink):
    db.set_setting("dish_target_version", "2026.04.01")
    services.check_target_version_reached(
                "dish", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    assert len(sink.sent) == 1
    assert "2026.04.01" in sink.sent[0]


def test_target_version_repeat_match_does_not_spam(sink):
    db.set_setting("dish_target_version", "2026.04.01")
    services.check_target_version_reached(
                "dish", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    services.check_target_version_reached(
                "dish", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    assert len(sink.sent) == 1


def test_target_version_new_target_resets_dedup(sink):
    """Зміна target на НОВЕ значення природно скидає notified-стан
    (порівняння значень, не boolean-прапорець)."""
    db.set_setting("dish_target_version", "2026.04.01")
    services.check_target_version_reached(
                "dish", "2026.04.01", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    db.set_setting("dish_target_version", "2026.05.01")
    services.check_target_version_reached(
                "dish", "2026.05.01", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    assert len(sink.sent) == 2
    assert "2026.05.01" in sink.sent[1]


def test_target_version_multiple_candidates_matches_any(sink):
    """Кілька версій через кому (різні апаратні ревізії) - матч на
    БУДЬ-ЯКУ з перелічених, не лише першу."""
    db.set_setting("dish_target_version", "2026.03.03.mr75126.1, 2026.03.03.mr75130.1")
    services.check_target_version_reached(
                "dish", "2026.03.03.mr75130.1", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    assert len(sink.sent) == 1
    assert "2026.03.03.mr75130.1" in sink.sent[0]


def test_target_version_multiple_candidates_none_matching_is_silent(sink):
    db.set_setting("dish_target_version", "v1, v2, v3")
    services.check_target_version_reached(
                "dish", "v4", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    assert sink.sent == []


def test_target_version_different_dish_id_gets_fresh_notification(sink):
    """Найважливіший сценарій: якщо ФІЗИЧНО ІНШИЙ Starlink (інший
    dish_id, напр. після заміни обладнання) збігається з тим самим
    target-значенням, що вже notified для ПОПЕРЕДНЬОГО dish - НЕ
    вважається дублікатом, отримує своє власне, свіже сповіщення."""
    db.set_setting("dish_target_version", "2026.03.03")
    services.check_target_version_reached(
                "dish", "2026.03.03", "dish_target_version", "dish_target_notified", "dish-OLD",
        sink._notify,
    )
    assert len(sink.sent) == 1

    # Той самий target, та сама версія, АЛЕ ІНШИЙ фізичний dish_id
    services.check_target_version_reached(
                "dish", "2026.03.03", "dish_target_version", "dish_target_notified", "dish-NEW",
        sink._notify,
    )
    assert len(sink.sent) == 2, "новий dish_id мав отримати власне сповіщення, не заблоковане дедублікацією попереднього"


def test_target_version_same_dish_id_still_deduplicates(sink):
    """Контрольний тест: дедублікація ВСЕ ЩЕ працює для ТОГО САМОГО
    dish_id (фіча не зламала звичайну поведінку, лише додала
    ізоляцію МІЖ різними фізичними пристроями)."""
    db.set_setting("dish_target_version", "2026.03.03")
    services.check_target_version_reached(
                "dish", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    services.check_target_version_reached(
                "dish", "2026.03.03", "dish_target_version", "dish_target_notified", "dish1",
        sink._notify,
    )
    assert len(sink.sent) == 1


# ---- Комбінована перевірка (check_both_targets_reached) ----

def test_both_targets_none_set_is_silent(sink):
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    assert sink.sent == []


def test_both_targets_only_one_set_is_silent(sink):
    db.set_setting("dish_target_version", "v1")
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    assert sink.sent == []


def test_both_targets_set_but_not_matching_is_silent(sink):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v0").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r0").to_dict())
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    assert sink.sent == []


def test_both_targets_only_dish_matching_is_silent(sink):
    """Найважливіший граничний випадок: ЛИШЕ dish відповідає своєму
    target, router - ще ні. НЕ повинно спамити частковий стан."""
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r0").to_dict())
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    assert sink.sent == []


def test_both_targets_matching_simultaneously_notifies(sink):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    assert len(sink.sent) == 1
    assert "v1" in sink.sent[0] and "r1" in sink.sent[0]


def test_both_targets_repeat_call_does_not_spam(sink):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    assert len(sink.sent) == 1


def test_both_targets_changing_one_target_resets_dedup(sink):
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)

    db.set_setting("router_target_version", "r2")
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    assert len(sink.sent) == 1  # router ще не оновився до r2

    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r2").to_dict())
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    assert len(sink.sent) == 2
    assert "r2" in sink.sent[1]


def test_both_targets_different_dish_id_gets_fresh_notification(sink):
    """Той самий принцип, що для per-component перевірки: ІНШИЙ
    фізичний Starlink (інший last_known_dish_id) з тим самим
    збігом версій - НЕ вважається дублікатом попереднього."""
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v1")
    db.set_setting("router_target_version", "r1")
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version="v1").to_dict())
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version="r1").to_dict())

    sink.last_known_dish_id = "dish-OLD"
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    assert len(sink.sent) == 1

    sink.last_known_dish_id = "dish-NEW"
    services.check_both_targets_reached(sink.last_known_dish_id, sink._notify)
    assert len(sink.sent) == 2, "новий фізичний Starlink мав отримати власне комбіноване сповіщення"


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
    services.upsert_dish_and_notify(status, lambda t: sent.append(t))
    assert len(sent) == 1
    assert "🔄" in sent[0] and "оновлена" in sent[0]
    assert "відкочена" not in sent[0]


def test_firmware_backward_change_does_not_notify(db_path):
    """Запит користувача: не сповіщати про відкат прошивки взагалі -
    реальний сценарій, знайдений раніше на практиці (SpaceX інколи
    відкочує прошивку), тепер НЕ генерує жодного Telegram-сповіщення,
    лише мовчки оновлює known_devices реальною поточною версією."""
    from app.starlink_client import DishStatus
    sent = []
    db.upsert_known_device_dish("dish1", "rev3", "2026.07.16.mr82459.1")
    status = DishStatus(
        timestamp=time.time(), online=True, uptime_s=100,
        dish_id="dish1", hardware_version="rev3", software_version="2026.05.13.mr80201",
    )
    services.upsert_dish_and_notify(status, lambda t: sent.append(t))
    assert sent == []
    assert db.get_known_device("dish1")["dish_software_version"] == "2026.05.13.mr80201"


def test_firmware_router_backward_change_does_not_notify(db_path):
    """Той самий точний сценарій із реального повідомлення
    користувача (router 2026.07.23.mr82306 -> 2025.10.03.mr61821),
    тепер без сповіщення."""
    from app.starlink_client import RouterInfo
    sent = []
    db.upsert_known_device_router("dish1", "rev2", "2026.07.23.mr82306")
    info = RouterInfo(
        timestamp=time.time(), online=True,
        hardware_version="rev2", software_version="2025.10.03.mr61821",
    )
    services.upsert_router_and_notify(info, "dish1", lambda t: sent.append(t))
    assert sent == []


# ---- check_updates_now(): ігноровані попередження роутера відкидаються в джерелі (starlink_client) ----

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
         patch.object(StarlinkClient, "get_status", return_value=dish), \
         patch.object(StarlinkClient, "get_router_info", return_value=router):
        services.check_updates_now(StarlinkClient(), lambda t: None)

    router_in_db = db.get_router_status()
    assert "thermal_throttle" in router_in_db["active_alerts"]


def test_ignored_router_alerts_contains_expected_value():
    from app import starlink_client
    assert starlink_client.IGNORED_ROUTER_ALERTS == {"wired_mesh_not_using_wan_iface"}


# ---- perform_auto_backup() - ротація, вміст ----

def test_perform_auto_backup_writes_valid_json(sink, tmp_path):
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    services.perform_auto_backup()

    files = os.listdir(config.AUTO_BACKUP_DIR)
    assert len(files) == 1
    with open(os.path.join(config.AUTO_BACKUP_DIR, files[0])) as f:
        content = json.load(f)
    assert content["format_version"] == db.BACKUP_FORMAT_VERSION


def test_perform_auto_backup_restricts_file_permissions(sink, tmp_path):
    """Security fix: backup-файл містить Telegram bot token у
    відкритому вигляді - той самий secret, що вже захищений chmod 600
    для /etc/starlink-monitor/env в install.sh. Без явного chmod тут
    файл покладався б лише на системний umask (типово 0644 -
    читабельний іншими локальними користувачами на тому самому Pi)."""
    import stat
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    services.perform_auto_backup()

    files = os.listdir(config.AUTO_BACKUP_DIR)
    path = os.path.join(config.AUTO_BACKUP_DIR, files[0])
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_perform_auto_backup_rotation_keeps_newest_only(sink, tmp_path):
    """Реальна мета ротації: старі backup-и видаляються, залишаються
    саме НАЙНОВІШІ, не найстаріші чи довільні."""
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    config.AUTO_BACKUP_KEEP_COUNT = 3

    for i in range(5):
        with patch("time.time", return_value=1000000.0 + i * 100):
            services.perform_auto_backup()

    files = sorted(os.listdir(config.AUTO_BACKUP_DIR))
    assert len(files) == 3
    assert files == ["backup-1000200.json", "backup-1000300.json", "backup-1000400.json"]


def test_perform_auto_backup_zero_keep_count_disables_rotation_deletion(sink, tmp_path):
    """AUTO_BACKUP_KEEP_COUNT=0 - крайовий випадок, не має видаляти
    ВСІ файли (0 - не 'нічого не зберігати', а 'ротація вимкнена')."""
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    config.AUTO_BACKUP_KEEP_COUNT = 0
    services.perform_auto_backup()
    services.perform_auto_backup()
    assert len(os.listdir(config.AUTO_BACKUP_DIR)) >= 1


# ---- check_db_integrity_and_notify() ----

def test_check_db_integrity_healthy_sends_no_notification(sink):
    services.check_db_integrity_and_notify(sink._notify)
    assert sink.sent == []


def test_check_db_integrity_corrupted_notifies_and_attempts_backup(sink, tmp_path):
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    with open(config.DB_PATH, "wb") as f:
        f.write(b"not a valid sqlite file" * 50)

    services.check_db_integrity_and_notify(sink._notify)

    assert len(sink.sent) >= 1
    assert "пошкодження" in sink.sent[0].lower()

# ---- компоненти - ідентифікатори, опечатка не проходить тихо ----

@pytest.mark.parametrize("bad", ["тарілки", "тарiлки", "Dish", "роутера", ""])
def test_unknown_component_raises_instead_of_raw_text(db_path, bad):
    """Раніше невідома назва (напр. "тарiлки" з латинською i) тихо ставала
    сирим текстом у сповіщенні; тепер - явна помилка."""
    with pytest.raises(ValueError):
        services.format_firmware_change_message(bad, "a", "b")


def test_component_texts_keep_both_grammatical_forms(db_path):
    """Тексти - побайтово ті самі, що були до переходу на ідентифікатори."""
    assert services.format_firmware_change_message("dish", "a", "b") == "🔄 Прошивка тарілки оновлена: a → b"
    assert services.format_firmware_change_message("router", "a", "b") == "🔄 Прошивка роутера оновлена: a → b"
    assert services.component_text("dish", services.COMPONENT_UPDATE) == "dish"
    assert services.component_text("router", services.COMPONENT_UPDATE) == "роутера"


# ---- db повертає код, текст перекладає споживач (db не залежить від i18n) ----

def test_corrupt_db_notification_is_in_default_language(sink, tmp_path):
    """Мова зберігається В ТІЙ САМІЙ БД: коли файл зіпсований, прочитати її
    неможливо - задокументоване правило "зламана БД -> дефолт uk"."""
    config.AUTO_BACKUP_DIR = str(tmp_path / "backups")
    db.set_setting("ui_language", "en")
    with open(config.DB_PATH, "wb") as f:
        f.write(b"not a valid sqlite file" * 50)
    services.check_db_integrity_and_notify(sink._notify)
    assert "файл не є валідною SQLite-базою: file is not a database" in sink.sent[0]


@pytest.mark.parametrize("lang,expected", [
    ("uk", "файл не є валідною SQLite-базою: file is not a database"),
    ("en", "file is not a valid SQLite database: file is not a database"),
])
def test_describe_integrity_problem_translates_the_code_by_ui_language(db_path, lang, expected):
    db.set_setting("ui_language", lang)
    assert services.describe_integrity_problem(f"{db.NOT_A_DATABASE}: file is not a database") == expected


def test_describe_integrity_problem_passes_other_messages_through():
    assert services.describe_integrity_problem("row 5 missing from index idx_x") == "row 5 missing from index idx_x"
    assert services.describe_integrity_problem("ok") == "ok"


# ---- одночасні перевірки не дублюють сповіщень ----

def test_target_reached_notifies_once_under_concurrent_checks(sink, db_path):
    from test_db import _run_concurrently
    db.set_setting("dish_target_version", "v9")
    _run_concurrently(lambda: services.check_target_version_reached(
        "dish", "v9", "dish_target_version", "dish_target_notified", "dX", sink._notify))
    assert len(sink.sent) == 1


def test_both_targets_notifies_once_under_concurrent_checks(sink, db_path):
    from test_db import _run_concurrently
    from app.starlink_client import DishStatus, RouterInfo
    db.set_setting("dish_target_version", "v9")
    db.set_setting("router_target_version", "r9")
    db.insert_metric(DishStatus(timestamp=1000.0, online=True, software_version="v9", dish_id="dX").to_dict())
    db.set_router_status(RouterInfo(timestamp=1000.0, online=True, software_version="r9").to_dict())
    _run_concurrently(lambda: services.check_both_targets_reached("dX", sink._notify))
    assert len(sink.sent) == 1


def test_firmware_change_notifies_once_under_concurrent_upserts(sink, db_path):
    from test_db import _run_concurrently
    from app.starlink_client import DishStatus
    services.upsert_dish_and_notify(DishStatus(timestamp=1.0, online=True, dish_id="dRACE", hardware_version="rev4", software_version="v1"), sink._notify)
    new = DishStatus(timestamp=2.0, online=True, dish_id="dRACE", hardware_version="rev4", software_version="v2")
    _run_concurrently(lambda: services.upsert_dish_and_notify(new, sink._notify))
    assert len(sink.sent) == 1 and "v2" in sink.sent[0]
