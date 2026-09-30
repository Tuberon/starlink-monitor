"""
Тести для app/db.py - prune старих метрик, злиття історії
відомих пристроїв, перевірка цілісності БД.
"""
import time

import pytest

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


def test_upsert_known_device_rejects_unknown_component_without_assert(db_path):
    """Захист f-рядка з назвами колонок у SQL - через if/raise, не assert
    (`python -O` прибрав би assert повністю)."""
    import pytest
    with pytest.raises(ValueError):
        db._upsert_known_device("d1", "dish; DROP TABLE known_devices", "h", "s")


# ---- "якір": WAL живе весь час роботи процесу ----

def test_anchor_keeps_wal_alive_and_close_checkpoints(db_path):
    """Без якоря кожне закриття останнього з'єднання робило повний
    checkpoint і видаляло -wal (fsync на кожен запис)."""
    import os
    wal = db_path + "-wal"
    db.insert_event("t", "x", success=True)
    assert not os.path.exists(wal)                 # поведінка без якоря
    db.open_anchor()
    try:
        db.insert_event("t", "y", success=True)
        assert os.path.exists(wal)                 # WAL не видалено
        assert db.check_integrity()[0] is True
        with db.get_conn() as c:
            c.execute("VACUUM")                    # щоденне обслуговування працює з якорем
    finally:
        db.close_anchor()
    assert not os.path.exists(wal)                 # штатне завершення - checkpoint
    assert [e["message"] for e in db.get_recent_events(10)][:2] == ["y", "x"]


def test_anchor_open_is_idempotent_and_close_is_safe(db_path):
    db.close_anchor()                              # без відкритого - без помилки
    db.open_anchor()
    first = db._anchor
    db.open_anchor()
    assert db._anchor is first
    db.close_anchor()
    assert db._anchor is None


@pytest.mark.parametrize("call", [
    lambda: db.set_setting("ui_language", "en"),
    lambda: db._upsert_known_device("d1", "dish", "rev3", "v1"),
    lambda: db.merge_known_devices([{"dish_id": "d2", "component": "dish", "hardware_version": "h",
                                     "software_version": "s", "first_seen_ts": 1, "last_seen_ts": 2}]),
])
def test_settings_and_device_history_writes_are_durable(db_path, call):
    """Налаштування й історія пристроїв синхронізуються одразу
    (synchronous=FULL), не через ~30 с, як метрики."""
    from unittest.mock import patch
    real = db.get_conn
    flags = []
    def spy(*a, **kw):
        flags.append(kw.get("durable", False))
        return real(*a, **kw)
    with patch.object(db, "get_conn", side_effect=spy):
        call()
    assert True in flags


# ---- міграція схеми на БД, створеній СТАРІШОЮ версією (шлях оновлення на реальному Pi) ----

def _old_schema_without_migrated_columns():
    """Поточна схема мінус колонки, що додаються міграціями init_db()."""
    import re
    migrated = {
        "metrics": ("update_state", "update_progress_pct", "update_requires_reboot", "update_install_pending",
                    "active_alerts", "hardware_version", "dish_id"),
        "router_status": ("update_state", "update_progress_pct", "update_install_pending", "active_alerts", "clients"),
        "events": ("count", "last_ts"),
    }
    schema = db.SCHEMA
    for table, cols in migrated.items():
        start = schema.index(f"CREATE TABLE IF NOT EXISTS {table} (")
        end = schema.index(");", start)
        body = schema[start:end]
        for col in cols:
            body = re.sub(rf"\n\s*{col} [^\n]*", "", body)
        body = re.sub(r",(\s*)$", r"\1", body.rstrip()) + "\n"
        # остання лишена колонка не має закінчуватись комою
        body = re.sub(r",\s*$", "", body.rstrip()) + "\n"
        schema = schema[:start] + body + schema[end:]
    return schema


def test_init_db_migrates_old_database_preserving_data(db_path):
    """CREATE TABLE IF NOT EXISTS не чіпає наявну таблицю - нові колонки
    додаються ALTER TABLE. Ця гілка не виконувалась жодним тестом, хоча саме
    вона працює при оновленні на Pi з уже наявним history.db."""
    import os
    import sqlite3
    os.remove(db_path)
    conn = sqlite3.connect(db_path)
    conn.executescript(_old_schema_without_migrated_columns())
    conn.execute("INSERT INTO metrics (ts, online, state, uptime_s) VALUES (100.0, 1, 'CONNECTED', 55)")
    conn.execute("INSERT INTO events (ts, kind, message, success) VALUES (200.0, 'dish_reboot', 'старий запис', 1)")
    conn.execute("INSERT INTO router_status (id, ts, online, software_version) VALUES (1, 300.0, 1, 'r-old')")
    conn.commit()
    old_cols = {r[1] for r in conn.execute("PRAGMA table_info(events)")}
    conn.close()
    assert "count" not in old_cols and "last_ts" not in old_cols        # тест справді має СТАРУ схему

    db.init_db()

    fresh = sqlite3.connect(":memory:")
    fresh.executescript(db.SCHEMA)
    with db.get_conn() as c:
        for table in ("metrics", "events", "router_status"):
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            assert have == {r[1] for r in fresh.execute(f"PRAGMA table_info({table})")}, table
    # старі дані на місці, нові колонки мають безпечні значення
    latest = db.get_latest_metric()
    assert latest["state"] == "CONNECTED" and latest["uptime_s"] == 55
    event = db.get_recent_events(5)[0]
    assert event["message"] == "старий запис" and event["count"] == 1
    assert db.get_router_status()["software_version"] == "r-old"
    # і база працює далі: нові записи з новими колонками
    db.insert_metric({"timestamp": 400.0, "online": True, "dish_id": "d1", "update_state": "IDLE"})
    assert db.get_latest_metric()["dish_id"] == "d1"
    db.insert_event("dish_reboot", "новий запис", success=True)


def test_init_db_is_idempotent(db_path):
    db.init_db()
    db.init_db()
    db.insert_event("t", "x", success=True)
    assert len(db.get_recent_events(5)) == 1


def test_schema_change_in_unmigrated_table_requires_migration(db_path):
    """system_metrics, known_devices і settings не мають міграції колонок
    (metrics, router_status, events - мають). Змінили їхню схему - база на
    Pi (history.db) НЕ отримає нову колонку: CREATE TABLE IF NOT EXISTS не
    чіпає наявну таблицю, і запис/читання впаде "no such column" одразу
    після оновлення. Змінюючи ці таблиці: додайте виклик
    _migrate_table_columns в init_db(), потім оновіть цей список."""
    expected = {
        "system_metrics": ["id", "ts", "uptime_s", "cpu_percent", "mem_total_mb", "mem_used_mb", "mem_free_mb",
                           "disk_total_gb", "disk_used_gb", "disk_free_gb", "temp_c"],
        "known_devices": ["dish_id", "first_seen_ts", "last_seen_ts", "dish_hardware_version",
                          "dish_software_version", "dish_software_updated_ts", "router_hardware_version",
                          "router_software_version", "router_software_updated_ts"],
        "settings": ["key", "value"],
    }
    with db.get_conn() as c:
        for table, columns in expected.items():
            assert [r["name"] for r in c.execute(f"PRAGMA table_info({table})")] == columns, table


# ---- VACUUM лише коли вільних сторінок багато (раніше: щодоби І на кожен запуск сервісу) ----

def _fill_metrics(rows):
    from app.starlink_client import DishStatus
    now = 1_000_000.0
    db.insert_metrics_batch([
        DishStatus(timestamp=now + i, online=True, uptime_s=i, dish_id="ut51c88d90-02724404-198fc3bd",
                   software_version="2026.09.14.mr86848", downlink_mbps=50.0 + i % 9).to_dict()
        for i in range(rows)
    ])
    return now


def _pages():
    with db.get_conn() as c:
        return c.execute("PRAGMA page_count").fetchone()[0], c.execute("PRAGMA freelist_count").fetchone()[0]


def test_vacuum_skipped_when_few_free_pages(db_path):
    """Після щоденного prune вільно ~20% - нові записи їх заповнять; VACUUM
    переписав би всю БД (вимір: 13.7 МБ записів на 6.5 МБ бази) без користі."""
    now = _fill_metrics(6000)
    with db.get_conn() as c:
        c.execute("DELETE FROM metrics WHERE ts < ?", (now + 600,))       # ~10% рядків
    pages, free = _pages()
    assert 0 < free / pages < db._VACUUM_MIN_FREE_RATIO
    assert db.vacuum_and_analyze() is False
    after_pages, after_free = _pages()
    # файл не переписано: розмір той самий, вільні сторінки лишились (VACUUM дав би 0);
    # на 1 сторінку менше вільних - це ANALYZE створив таблицю sqlite_stat1
    assert after_pages == pages and after_free >= free - 1 and after_free > 0


def test_vacuum_runs_when_most_pages_are_free(db_path):
    """Наприклад, після зменшення HISTORY_DAYS: тоді файл справді треба стиснути."""
    import os
    now = _fill_metrics(6000)
    with db.get_conn() as c:
        c.execute("DELETE FROM metrics WHERE ts < ?", (now + 5000,))      # ~83% рядків
    with db.get_conn() as c:
        c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    before = os.path.getsize(db_path)
    pages, free = _pages()
    assert free / pages >= db._VACUUM_MIN_FREE_RATIO
    assert db.vacuum_and_analyze() is True
    assert _pages()[1] == 0 and os.path.getsize(db_path) < before


def test_vacuum_and_analyze_on_fresh_database_is_safe(db_path):
    assert db.vacuum_and_analyze() is False
