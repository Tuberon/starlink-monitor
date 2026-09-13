"""
Тести для app/db.py - prune старих метрик, злиття історії
відомих пристроїв, перевірка цілісності БД.
"""
import time

from app import config, db
from app.starlink_client import DishStatus


def _insert_metrics(count, interval_s, base_ts, downlink=50.0):
    for i in range(count):
        ts = base_ts + i * interval_s
        status = DishStatus(
            timestamp=ts, online=True, uptime_s=i * 10,
            downlink_mbps=downlink, uplink_mbps=10.0,
            ping_latency_ms=30.0, ping_drop_ratio=0.01, obstruction_fraction=0.0,
        )
        db.insert_metric(status.to_dict())


def test_prune_old_removes_expired_raw_metrics_keeps_recent(db_path):
    """prune_old() видаляє raw-метрики старші за retention-межу,
    недавні лишає недоторканими."""
    config.HISTORY_RETENTION_DAYS = 30
    now = time.time()
    _insert_metrics(50, 60, now - 35 * 86400)  # застарілі (>30д)
    _insert_metrics(50, 60, now - 5 * 86400)   # актуальні

    with db.get_conn() as conn:
        count_before = conn.execute("SELECT COUNT(*) as c FROM metrics").fetchone()["c"]
    assert count_before == 100

    db.prune_old()

    with db.get_conn() as conn:
        count_after = conn.execute("SELECT COUNT(*) as c FROM metrics").fetchone()["c"]
    assert count_after == 50


def test_prune_old_days_zero_is_not_silently_replaced_by_default(db_path):
    """Реальний баг: `days = days or config.HISTORY_RETENTION_DAYS`
    робив явно передане 0 (falsy в Python) нерозрізненим від
    "аргумент не переданий" - prune_old(days=0) мовчки підмінявся
    дефолтним config.HISTORY_RETENTION_DAYS замість реального
    видалення всієї історії."""
    config.HISTORY_RETENTION_DAYS = 30
    db.insert_event("test", "щойно вставлена подія", success=True)

    db.prune_old(days=0)

    assert db.get_recent_events(10) == []


# ---- known_devices - історія відомих Starlink-пристроїв (backup/restore) ----

def test_merge_known_devices_adds_new_on_empty_db(db_path):
    devices = [
        {"dish_id": "dish-AAA", "first_seen_ts": 1000.0, "last_seen_ts": 2000.0,
         "dish_software_version": "v1.0"},
        {"dish_id": "dish-BBB", "first_seen_ts": 1000.0, "last_seen_ts": 2000.0,
         "dish_software_version": "v2.0"},
    ]
    added = db.merge_known_devices(devices)
    assert added == 2
    assert len(db.get_all_known_devices()) == 2


def test_merge_known_devices_does_not_overwrite_existing(db_path):
    """Найважливіший сценарій: dish_id, що вже є локально, НЕ
    перезаписується даними з backup, навіть якщо локальний запис
    об'єктивно новіший (backup - потенційно застарілий знімок)."""
    db.upsert_known_device_dish("dish-AAA", "rev3", "v99.0-NEWER")

    added = db.merge_known_devices([
        {"dish_id": "dish-AAA", "first_seen_ts": 1000.0, "last_seen_ts": 2000.0,
         "dish_software_version": "v1.0-OLD"},
    ])

    assert added == 0
    current = db.get_known_device("dish-AAA")
    assert current["dish_software_version"] == "v99.0-NEWER"


def test_merge_known_devices_ignores_entries_without_dish_id(db_path):
    added = db.merge_known_devices([{"first_seen_ts": 1000.0}])
    assert added == 0
    assert db.get_all_known_devices() == []


# ---- check_integrity() - PRAGMA quick_check ----

def test_check_integrity_healthy_db_returns_ok(db_path):
    ok, message = db.check_integrity()
    assert ok is True
    assert message == "ok"


def test_check_integrity_detects_fully_invalid_file(db_path):
    """Реальний edge case, знайдений живим тестом під час реалізації:
    файл, що ВЗАГАЛІ не є SQLite (не просто пошкоджені дані всередині),
    змушує PRAGMA quick_check кинути sqlite3.DatabaseError замість
    повернення результату - check_integrity() МАЄ це ловити і
    повертати (False, message), не поширювати виняток."""
    with open(db_path, "wb") as f:
        f.write(b"not a valid sqlite file" * 50)
    ok, message = db.check_integrity()
    assert ok is False
    assert "не є валідною SQLite-базою" in message


# ---- set_activity_callback() - хук для LED активності SD-картки ----

def test_activity_callback_called_after_successful_commit(db_path):
    calls = []
    db.set_activity_callback(lambda: calls.append(1))
    try:
        db.insert_event("test", "тест", success=True)
        assert len(calls) >= 1, "callback мав спрацювати після успішного commit"
    finally:
        db.set_activity_callback(None)


def test_activity_callback_not_called_on_exception(db_path):
    """Реальна мета: LED НЕ має блимати при провалених/rollback
    транзакціях - лише при реально успішному записі."""
    calls = []
    db.set_activity_callback(lambda: calls.append(1))
    try:
        try:
            with db.get_conn() as conn:
                conn.execute("SELECT * FROM nonexistent_table")
        except Exception:
            pass
        assert calls == [], "callback НЕ мав спрацювати при винятку"
    finally:
        db.set_activity_callback(None)


def test_activity_callback_exception_does_not_break_write(db_path):
    """Якщо сам callback провалюється (напр. LED-бібліотека мала
    проблему) - запис у БД МАЄ все одно пройти успішно."""
    db.set_activity_callback(lambda: (_ for _ in ()).throw(RuntimeError("LED зламався")))
    try:
        db.insert_event("test", "запис попри зламаний callback", success=True)
        events = db.get_recent_events(10)
        assert any(e["message"] == "запис попри зламаний callback" for e in events)
    finally:
        db.set_activity_callback(None)


def test_no_activity_callback_is_safe_default(db_path):
    """Дефолтний стан (None) - запис МАЄ працювати звично, без жодної
    помилки через відсутність callback."""
    db.set_activity_callback(None)
    db.insert_event("test", "звичайний запис без LED", success=True)
