"""
Тести для app/db.py - downsampling старих метрик. Найважливіший
сценарій: get_metrics_chart_data() має бути БЕЗШОВНОЮ на межі cutoff
між raw (metrics) і downsampled (metrics_downsampled) даними - UNION
ALL з тим самим cutoff для обох частин природно забезпечує це, без
явного знання порогу downsample у самому запиті.
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


def test_downsample_moves_old_data_and_keeps_recent(db_path):
    """Дані старші за DOWNSAMPLE_AFTER_DAYS переносяться в
    metrics_downsampled і видаляються з metrics, недавні лишаються
    недоторканими в metrics."""
    config.DOWNSAMPLE_AFTER_DAYS = 3
    config.DOWNSAMPLE_BUCKET_SEC = 300
    now = time.time()

    _insert_metrics(200, 60, now - 4 * 86400)  # старі (>3 днів)
    _insert_metrics(200, 60, now - 1 * 86400)  # недавні (<3 днів)

    with db.get_conn() as conn:
        count_before = conn.execute("SELECT COUNT(*) as c FROM metrics").fetchone()["c"]
    assert count_before == 400

    buckets = db.downsample_old_metrics()
    assert buckets > 0

    with db.get_conn() as conn:
        count_after_raw = conn.execute("SELECT COUNT(*) as c FROM metrics").fetchone()["c"]
        count_downsampled = conn.execute("SELECT COUNT(*) as c FROM metrics_downsampled").fetchone()["c"]
    assert count_after_raw == 200  # лише недавні лишились
    assert count_downsampled == buckets


def test_downsample_is_idempotent(db_path):
    """Повторний виклик після того, як старих raw-рядків уже немає -
    не дублює дані (PRIMARY KEY(bucket_ts) + ON CONFLICT DO NOTHING)."""
    config.DOWNSAMPLE_AFTER_DAYS = 3
    now = time.time()
    _insert_metrics(100, 60, now - 4 * 86400)

    first_call = db.downsample_old_metrics()
    second_call = db.downsample_old_metrics()

    assert first_call > 0
    assert second_call == 0
    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) as c FROM metrics_downsampled").fetchone()["c"]
    assert count == first_call


def test_chart_data_seamless_across_downsample_boundary(db_path):
    """Найважливіший сценарій: графік за 7 днів дає ПРИБЛИЗНО ту саму
    кількість точок і НУЛЬ прогалин до і після downsampling - UNION
    ALL з тим самим cutoff для обох таблиць природно забезпечує
    безшовний перехід без явного знання порогу downsample у запиті."""
    config.DOWNSAMPLE_AFTER_DAYS = 3
    config.POLL_INTERVAL_SEC = 60
    now = time.time()
    _insert_metrics(7 * 24 * 60 // 60, 60, now - 7 * 86400, downlink=50.0)

    data_before = db.get_metrics_chart_data(hours=7 * 24, target_points=150)
    points_before = len(data_before)

    db.downsample_old_metrics()

    data_after = db.get_metrics_chart_data(hours=7 * 24, target_points=150)
    points_after = len(data_after)

    assert abs(points_after - points_before) <= 5
    gaps = sum(1 for d in data_after if d["downlink_mbps"] is None)
    assert gaps == 0


def test_prune_old_cleans_both_raw_and_downsampled(db_path):
    """prune_old() видаляє застарілі дані з ОБОХ таблиць (raw metrics
    за retention-межею, і metrics_downsampled теж, не лише raw)."""
    config.HISTORY_RETENTION_DAYS = 30
    now = time.time()

    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO metrics_downsampled (bucket_ts, sample_count, online_fraction, downlink_mbps) "
            "VALUES (?, ?, ?, ?)",
            (now - 35 * 86400, 30, 1.0, 50.0),  # застарілий (>30д)
        )
        conn.execute(
            "INSERT INTO metrics_downsampled (bucket_ts, sample_count, online_fraction, downlink_mbps) "
            "VALUES (?, ?, ?, ?)",
            (now - 10 * 86400, 30, 1.0, 50.0),  # актуальний
        )

    db.prune_old()

    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) as c FROM metrics_downsampled").fetchone()["c"]
    assert count == 1


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
