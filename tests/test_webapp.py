"""
Тести для app/webapp.py - компаратор версій прошивки Starlink
(_version_key/_is_older_version, толерантний до формату, не строгий
semver) та валідація /api/target-versions ("лише новіші").
"""
import time

import pytest

from app import db
from app.webapp import _is_older_version, app as flask_app


# ---- Компаратор версій (_is_older_version) ----

@pytest.mark.parametrize("older,newer", [
    ("2026.03.03.mr75126.1", "2026.03.15.mr80000.1"),  # різні дати
    ("2025.10.03.mr61821", "2026.03.03.mr75126.1"),  # різний рік
])
def test_is_older_version_true_cases(older, newer):
    assert _is_older_version(older, newer) is True
    assert _is_older_version(newer, older) is False


def test_is_older_version_identical_versions_not_older():
    v = "2026.03.03.mr75126.1"
    assert _is_older_version(v, v) is False


def test_is_older_version_same_date_lower_build_number():
    """Той самий день, менший mr-номер - вважається старішою."""
    assert _is_older_version("2026.03.03.mr75126.1", "2026.03.03.mr80000.1") is True
    assert _is_older_version("2026.03.03.mr80000.1", "2026.03.03.mr75126.1") is False


def test_is_older_version_nonstandard_format_does_not_crash():
    """Формат без YYYY.MM.DD-префіксу - fallback на посегментне
    порівняння, не падає з винятком."""
    assert _is_older_version("v1.0", "v2.0") is True
    assert _is_older_version("", "2026.03.03") is True
    assert _is_older_version("unknown", "unknown") is False


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
