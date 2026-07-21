"""SQLite шар для історії метрик Starlink та журналу подій (reboot, оновлення)."""
import json
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
    hardware_version TEXT,
    dish_id TEXT,
    error TEXT,
    update_state TEXT,
    update_progress_pct REAL,
    update_requires_reboot INTEGER,
    update_install_pending INTEGER,
    active_alerts TEXT
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,       -- 'dish_reboot', 'watchdog_trigger', 'update_state_change', ...
    message TEXT,
    success INTEGER,
    count INTEGER NOT NULL DEFAULT 1,
    last_ts REAL
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

CREATE TABLE IF NOT EXISTS router_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ts REAL NOT NULL,
    online INTEGER NOT NULL,
    software_version TEXT,
    hardware_version TEXT,
    bootcount INTEGER,
    error TEXT,
    update_state TEXT,
    update_progress_pct REAL,
    update_install_pending INTEGER,
    active_alerts TEXT,
    clients TEXT
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS known_devices (
    dish_id TEXT PRIMARY KEY,
    first_seen_ts REAL NOT NULL,
    last_seen_ts REAL NOT NULL,
    dish_hardware_version TEXT,
    dish_software_version TEXT,
    dish_software_updated_ts REAL,
    router_hardware_version TEXT,
    router_software_version TEXT,
    router_software_updated_ts REAL
);

CREATE TABLE IF NOT EXISTS speedtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    download_mbps REAL,
    upload_mbps REAL,
    ping_ms REAL,
    server_name TEXT,
    success INTEGER NOT NULL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_speedtest_results_ts ON speedtest_results(ts);
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
    # WAL: monitor.service (пише кожні ~10с) і webui.service (читає щосекунди)
    # - окремі процеси, що звертаються до одного файлу одночасно. У режимі
    # за замовчуванням (rollback journal) запис блокує читання на час
    # транзакції; WAL дозволяє паралельне читання під час запису.
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate_table_columns(conn, "metrics", {
            "update_state": "TEXT",
            "update_progress_pct": "REAL",
            "update_requires_reboot": "INTEGER",
            "update_install_pending": "INTEGER",
            "active_alerts": "TEXT",
            "hardware_version": "TEXT",
            "dish_id": "TEXT",
        })
        _migrate_table_columns(conn, "router_status", {
            "update_state": "TEXT",
            "update_progress_pct": "REAL",
            "update_install_pending": "INTEGER",
            "active_alerts": "TEXT",
            "clients": "TEXT",
        })
        _migrate_table_columns(conn, "events", {
            "count": "INTEGER NOT NULL DEFAULT 1",
            "last_ts": "REAL",
        })


def _migrate_table_columns(conn, table: str, new_columns: dict):
    """Додає нові колонки в уже існуючу таблицю (для баз, створених до
    появи цих полів). CREATE TABLE IF NOT EXISTS не чіпає існуючу
    таблицю, тому колонки додаємо окремо через ALTER TABLE."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col, col_type in new_columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")


def insert_metric(status_dict: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO metrics
               (ts, online, state, uptime_s, downlink_mbps, uplink_mbps,
                ping_latency_ms, ping_drop_ratio, obstruction_fraction,
                currently_obstructed, software_version, hardware_version, dish_id, error,
                update_state, update_progress_pct, update_requires_reboot,
                update_install_pending, active_alerts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                status_dict.get("hardware_version", ""),
                status_dict.get("dish_id", ""),
                status_dict.get("error", ""),
                status_dict.get("update_state", ""),
                status_dict.get("update_progress_pct", 0),
                int(status_dict.get("update_requires_reboot", False)),
                int(status_dict.get("update_install_pending", False)),
                status_dict.get("active_alerts", "[]"),
            ),
        )


def _json_field(raw) -> list:
    """Парсить JSON-серіалізоване поле (active_alerts, clients) назад у
    список, з безпечним fallback на порожній список при відсутності чи
    пошкодженні даних. Спільний хелпер для трьох місць, де раніше було
    дубльовано той самий try/except json.loads блок."""
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []


def insert_event(kind: str, message: str, success: bool = True):
    """Записує подію в журнал. Якщо остання подія має той самий
    kind і message (типово - серія однакових попереджень підряд),
    замість нового рядка інкрементує count і оновлює last_ts/ts
    існуючого запису - журнал не засмічується повторами."""
    now = time.time()
    with get_conn() as conn:
        last = conn.execute(
            "SELECT id, kind, message FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last is not None and last["kind"] == kind and last["message"] == message:
            conn.execute(
                "UPDATE events SET ts = ?, last_ts = ?, count = count + 1, success = ? WHERE id = ?",
                (now, now, int(success), last["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO events (ts, kind, message, success, count) VALUES (?,?,?,?,1)",
                (now, kind, message, int(success)),
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


def set_router_status(r: dict):
    """Записує останній відомий стан роутера. Таблиця завжди містить
    рівно один рядок (id=1) - історія не потрібна, лише поточний стан."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO router_status
               (id, ts, online, software_version, hardware_version, bootcount, error,
                update_state, update_progress_pct, update_install_pending, active_alerts, clients)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 ts=excluded.ts, online=excluded.online,
                 software_version=excluded.software_version,
                 hardware_version=excluded.hardware_version,
                 bootcount=excluded.bootcount, error=excluded.error,
                 update_state=excluded.update_state,
                 update_progress_pct=excluded.update_progress_pct,
                 update_install_pending=excluded.update_install_pending,
                 active_alerts=excluded.active_alerts,
                 clients=excluded.clients""",
            (
                r["timestamp"],
                int(r["online"]),
                r.get("software_version", ""),
                r.get("hardware_version", ""),
                r.get("bootcount", 0),
                r.get("error", ""),
                r.get("update_state", ""),
                r.get("update_progress_pct", 0),
                int(r.get("update_install_pending", False)),
                r.get("active_alerts", "[]"),
                r.get("clients", "[]"),
            ),
        )


def get_router_status():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM router_status WHERE id = 1").fetchone()
        if not row:
            return None
        d = dict(row)
        d["active_alerts"] = _json_field(d.get("active_alerts"))
        d["clients"] = _json_field(d.get("clients"))
        return d


def get_latest_system_metric():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM system_metrics ORDER BY ts DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def _parse_metric_row(row: dict) -> dict:
    """Розпарсити JSON-серіалізований active_alerts назад у список для API."""
    row["active_alerts"] = _json_field(row.get("active_alerts"))
    return row


def get_recent_metrics(limit: int = 500):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM metrics ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_parse_metric_row(dict(r)) for r in reversed(rows)]


def get_recent_events(limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_metric():
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM metrics ORDER BY ts DESC LIMIT 1").fetchone()
        return _parse_metric_row(dict(row)) if row else None


def prune_old(days: int = None):
    days = days or config.HISTORY_RETENTION_DAYS
    cutoff = time.time() - days * 86400
    with get_conn() as conn:
        conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM system_metrics WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM speedtest_results WHERE ts < ?", (cutoff,))


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


def upsert_known_device_dish(dish_id: str, hardware_version: str, software_version: str):
    """Записує/оновлює відому інформацію про dish для конкретного dish_id.
    dish_software_updated_ts оновлюється лише коли software_version реально
    змінилась відносно попереднього запису (не при кожному опитуванні) -
    так /id у Telegram-боті може показати, коли саме відбулось останнє
    встановлене оновлення, а не час останнього опитування."""
    if not dish_id:
        return
    now = time.time()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT dish_software_version FROM known_devices WHERE dish_id = ?", (dish_id,)
        ).fetchone()
        version_changed = existing is None or existing["dish_software_version"] != software_version

        conn.execute(
            """INSERT INTO known_devices
               (dish_id, first_seen_ts, last_seen_ts, dish_hardware_version,
                dish_software_version, dish_software_updated_ts)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(dish_id) DO UPDATE SET
                 last_seen_ts = excluded.last_seen_ts,
                 dish_hardware_version = excluded.dish_hardware_version,
                 dish_software_version = excluded.dish_software_version,
                 dish_software_updated_ts = CASE WHEN ? THEN excluded.dish_software_updated_ts
                                                  ELSE known_devices.dish_software_updated_ts END""",
            (dish_id, now, now, hardware_version, software_version, now if version_changed else None,
             int(version_changed)),
        )


def upsert_known_device_router(dish_id: str, hardware_version: str, software_version: str):
    """Аналогічно до upsert_known_device_dish, але для роутерної частини
    того самого фізичного Mini. Прив'язується до того ж dish_id - dish і
    router опитуються в різних циклах, тому оновлюються окремо; якщо
    запису для dish_id ще немає (router опитався раніше за dish), рядок
    створюється з порожніми dish-полями."""
    if not dish_id:
        return
    now = time.time()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT router_software_version FROM known_devices WHERE dish_id = ?", (dish_id,)
        ).fetchone()
        version_changed = existing is None or existing["router_software_version"] != software_version

        conn.execute(
            """INSERT INTO known_devices
               (dish_id, first_seen_ts, last_seen_ts, router_hardware_version,
                router_software_version, router_software_updated_ts)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(dish_id) DO UPDATE SET
                 last_seen_ts = excluded.last_seen_ts,
                 router_hardware_version = excluded.router_hardware_version,
                 router_software_version = excluded.router_software_version,
                 router_software_updated_ts = CASE WHEN ? THEN excluded.router_software_updated_ts
                                                    ELSE known_devices.router_software_updated_ts END""",
            (dish_id, now, now, hardware_version, software_version, now if version_changed else None,
             int(version_changed)),
        )


def get_known_device(dish_id: str):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM known_devices WHERE dish_id = ?", (dish_id,)).fetchone()
        return dict(row) if row else None


def get_all_known_devices():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM known_devices ORDER BY last_seen_ts DESC").fetchall()
        return [dict(r) for r in rows]


def insert_speedtest_result(data: dict):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO speedtest_results
               (ts, download_mbps, upload_mbps, ping_ms, server_name, success, error)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                data["ts"],
                data.get("download_mbps"),
                data.get("upload_mbps"),
                data.get("ping_ms"),
                data.get("server_name", ""),
                int(data.get("success", False)),
                data.get("error", ""),
            ),
        )


def get_recent_speedtest_results(limit: int = 50):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM speedtest_results ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_speedtest_result():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM speedtest_results WHERE success = 1 ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_setting(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value),
        )


def get_auto_reboot_enabled() -> bool:
    """Runtime-перемикач автоматичного reboot dish/router при готовому
    оновленні. Якщо ще не встановлювався через веб-інтерфейс - бере
    значення за замовчуванням з config.AUTO_REBOOT_ON_UPDATE_READY
    (змінна середовища STARLINK_AUTO_REBOOT_ON_UPDATE)."""
    val = get_setting("auto_reboot_enabled")
    if val is None:
        return config.AUTO_REBOOT_ON_UPDATE_READY
    return val == "1"


def set_auto_reboot_enabled(enabled: bool):
    set_setting("auto_reboot_enabled", "1" if enabled else "0")
