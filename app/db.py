"""SQLite шар для історії метрик Starlink та журналу подій (reboot, оновлення)."""
import os
import sqlite3
import time
from contextlib import contextmanager

from app import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    online INTEGER NOT NULL,
    state TEXT,
    uptime_s INTEGER,
    downlink_mbps REAL,
    uplink_mbps REAL,
    ping_latency_ms REAL,
    ping_drop_ratio REAL,
    obstruction_fraction REAL,
    currently_obstructed INTEGER,
    software_version TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,       -- 'dish_reboot', 'system_update', 'system_reboot', 'watchdog_trigger'
    message TEXT,
    success INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    uptime_s INTEGER,
    cpu_percent REAL,
    mem_total_mb REAL,
    mem_used_mb REAL,
    mem_free_mb REAL,
    disk_total_gb REAL,
    disk_used_gb REAL,
    disk_free_gb REAL,
    temp_c REAL
);
CREATE INDEX IF NOT EXISTS idx_system_metrics_ts ON system_metrics(ts);
"""


def _ensure_dir():
    d = os.path.dirname(config.DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)


@contextmanager
def get_conn():
    _ensure_dir()
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


def insert_metric(status_dict: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO metrics
               (ts, online, state, uptime_s, downlink_mbps, uplink_mbps,
                ping_latency_ms, ping_drop_ratio, obstruction_fraction,
                currently_obstructed, software_version, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                status_dict["timestamp"],
                int(status_dict["online"]),
                status_dict.get("state", ""),
                status_dict.get("uptime_s", 0),
                status_dict.get("downlink_mbps", 0),
                status_dict.get("uplink_mbps", 0),
                status_dict.get("ping_latency_ms", 0),
                status_dict.get("ping_drop_ratio", 0),
                status_dict.get("obstruction_fraction", 0),
                int(status_dict.get("currently_obstructed", False)),
                status_dict.get("software_version", ""),
                status_dict.get("error", ""),
            ),
        )


def insert_event(kind: str, message: str, success: bool = True):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO events (ts, kind, message, success) VALUES (?,?,?,?)",
            (time.time(), kind, message, int(success)),
        )


def insert_system_metric(m: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO system_metrics
               (ts, uptime_s, cpu_percent, mem_total_mb, mem_used_mb, mem_free_mb,
                disk_total_gb, disk_used_gb, disk_free_gb, temp_c)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                m["timestamp"],
                m.get("uptime_s", 0),
                m.get("cpu_percent", 0),
                m.get("mem_total_mb", 0),
                m.get("mem_used_mb", 0),
                m.get("mem_free_mb", 0),
                m.get("disk_total_gb", 0),
                m.get("disk_used_gb", 0),
                m.get("disk_free_gb", 0),
                m.get("temp_c"),
            ),
        )


def get_recent_system_metrics(limit: int = 500):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM system_metrics ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_latest_system_metric():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM system_metrics ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def get_recent_metrics(limit: int = 500):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM metrics ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_recent_events(limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_metric():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM metrics ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def prune_old(days: int = None):
    days = days or config.HISTORY_RETENTION_DAYS
    cutoff = time.time() - days * 86400
    with get_conn() as conn:
        conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM system_metrics WHERE ts < ?", (cutoff,))


def clear_events():
    with get_conn() as conn:
        conn.execute("DELETE FROM events")


def uptime_stats_24h():
    """Частка часу online за останні 24 години (для дашборду)."""
    cutoff = time.time() - 86400
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as total, SUM(online) as up FROM metrics WHERE ts > ?",
            (cutoff,),
        ).fetchone()
        if not row or not row["total"]:
            return None
        return round(100.0 * (row["up"] or 0) / row["total"], 2)
