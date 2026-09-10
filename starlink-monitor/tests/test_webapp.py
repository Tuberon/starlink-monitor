"""
Тести для app/db.py (компаратор версій прошивки Starlink) та
app/webapp.py (валідація /api/target-versions - "лише новіші").
"""
import json
import time
from unittest.mock import patch

import pytest

from app import db
from app.webapp import app as flask_app


# ---- Компаратор версій (db.version_key/db.is_older_version) ----

@pytest.mark.parametrize("older,newer", [
    ("2026.03.03.mr75126.1", "2026.03.15.mr80000.1"),  # різні дати
    ("2025.10.03.mr61821", "2026.03.03.mr75126.1"),  # різний рік
])
def test_is_older_version_true_cases(older, newer):
    assert db.is_older_version(older, newer) is True
    assert db.is_older_version(newer, older) is False


def test_is_older_version_identical_versions_not_older():
    v = "2026.03.03.mr75126.1"
    assert db.is_older_version(v, v) is False


def test_is_older_version_same_date_lower_build_number():
    """Той самий день, менший mr-номер - вважається старішою."""
    assert db.is_older_version("2026.03.03.mr75126.1", "2026.03.03.mr80000.1") is True
    assert db.is_older_version("2026.03.03.mr80000.1", "2026.03.03.mr75126.1") is False


def test_is_older_version_nonstandard_format_does_not_crash():
    """Формат без YYYY.MM.DD-префіксу - fallback на посегментне
    порівняння, не падає з винятком."""
    assert db.is_older_version("v1.0", "v2.0") is True
    assert db.is_older_version("", "2026.03.03") is True
    assert db.is_older_version("unknown", "unknown") is False


# ---- API /api/target-versions - валідація "лише новіші" ----

@pytest.fixture
def client(db_path):
    with flask_app.test_client() as c:
        yield c


def _insert_dish_version(version):
    from app.starlink_client import DishStatus
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100, software_version=version).to_dict())


def _insert_router_version(version):
    from app.starlink_client import RouterInfo
    db.set_router_status(RouterInfo(timestamp=time.time(), online=True, software_version=version).to_dict())


def test_target_versions_first_input_equal_to_current_accepted(client):
    _insert_dish_version("2026.03.03.mr75126.1")
    resp = client.post("/api/target-versions", json={"dish_target": "2026.03.03.mr75126.1"})
    assert resp.get_json()["success"] is True


def test_target_versions_older_than_current_rejected(client):
    _insert_dish_version("2026.03.03.mr75126.1")
    client.post("/api/target-versions", json={"dish_target": "2026.03.03.mr75126.1"})

    resp = client.post("/api/target-versions", json={"dish_target": "2025.01.01.mr1000.1"})
    data = resp.get_json()
    assert data["success"] is False
    assert "старіша" in data["message"]

    # target реально НЕ перезаписаний старішим значенням
    check = client.get("/api/target-versions").get_json()
    assert check["dish_target"] == "2026.03.03.mr75126.1"


def test_target_versions_newer_accepted(client):
    _insert_dish_version("2026.03.03.mr75126.1")
    client.post("/api/target-versions", json={"dish_target": "2026.03.03.mr75126.1"})

    resp = client.post("/api/target-versions", json={"dish_target": "2026.04.01.mr80000.1"})
    assert resp.get_json()["success"] is True


def test_target_versions_partial_success_mixed_dish_router(client):
    """dish (новіше, valid) + router (старіше за поточну встановлену,
    invalid) одночасно - валідне поле зберігається, невалідне
    відхиляється окремо (не 'усе або нічого')."""
    _insert_dish_version("2026.03.03.mr75126.1")
    _insert_router_version("2025.10.03")

    resp = client.post("/api/target-versions", json={
        "dish_target": "2026.05.01.mr90000.1",
        "router_target": "2024.01.01",
    })
    data = resp.get_json()
    assert data["success"] is True  # dish зберігся, тому success=True
    assert "роутер" in data["message"]

    check = client.get("/api/target-versions").get_json()
    assert check["dish_target"] == "2026.05.01.mr90000.1"
    assert check["router_target"] is None


def test_target_versions_multiple_candidates_all_newer_accepted(client):
    """Кілька версій через кому (різні апаратні ревізії), усі новіші
    за поточну - весь список приймається."""
    _insert_dish_version("2026.03.03.mr75126.1")
    resp = client.post("/api/target-versions", json={
        "dish_target": "2026.04.01.mr80000.1, 2026.04.01.mr80005.1",
    })
    data = resp.get_json()
    assert data["success"] is True
    check = client.get("/api/target-versions").get_json()
    assert check["dish_target"] == "2026.04.01.mr80000.1, 2026.04.01.mr80005.1"


def test_target_versions_multiple_candidates_one_older_rejects_whole_list(client):
    """Якщо ХОЧ ОДИН кандидат у списку старіший за baseline -
    відхиляється ВЕСЬ список для цього поля (не часткове прийняття
    окремих кандидатів)."""
    _insert_dish_version("2026.03.03.mr75126.1")
    client.post("/api/target-versions", json={"dish_target": "2026.03.03.mr75126.1"})

    resp = client.post("/api/target-versions", json={
        "dish_target": "2026.04.01.mr80000.1, 2020.01.01",
    })
    data = resp.get_json()
    assert data["success"] is False
    assert "2020.01.01" in data["message"]

    check = client.get("/api/target-versions").get_json()
    assert check["dish_target"] == "2026.03.03.mr75126.1"  # старий список не перезаписаний


def test_target_versions_empty_string_clears_field(client):
    """Порожній рядок, явно надісланий - команда ОЧИСТИТИ поле, не
    'нічого не робити' (реальна прогалина: раніше порожній рядок
    просто мовчки ігнорувався, старе значення лишалось назавжди без
    жодного способу його скасувати)."""
    client.post("/api/target-versions", json={"dish_target": "2026.03.03"})
    resp = client.post("/api/target-versions", json={"dish_target": ""})
    data = resp.get_json()
    assert data["success"] is True
    assert "очищено" in data["message"]

    check = client.get("/api/target-versions").get_json()
    assert not check["dish_target"]


def test_target_versions_response_always_has_explicit_message(client):
    """API завжди повертає message, що описує РЕАЛЬНИЙ результат -
    не лише коли є rejected (раніше success-без-rejected випадок
    повертав голий {"success": true} без жодного пояснення)."""
    resp1 = client.post("/api/target-versions", json={"dish_target": "2026.03.03"})
    assert "message" in resp1.get_json()

    resp2 = client.post("/api/target-versions", json={})
    assert resp2.get_json()["message"] == "Без змін"


def test_target_versions_backup_includes_targets_not_notified_state(client):
    """Target-версії (user-налаштування) включені в backup, internal
    dedup-стан (*_notified) - навмисно ні."""
    db.set_setting("dish_target_version", "2026.06.01")
    db.set_setting("dish_target_notified", "2026.06.01")

    backup = client.get("/api/settings-backup").get_json()
    assert backup["dish_target_version"] == "2026.06.01"
    assert "dish_target_notified" not in backup
    assert "both_targets_notified" not in backup


# ---- /api/check-updates - ручна кнопка має ту саму логіку сповіщень, що фоновий watchdog ----

def test_manual_check_updates_sends_target_version_notification(client, monkeypatch):
    """Найважливіший регресійний тест: раніше ручна перевірка ЛИШЕ
    записувала статус у БД, БЕЗ жодного сповіщення, навіть коли версія
    якраз збігалась із target у момент натискання кнопки."""
    from app.starlink_client import DishStatus, RouterInfo
    from app import webapp as webapp_module

    db.set_setting("dish_target_version", "2026.03.03.mr75126.1")

    dish_status = DishStatus(
        timestamp=time.time(), online=True, uptime_s=100,
        software_version="2026.03.03.mr75126.1", dish_id="dish1", hardware_version="rev3",
    )
    router_status = RouterInfo(timestamp=time.time(), online=True, software_version="2025.10.03")

    sent = []
    monkeypatch.setattr("app.telegram_notify.send_message", lambda t: sent.append(t))
    monkeypatch.setattr(webapp_module.client, "get_status", lambda: dish_status)
    monkeypatch.setattr(webapp_module.client, "get_router_info", lambda: router_status)

    resp = client.post("/api/check-updates")
    assert resp.status_code == 200

    assert len(sent) == 1
    assert "2026.03.03.mr75126.1" in sent[0]


def test_manual_check_updates_upserts_known_devices(client, monkeypatch):
    """Ручна перевірка тепер теж оновлює known_devices (раніше -
    жодного запису взагалі, /id у Telegram-боті показував би
    застарілі чи відсутні дані)."""
    from app.starlink_client import DishStatus, RouterInfo
    from app import webapp as webapp_module

    dish_status = DishStatus(
        timestamp=time.time(), online=True, uptime_s=100,
        software_version="v1", dish_id="dish-manual", hardware_version="rev3",
    )
    router_status = RouterInfo(timestamp=time.time(), online=True, software_version="r1")

    monkeypatch.setattr("app.telegram_notify.send_message", lambda t: None)
    monkeypatch.setattr(webapp_module.client, "get_status", lambda: dish_status)
    monkeypatch.setattr(webapp_module.client, "get_router_info", lambda: router_status)

    client.post("/api/check-updates")

    known = db.get_known_device("dish-manual")
    assert known is not None
    assert known["dish_software_version"] == "v1"
    assert known["router_software_version"] == "r1"


def test_backup_includes_known_devices(client):
    db.upsert_known_device_dish("dish-AAA", "rev3", "v1.0")
    backup = client.get("/api/settings-backup").get_json()
    assert len(backup["known_devices"]) == 1
    assert backup["known_devices"][0]["dish_id"] == "dish-AAA"


def test_restore_known_devices_via_api(client):
    payload = {
        "format_version": 1,
        "known_devices": [
            {"dish_id": "dish-XYZ", "first_seen_ts": 1000.0, "last_seen_ts": 2000.0,
             "dish_software_version": "v3.0"},
        ],
    }
    resp = client.post("/api/settings-restore", json=payload)
    data = resp.get_json()
    assert data["success"] is True
    assert "1 нових з 1" in data["message"]
    assert db.get_known_device("dish-XYZ") is not None


# ---- version_channel / channel-aware валідація - різні апаратні ревізії Starlink ----

def test_version_channel_extracts_letter_prefix():
    from app.db import version_channel
    assert version_channel("2026.07.06.cr81950.49600") == "cr"
    assert version_channel("2026.07.19.mr82648") == "mr"
    assert version_channel("2020.01.01") is None
    assert version_channel("") is None


def test_target_versions_different_build_channels_not_compared(client):
    """Точний сценарій, знайдений користувачем на практиці: 'mr' і 'cr'
    build-канали можуть відповідати РІЗНИМ апаратним ревізіям з
    незалежними датами випуску одночасно - порівнювати дату напряму
    між каналами дає хибне відхилення "старіша версія"."""
    _insert_dish_version("2026.07.19.mr82648")
    resp = client.post("/api/target-versions", json={"dish_target": "2026.07.06.cr81950.49600"})
    data = resp.get_json()
    assert data["success"] is True, f"різні build-канали (mr/cr) не мають блокувати одне одного: {data}"


def test_target_versions_same_channel_still_rejects_older(client):
    """Контрольний тест: У МЕЖАХ ТОГО САМОГО каналу (mr) захист від
    відкату все ще коректно працює - фіча не послабила звичайну
    валідацію, лише додала ізоляцію МІЖ різними каналами."""
    _insert_dish_version("2026.07.19.mr82648")
    resp = client.post("/api/target-versions", json={"dish_target": "2026.03.03.mr70000"})
    data = resp.get_json()
    assert data["success"] is False
    assert "mr70000" in data["message"]


def test_target_versions_candidate_without_channel_compares_globally(client):
    """Edge case, знайдений власним тестом одразу після першої
    реалізації channel-aware логіки: candidate БЕЗ явного каналу
    (проста дата, без mr/cr-суфікса) - це НЕ "свій окремий канал",
    порівнюється з УСІМА baseline звичайно (fallback), а не хибно
    приймається як "непорівнюваний з рештою"."""
    _insert_dish_version("2026.07.19.mr82648")
    resp = client.post("/api/target-versions", json={"dish_target": "2020.01.01"})
    data = resp.get_json()
    assert data["success"] is False, "версія без каналу (2020.01.01) мала порівнятись глобально й відхилитись"


def test_target_versions_matching_current_installed_always_accepted_after_rollback(client):
    """Точний сценарій із реального повідомлення користувача: target
    раніше введений як 07-24, ПОТІМ dish РЕАЛЬНО відкотився (SpaceX-
    side rollback, вже підтверджений раніше) на 07-19 - user хоче
    узгодити target із ФАКТИЧНОЮ реальністю. Candidate, що ТОЧНО
    збігається з поточною встановленою версією, завжди приймається,
    незалежно від старішого попереднього target."""
    _insert_dish_version("2026.07.24.mr83021")
    client.post("/api/target-versions", json={"dish_target": "2026.07.24.mr83021"})

    _insert_dish_version("2026.07.19.mr82648")  # реальний rollback
    resp = client.post("/api/target-versions", json={"dish_target": "2026.07.19.mr82648"})
    data = resp.get_json()
    assert data["success"] is True, f"узгодження з фактично встановленою версією мало прийнятись: {data}"

    check = client.get("/api/target-versions").get_json()
    assert check["dish_target"] == "2026.07.19.mr82648"


def test_target_versions_real_typo_still_rejected_after_rollback_fix(client):
    """Контрольний тест: фікс "завжди дозволяти поточну версію" НЕ
    послабив захист від СПРАВЖНІХ описок (candidate, що НЕ збігається
    ні з поточною версією, ні є новішим за попередній target)."""
    _insert_dish_version("2026.07.24.mr83021")
    client.post("/api/target-versions", json={"dish_target": "2026.07.24.mr83021"})

    resp = client.post("/api/target-versions", json={"dish_target": "2026.01.01.mr50000"})
    data = resp.get_json()
    assert data["success"] is False, "справжня описка (не поточна версія, не новіша) мала відхилитись"


# ---- /healthz - зовнішній моніторинг (UptimeRobot тощо) ----

def test_healthz_no_data_yet_returns_200(client):
    """Реальний сценарій: щойно встановлений Pi, watchdog ще жодного
    разу не записав метрику - НЕ має вважатись "degraded" (503),
    процес просто щойно стартував."""
    resp = client.get("/healthz")
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["checks"]["watchdog"] == "no data yet"


def test_healthz_fresh_metric_returns_200(client):
    from app.starlink_client import DishStatus
    db.insert_metric(DishStatus(timestamp=time.time(), online=True, uptime_s=100).to_dict())
    resp = client.get("/healthz")
    data = resp.get_json()
    assert resp.status_code == 200
    assert data["status"] == "ok"
    assert "ok" in data["checks"]["watchdog"]


def test_healthz_stale_metric_returns_503(client):
    """Головна мета /healthz: watchdog реально завис (deadlock, не
    crash) - метрика стара, свіжих немає, зовнішній моніторинг МАЄ
    отримати 503, не тихе 200."""
    from app import config
    from app.starlink_client import DishStatus
    stale_ts = time.time() - (config.POLL_INTERVAL_SEC * 3 + config.DISH_METRICS_BATCH_INTERVAL_SEC + 60)
    db.insert_metric(DishStatus(timestamp=stale_ts, online=True, uptime_s=100).to_dict())
    resp = client.get("/healthz")
    data = resp.get_json()
    assert resp.status_code == 503
    assert data["status"] == "degraded"
    assert "stale" in data["checks"]["watchdog"]


# ---- /api/reboot-dish - реальна фізична дія ----

def test_reboot_dish_success_logs_event_and_notifies(client):
    from app import webapp
    with patch.object(webapp.client, "reboot_dish", return_value=(True, "ok")), \
         patch("app.telegram_notify.send_message") as mock_notify:
        resp = client.post("/api/reboot-dish")
    data = resp.get_json()
    assert data["success"] is True
    events = db.get_recent_events(10)
    assert any(e["kind"] == "dish_reboot" for e in events)
    mock_notify.assert_called_once()
    assert "перезавантажено" in mock_notify.call_args[0][0]


def test_reboot_dish_failure_reports_error_message(client):
    from app import webapp
    with patch.object(webapp.client, "reboot_dish", return_value=(False, "timeout")), \
         patch("app.telegram_notify.send_message") as mock_notify:
        resp = client.post("/api/reboot-dish")
    data = resp.get_json()
    assert data["success"] is False
    assert "Не вдалося" in mock_notify.call_args[0][0]


# ---- /api/telegram-config - маскування token (безпекова логіка) ----

def test_telegram_config_get_no_token_set(client):
    resp = client.get("/api/telegram-config")
    data = resp.get_json()
    assert data["token_set"] is False
    assert data["token_masked"] == ""


def test_telegram_config_get_masks_long_token(client):
    """Реальна мета: повний token НІКОЛИ не має з'являтись у відповіді
    (secret, видимий у мережевому інспекторі браузера/консолі)."""
    from app import telegram_notify
    telegram_notify.set_telegram_config(token="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11", chat_ids=None, enabled=None)
    resp = client.get("/api/telegram-config")
    data = resp.get_json()
    assert data["token_set"] is True
    assert "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11" not in str(data)
    assert data["token_masked"].startswith("123456")
    assert "..." in data["token_masked"]


def test_telegram_config_get_masks_short_token_fully(client):
    """Короткий token (<=10 символів) - взагалі не показує жодної
    його частини (навіть перші 6 символів вже забагато для дуже
    короткого secret), просто "***"."""
    from app import telegram_notify
    telegram_notify.set_telegram_config(token="short1", chat_ids=None, enabled=None)
    resp = client.get("/api/telegram-config")
    data = resp.get_json()
    assert data["token_masked"] == "***"
    assert "short1" not in str(data)


def test_telegram_config_post_parses_comma_separated_string(client):
    resp = client.post("/api/telegram-config", json={"chat_ids": "111, 222 ,333"})
    assert resp.get_json()["success"] is True
    from app import telegram_notify
    _, chat_ids, _ = telegram_notify.get_telegram_config()
    assert chat_ids == ["111", "222", "333"]


def test_telegram_config_post_accepts_list_directly(client):
    resp = client.post("/api/telegram-config", json={"chat_ids": ["444", "555"]})
    assert resp.get_json()["success"] is True
    from app import telegram_notify
    _, chat_ids, _ = telegram_notify.get_telegram_config()
    assert chat_ids == ["444", "555"]


# ---- /api/auto-reboot ----

def test_set_auto_reboot_toggle_on(client):
    resp = client.post("/api/auto-reboot", json={"enabled": True})
    data = resp.get_json()
    assert data["success"] is True
    assert data["enabled"] is True
    assert db.get_auto_reboot_enabled() is True


def test_set_auto_reboot_toggle_off(client):
    client.post("/api/auto-reboot", json={"enabled": True})
    resp = client.post("/api/auto-reboot", json={"enabled": False})
    assert resp.get_json()["enabled"] is False
    assert db.get_auto_reboot_enabled() is False


# ---- /api/settings-backup + /api/settings-restore - повний цикл ----

def test_backup_then_restore_round_trip(client):
    from app import telegram_notify
    telegram_notify.set_telegram_config(token="test-token-123456789", chat_ids=["777"], enabled=True)
    db.set_auto_reboot_enabled(True)

    backup_resp = client.get("/api/settings-backup")
    backup_data = backup_resp.get_json()
    assert backup_data["telegram_bot_token"] == "test-token-123456789"

    telegram_notify.set_telegram_config(token="", chat_ids=[], enabled=False)
    db.set_auto_reboot_enabled(False)

    restore_resp = client.post("/api/settings-restore", data=json.dumps(backup_data), content_type="application/json")
    assert restore_resp.get_json()["success"] is True

    token, chat_ids, enabled = telegram_notify.get_telegram_config()
    assert token == "test-token-123456789"
    assert chat_ids == ["777"]
    assert db.get_auto_reboot_enabled() is True


def test_restore_rejects_payload_without_format_version(client):
    resp = client.post("/api/settings-restore", data=json.dumps({"telegram_bot_token": "x"}), content_type="application/json")
    data = resp.get_json()
    assert data["success"] is False
    assert "формат" in data["message"].lower()


# ---- Решта restore-гілок та env-config endpoints ----

def test_restore_applies_env_params(client):
    from app import monitor
    backup = monitor.build_backup_dict()
    backup["env_params"] = {"STARLINK_POLL_INTERVAL": "15"}
    resp = client.post("/api/settings-restore", data=json.dumps(backup), content_type="application/json")
    data = resp.get_json()
    assert data["success"] is True
    assert "параметри моніторингу" in data["message"]


def test_restore_applies_target_versions(client):
    from app import monitor
    backup = monitor.build_backup_dict()
    backup["dish_target_version"] = "2026.01.01.mr1"
    resp = client.post("/api/settings-restore", data=json.dumps(backup), content_type="application/json")
    assert resp.get_json()["success"] is True
    assert db.get_setting("dish_target_version") == "2026.01.01.mr1"


def test_restore_applies_known_devices(client):
    from app import monitor
    backup = monitor.build_backup_dict()
    backup["known_devices"] = [{
        "dish_id": "restored-dish", "first_seen_ts": time.time(), "last_seen_ts": time.time(),
    }]
    resp = client.post("/api/settings-restore", data=json.dumps(backup), content_type="application/json")
    assert resp.get_json()["success"] is True
    assert db.get_known_device("restored-dish") is not None


def test_restore_handles_malformed_payload_without_crashing(client):
    """Реальний edge case: payload з format_version, але з іншими
    полями зіпсованого типу (напр. known_devices - рядок, не список).
    Головна гарантія - НЕ падає з 500 (виняток спіймано і повернутий
    як success=False), а не конкретне значення success для ЦЬОГО
    прикладу (known_devices="not-a-list" ітерується як символи, кожен
    ігнорується merge_known_devices() через відсутність dish_id -
    толерантно, без винятку)."""
    resp = client.post(
        "/api/settings-restore",
        data=json.dumps({"format_version": 3, "known_devices": "not-a-list"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert "success" in resp.get_json()


def test_restore_exception_path_returns_success_false(client):
    """Реальний виняток (не толерантний edge case) МАЄ повернути
    success=False, не поширюватись як 500."""
    resp = client.post(
        "/api/settings-restore",
        data=json.dumps({"format_version": 3, "env_params": "not-a-dict"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is False


# ---- /api/env-config ----

def test_env_config_get_returns_all_params_with_categories(client):
    resp = client.get("/api/env-config")
    data = resp.get_json()
    assert len(data["params"]) > 0
    assert "category_labels" in data


def test_env_config_post_saves_and_logs_event(client):
    resp = client.post("/api/env-config", json={"values": {"STARLINK_POLL_INTERVAL": "20"}})
    data = resp.get_json()
    assert data["success"] is True
    events = db.get_recent_events(10)
    assert any(e["kind"] == "env_config_updated" for e in events)


def test_env_config_post_validation_error_reports_failure(client):
    resp = client.post("/api/env-config", json={"values": {"STARLINK_POLL_INTERVAL": "not-a-number"}})
    data = resp.get_json()
    assert data["success"] is False
