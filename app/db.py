"""SQLite шар для історії метрик Starlink та журналу подій (reboot, оновлення)."""
import json
import math
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from app import config

# Одинокі сурогати (\ud800-\udfff) не кодуються в UTF-8: sqlite3 кидає UnicodeEncodeError, а JSON з ensure_ascii=False
# не віддається клієнту (500). Приходять із зовнішнього JSON (`\ud800`) і з тіл запитів.
_SURROGATES = re.compile("[\ud800-\udfff]")
EVENT_MESSAGE_MAX_CHARS = 2000      # без межі подія на 5 МБ роздувала БД і відповідь /api/events (10 МБ щоразу)
EVENT_KIND_MAX_CHARS = 64


def _clean_text(value: str, limit: int = 0) -> str:
    """Безпечний для БД/JSON текст: сурогати -> U+FFFD, довжина обмежена (з \"…\")."""
    value = _SURROGATES.sub("\ufffd", value)
    if limit and len(value) > limit:
        value = value[:limit - 1] + "…"
    return value


def _clean_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: (_clean_text(v) if isinstance(v, str) else v) for k, v in row.items()}

# Формат backup-файлу (ручний через веб-кнопку і автоматичний
# періодичний, обидва в services.build_backup_dict()) - тут, не в
# webapp.py, щоб бути доступною з monitor.py без циклічного імпорту
# (webapp.py вже імпортує monitor, тому monitor не може імпортувати
# щось із webapp.py).
BACKUP_FORMAT_VERSION = 3

# Опційний callback для LED активності SD-картки (app/activity_led.py),
# викликається get_conn() після кожного успішного commit. None за
# замовчуванням - жодних накладних витрат, якщо LED вимкнено чи не
# зареєстрований (webapp.py-процес свідомо цього не робить, лише
# watchdog-процес monitor.py, де відбувається основний обсяг записів).
_activity_callback: Optional[Callable[[], None]] = None


def set_activity_callback(callback: Optional[Callable[[], None]]) -> None:
    global _activity_callback
    _activity_callback = callback

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
"""


def _ensure_dir() -> None:
    d = os.path.dirname(config.DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)


@contextmanager
def get_conn(durable: bool = False) -> Iterator[sqlite3.Connection]:
    """durable=True - commit синхронізується з диском одразу (synchronous=
    FULL). Для рідкісних записів, які не можна втратити при раптовому
    вимкненні живлення: налаштування (/settings) та історія пристроїв.
    Решта (метрики) - NORMAL: див. коментар нижче і open_anchor()."""
    _ensure_dir()
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL: monitor.service (пише кожні ~10с) і webui.service (читає щосекунди)
    # - окремі процеси, що звертаються до одного файлу одночасно. У режимі
    # за замовчуванням (rollback journal) запис блокує читання на час
    # транзакції; WAL дозволяє паралельне читання під час запису.
    conn.execute("PRAGMA journal_mode=WAL")
    # NORMAL (не дефолтний FULL) - офіційно рекомендований режим для
    # WAL (sqlite.org/pragma.html#pragma_synchronous): fsync лише при
    # checkpoint, не на кожному commit - значно менше фізичних записів
    # на SD-картку. БД лишається захищеною від пошкодження (WAL це
    # гарантує); ризик - втрата лише кількох останніх транзакцій при
    # раптовому вимкненні живлення, не критично для метрик моніторингу.
    conn.execute("PRAGMA synchronous=FULL" if durable else "PRAGMA synchronous=NORMAL")
    try:
        yield conn
        conn.commit()
        # Лише ПІСЛЯ успішного commit (не при винятку/rollback) -
        # опційний хук для LED активності SD-картки (app/activity_led.py).
        # Реєструється watchdog-процесом через set_activity_callback();
        # без реєстрації (LED вимкнено чи це webapp.py-процес, який не
        # ініціалізує LED) - _activity_callback лишається None, немає
        # накладних витрат.
        if _activity_callback is not None:
            try:
                _activity_callback()
            except Exception:
                pass
    finally:
        conn.close()



# "Якір": одне незадіяне з'єднання на весь час роботи монітора. Без нього WAL не працював: закриття
# ОСТАННЬОГО з'єднання змушує SQLite зробити повний checkpoint і видалити -wal, тож кожен запис проходив
# цикл WAL -> fsync -> копія в БД -> fsync -> видалення WAL. Вимір (симульована доба): fdatasync ~21600
# -> ~144 (-99%), видалень WAL ~10800 -> ~48, записів ~94 -> ~48 МБ, час x4.7. Компроміс: з
# synchronous=NORMAL записи потрапляють на SD за ~30 с — при раптовому вимкненні можна втратити останні
# ~30 с метрик, БД не пошкоджується. Налаштування й історія пристроїв — get_conn(durable=True).
_anchor: Optional[sqlite3.Connection] = None


def open_anchor() -> None:
    global _anchor
    if _anchor is not None:
        return
    _ensure_dir()
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    # Файл БД SQLite відкриває ліниво - без запиту незадіяне з'єднання не
    # приєднується до WAL і нічого не утримує (перевірено виміром).
    conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchall()
    _anchor = conn


def close_anchor() -> None:
    """Закриття - штатне завершення: останнє з'єднання робить checkpoint,
    WAL записується в основний файл і видаляється."""
    global _anchor
    conn, _anchor = _anchor, None
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass

def init_db() -> None:
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


def _migrate_table_columns(conn: sqlite3.Connection, table: str, new_columns: dict[str, str]) -> None:
    """Додає нові колонки в уже існуючу таблицю (для баз, створених до
    появи цих полів). CREATE TABLE IF NOT EXISTS не чіпає існуючу
    таблицю, тому колонки додаємо окремо через ALTER TABLE."""
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    for col, col_type in new_columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")


def _finite(value: Any) -> Any:
    """float inf/-inf -> None (NULL у БД). Нескінченні значення в метриках
    потім потрапляли б у відповіді API як `Infinity` - це НЕвалідний JSON,
    і `JSON.parse` у браузері падає (дашборд переставав оновлювати статус).
    NaN SQLite і так зберігає як NULL; тут те саме правило для inf."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _metric_row_params(status_dict: dict[str, Any]) -> tuple[Any, ...]:
    """Формує tuple параметрів для INSERT у metrics - спільний helper
    для insert_metric() (один рядок) і insert_metrics_batch() (кілька
    рядків через executemany), щоб не дублювати той самий список
    status_dict.get(...) двічі."""
    return (
        status_dict["timestamp"],
        int(status_dict["online"]),
        status_dict.get("state", ""),
        status_dict.get("uptime_s", 0),
        _finite(status_dict.get("downlink_mbps", 0)),
        _finite(status_dict.get("uplink_mbps", 0)),
        _finite(status_dict.get("ping_latency_ms", 0)),
        _finite(status_dict.get("ping_drop_ratio", 0)),
        _finite(status_dict.get("obstruction_fraction", 0)),
        status_dict.get("software_version", ""),
        status_dict.get("hardware_version", ""),
        status_dict.get("dish_id", ""),
        status_dict.get("error", ""),
        status_dict.get("update_state", ""),
        _finite(status_dict.get("update_progress_pct", 0)),
        int(status_dict.get("update_requires_reboot", False)),
        int(status_dict.get("update_install_pending", False)),
        status_dict.get("active_alerts", "[]"),
    )


# currently_obstructed НАВМИСНО не заповнюється (лишається NULL для
# нових рядків) - аудит показав, що ця колонка ніколи не читається
# ніде (дашборд показує лише obstruction_fraction, реальний відсоток
# обструкції); write-only колонка без користі. Розбір цього поля з
# відповіді Starlink у starlink_client теж прибрано. Схема лишається як є
# (без DROP COLUMN) - менший ризик для вже існуючих БД на реальних
# пристроях, ніж зміна схеми.
_INSERT_METRIC_SQL = """INSERT INTO metrics
   (ts, online, state, uptime_s, downlink_mbps, uplink_mbps,
    ping_latency_ms, ping_drop_ratio, obstruction_fraction,
    software_version, hardware_version, dish_id, error,
    update_state, update_progress_pct, update_requires_reboot,
    update_install_pending, active_alerts)
   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"""


def insert_metric(status_dict: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(_INSERT_METRIC_SQL, _metric_row_params(status_dict))


def insert_metrics_batch(status_dicts: list[dict[str, Any]]) -> None:
    """Записує КІЛЬКА dish-зчитувань ОДНІЄЮ транзакцією (SD-card-wear
    reduction - замість окремого write-транзакції на кожні 10с,
    накопичені в пам'яті зчитування пишуться разом раз на
    DISH_METRICS_BATCH_INTERVAL_SEC). Порожній список - тихо нічого
    не робить (не відкриває з'єднання даремно)."""
    if not status_dicts:
        return
    with get_conn() as conn:
        conn.executemany(_INSERT_METRIC_SQL, [_metric_row_params(d) for d in status_dicts])


def _json_field(raw: Any) -> list[Any]:
    """Парсить JSON-серіалізоване поле (active_alerts, clients) назад у
    список, з безпечним fallback на порожній список при відсутності чи
    пошкодженні даних. Спільний хелпер для трьох місць, де раніше було
    дубльовано той самий try/except json.loads блок."""
    if not raw:
        return []
    try:
        parsed: list[Any] = json.loads(raw)
        return parsed
    except (TypeError, json.JSONDecodeError):
        return []


def insert_event(kind: str, message: str, success: bool = True) -> None:
    """Записує подію в журнал. Якщо остання подія має той самий
    kind і message (типово - серія однакових попереджень підряд),
    замість нового рядка інкрементує count і оновлює ts існуючого
    запису - журнал не засмічується повторами. last_ts НАВМИСНО не
    оновлюється (лишається NULL) - аудит показав, що ця колонка
    ніколи не читалась ніде, і навіть якби читалась, дублювала б ts
    (обидві завжди отримували те саме значення now в одному UPDATE)."""
    kind = _clean_text(kind, EVENT_KIND_MAX_CHARS)
    message = _clean_text(message, EVENT_MESSAGE_MAX_CHARS)
    now = time.time()
    with get_conn() as conn:
        last = conn.execute(
            "SELECT id, kind, message FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if last is not None and last["kind"] == kind and last["message"] == message:
            conn.execute(
                "UPDATE events SET ts = ?, count = count + 1, success = ? WHERE id = ?",
                (now, int(success), last["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO events (ts, kind, message, success, count) VALUES (?,?,?,?,1)",
                (now, kind, message, int(success)),
            )


def insert_system_metric(m: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO system_metrics
               (ts, uptime_s, cpu_percent, mem_total_mb, mem_used_mb, mem_free_mb,
                disk_total_gb, disk_used_gb, disk_free_gb, temp_c)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                m["timestamp"],
                m.get("uptime_s", 0),
                _finite(m.get("cpu_percent", 0)),
                _finite(m.get("mem_total_mb", 0)),
                _finite(m.get("mem_used_mb", 0)),
                _finite(m.get("mem_free_mb", 0)),
                _finite(m.get("disk_total_gb", 0)),
                _finite(m.get("disk_used_gb", 0)),
                _finite(m.get("disk_free_gb", 0)),
                _finite(m.get("temp_c")),
            ),
        )


def set_router_status(r: dict[str, Any]) -> None:
    """Записує останній відомий стан роутера: таблиця містить рівно один рядок (id=1), історія не потрібна.
    bootcount НАВМИСНО не заповнюється (NULL): аудит показав, що колонку ніхто не читає; розбір поля в
    starlink_client теж прибрано. Схема без DROP COLUMN — менший ризик для наявних БД на пристроях, ніж
    її зміна.
    """
    r = _clean_row(r)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO router_status
               (id, ts, online, software_version, hardware_version, error,
                update_state, update_progress_pct, update_install_pending, active_alerts, clients)
               VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 ts=excluded.ts, online=excluded.online,
                 software_version=excluded.software_version,
                 hardware_version=excluded.hardware_version,
                 error=excluded.error,
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
                r.get("error", ""),
                r.get("update_state", ""),
                _finite(r.get("update_progress_pct", 0)),
                int(r.get("update_install_pending", False)),
                r.get("active_alerts", "[]"),
                r.get("clients", "[]"),
            ),
        )


def get_router_status() -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM router_status WHERE id = 1").fetchone()
        if not row:
            return None
        d = dict(row)
        d["active_alerts"] = _json_field(d.get("active_alerts"))
        d["clients"] = _json_field(d.get("clients"))
        return d


# "Останній" рядок вибирається за ts (настінний час): рядок із МАЙБУТНЬОГО (годинник стрибнув назад:
# ручна зміна, RTC, збій NTP) лишався б "останнім", і дашборд показував би застарілий стан, поки
# годинник не наздожене (вимір: тарілка offline, а дашборд online 2 год). Рядки з ts пізніше "зараз +
# допуск" ігноруються.
_FUTURE_TOLERANCE_SEC = 600


def get_latest_system_metric() -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM system_metrics WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
                           (time.time() + _FUTURE_TOLERANCE_SEC,)).fetchone()
        return dict(row) if row else None


def _parse_metric_row(row: dict[str, Any]) -> dict[str, Any]:
    """Розпарсити JSON-серіалізований active_alerts назад у список для API."""
    row["active_alerts"] = _json_field(row.get("active_alerts"))
    return row


def get_recent_events(limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_latest_metric() -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM metrics WHERE ts <= ? ORDER BY ts DESC LIMIT 1",
                           (time.time() + _FUTURE_TOLERANCE_SEC,)).fetchone()
        return _parse_metric_row(dict(row)) if row else None


def prune_old(days: Optional[int] = None) -> None:
    # Явна перевірка на None, не `days or config.X` - 0 є легітимним
    # (хоч і нетиповим) значенням days, яке `or`-патерн мовчки
    # ігнорував би (0 falsy в Python), підміняючи дефолтом.
    if days is None:
        days = config.HISTORY_RETENTION_DAYS
    cutoff = time.time() - days * 86400
    with get_conn() as conn:
        conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM events WHERE ts < ?", (cutoff,))
        conn.execute("DELETE FROM system_metrics WHERE ts < ?", (cutoff,))


# VACUUM переписує ВСЮ БД: вимір (БД 6.5 МБ) — 13.7 МБ записів на SD, а виконувався щодоби І на кожен
# запуск сервісу. Користі майже немає: після щоденного prune звільняється ~1/HISTORY_DAYS сторінок, але
# нові записи їх одразу заповнюють. Тож VACUUM лише коли вільних сторінок реально багато (напр. після
# зменшення HISTORY_DAYS чи великого видалення).
_VACUUM_MIN_FREE_RATIO = 0.30


def vacuum_and_analyze() -> bool:
    """Періодична оптимізація БД: ANALYZE (статистика планувальника,
    дешево) завжди; VACUUM - лише коли вільних сторінок >=
    _VACUUM_MIN_FREE_RATIO (див. коментар вище). Повертає, чи виконано
    VACUUM. Окреме з'єднання (не get_conn(), який лишає WAL-режим):
    надійніше й простіше з чистим autocommit-з'єднанням."""
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    try:
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        vacuumed = bool(pages > 0 and free / pages >= _VACUUM_MIN_FREE_RATIO)
        if vacuumed:
            conn.execute("VACUUM")
        conn.execute("ANALYZE")
        return vacuumed
    finally:
        conn.close()


# Код причини в повідомленні check_integrity() для файлу, що взагалі не є
# SQLite-базою. Шар даних НЕ перекладає тексти (раніше тут був лінивий імпорт
# i18n - цикл db <-> i18n): повертає код + технічну деталь, а переклад робить
# споживач (services.describe_integrity_problem). Формат: "<код>: <деталь>".
NOT_A_DATABASE = "not_a_database"


def check_integrity() -> tuple[bool, str]:
    """PRAGMA quick_check — швидша за integrity_check (не перевіряє UNIQUE, для нас достатньо); виявляє
    мовчазну деградацію БД (напр. після раптового вимкнення під час WAL-checkpoint) до того, як вона
    стане критичною. Повертає (ok, message): "ok" — коли quick_check повернув рядок "ok"
    (SQLite-конвенція), інакше — список проблем.
    """
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    try:
        rows = conn.execute("PRAGMA quick_check").fetchall()
    except sqlite3.DatabaseError as e:
        # Файл взагалі не SQLite-база (а не просто пошкоджені дані): PRAGMA quick_check тоді сам кидає
        # виняток. Без цього DatabaseError вийшов би назовні замість (False, message), якого чекає
        # викликач (знайдено живим тестом).
        return False, f"{NOT_A_DATABASE}: {e}"
    finally:
        conn.close()
    messages = [str(r[0]) for r in rows]
    if messages == ["ok"]:
        return True, "ok"
    return False, "; ".join(messages)


def _upsert_known_device(dish_id: str, component: str, hardware_version: str, software_version: str) -> tuple[bool, Optional[str]]:
    """Спільна логіка upsert_known_device_dish()/_router(): відрізняються лише column-префіксом
    (dish_/router_). SQLite не параметризує НАЗВИ колонок через `?`, тому вони підставляються f-рядком —
    `component` МАЄ бути внутрішньою константою ("dish"/"router"), не user input; явна перевірка нижче —
    останній захист.
    """
    # if/raise, не assert: `python -O` прибирає assert повністю, а від цієї
    # перевірки залежить безпека f-рядка з назвами колонок у SQL нижче.
    if component not in ("dish", "router"):
        raise ValueError(f"невідомий компонент: {component!r}")
    if not dish_id:
        return False, None
    dish_id, hardware_version, software_version = _clean_text(dish_id), _clean_text(hardware_version), _clean_text(software_version)
    now = time.time()
    hw_col = f"{component}_hardware_version"
    sw_col = f"{component}_software_version"
    ts_col = f"{component}_software_updated_ts"
    with get_conn(durable=True) as conn:
        # BEGIN IMMEDIATE ДО читання: sqlite3 починає транзакцію лише перед записом, тож два одночасні
        # виклики (монітор і веб-кнопка, два потоки бота) обидва прочитали б СТАРУ версію й обидва
        # вважали б це зміною: дубльовані сповіщення про одне оновлення (виміряно: 8 потоків → 8
        # сповіщень).
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            f"SELECT {sw_col} FROM known_devices WHERE dish_id = ?", (dish_id,)  # noqa: S608 - колонки лише з "dish"/"router" (перевірка вище)
        ).fetchone()
        old_version = existing[sw_col] if existing else None
        version_changed = existing is None or old_version != software_version
        real_change = old_version is not None and old_version != software_version

        conn.execute(
            f"""INSERT INTO known_devices
               (dish_id, first_seen_ts, last_seen_ts, {hw_col}, {sw_col}, {ts_col})
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(dish_id) DO UPDATE SET
                 last_seen_ts = excluded.last_seen_ts,
                 {hw_col} = excluded.{hw_col},
                 {sw_col} = excluded.{sw_col},
                 {ts_col} = CASE WHEN ? THEN excluded.{ts_col}
                                  ELSE known_devices.{ts_col} END""",  # noqa: S608 - колонки лише з "dish"/"router" (перевірка вище)
            (dish_id, now, now, hardware_version, software_version, now if version_changed else None,
             int(version_changed)),
        )
    return real_change, old_version


def upsert_known_device_dish(dish_id: str, hardware_version: str, software_version: str) -> tuple[bool, Optional[str]]:
    """Записує/оновлює відому інформацію про dish для dish_id. dish_software_updated_ts змінюється лише
    коли software_version реально змінилась (не при кожному опитуванні) — /id у Telegram показує час
    останнього встановленого оновлення. Повертає (real_change, old_version): real_change=True лише коли
    dish_id уже був відомий і версія відрізняється (перший запис нового dish_id — не "зміна").
    """
    return _upsert_known_device(dish_id, "dish", hardware_version, software_version)


def upsert_known_device_router(dish_id: str, hardware_version: str, software_version: str) -> tuple[bool, Optional[str]]:
    """Те саме, що upsert_known_device_dish, для роутерної частини Mini з тим самим dish_id. Dish і router
    опитуються в різних циклах: якщо запису для dish_id ще немає (router опитався раніше), рядок
    створюється з порожніми dish-полями. Повертає (real_change, old_version).
    """
    return _upsert_known_device(dish_id, "router", hardware_version, software_version)


def get_known_device(dish_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM known_devices WHERE dish_id = ?", (dish_id,)).fetchone()
        return dict(row) if row else None


def get_all_known_devices() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM known_devices ORDER BY last_seen_ts DESC").fetchall()
        return [dict(r) for r in rows]


def merge_known_devices(devices: list[dict[str, Any]]) -> int:
    """Відновлення known_devices з backup НЕ перезаписує dish_id, що вже є в цільовій БД (INSERT OR
    IGNORE), навіть якщо дані в backup виглядають новішими: локальна БД працюючого watchdog
    авторитетніша за статичний знімок з невідомого моменту (як і для target-версій — не перезаписувати
    потенційно свіжіший стан старішим). Повертає кількість РЕАЛЬНО доданих dish_id.
    """
    added = 0
    with get_conn(durable=True) as conn:
        for d in devices:
            if "dish_id" not in d:
                continue
            cursor = conn.execute(
                """INSERT OR IGNORE INTO known_devices
                   (dish_id, first_seen_ts, last_seen_ts, dish_hardware_version,
                    dish_software_version, dish_software_updated_ts,
                    router_hardware_version, router_software_version,
                    router_software_updated_ts)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    d["dish_id"], d.get("first_seen_ts"), d.get("last_seen_ts"),
                    d.get("dish_hardware_version"), d.get("dish_software_version"),
                    d.get("dish_software_updated_ts"), d.get("router_hardware_version"),
                    d.get("router_software_version"), d.get("router_software_updated_ts"),
                ),
            )
            if cursor.rowcount > 0:
                added += 1
    return added


def parse_version_list(raw: Optional[str]) -> list[str]:
    """Розбирає comma-separated список версій (формат як telegram_chat_ids): SpaceX інколи видає РІЗНІ
    номери для різних апаратних ревізій. Спільний helper для monitor.py (досягнення) і webapp.py
    (валідація "лише новіші" для КОЖНОГО кандидата).
    """
    if not raw:
        return []
    return [v.strip() for v in raw.split(",") if v.strip()]


def version_channel(v: str) -> Optional[str]:
    """Витягує 'канал' білда з версії прошивки - буквений префікс
    перед цифрами в build-сегменті (напр. 'mr' з 'mr82648', 'cr' з
    'cr81950'). Різні апаратні ревізії Starlink можуть отримувати
    оновлення з РІЗНИХ, незалежних build-каналів - порівнювати дату
    напряму МІЖ каналами не має сенсу (знайдено на реальному запиті
    користувача: '2026.07.06.cr81950...' хибно відхилявся як
    "старіший" за '2026.07.19.mr82648' - обидва можуть бути
    легітимними, актуальними версіями для СВОЇХ окремих апаратних
    ревізій одночасно, просто з різних build-каналів). None якщо
    жоден сегмент не відповідає паттерну (нетиповий формат версії)."""
    for segment in v.split("."):
        m = re.match(r"^([a-zA-Z]+)\d+", segment)
        if m:
            return m.group(1).lower()
    return None


def version_key(v: str) -> tuple[tuple[int, int, int], tuple[Any, ...]]:
    """Толерантний ключ порівняння версій прошивки Starlink (зазвичай YYYY.MM.DD.mrXXXXX.N, не строгий
    semver). Префікс YYYY.MM.DD домінує; якщо дата однакова чи відсутня — посегментне порівняння
    ('.'-частини: числові як int, нечислові як рядок, щоб не впасти на нетиповому форматі). Публічний:
    потрібен і валідації target-версій (webapp.py), і розрізненню "🔄 оновлено"/"⏪ відкочено"
    (monitor.py).
    """
    m = re.match(r"^([0-9]{4})\.([0-9]{2})\.([0-9]{2})", v)
    date_part = (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else (0, 0, 0)
    # isdigit() істинне й для "²", "①" (а int() їх не розбирає - ValueError, 500 на /api/target-versions) і для
    # арабсько-індійських цифр: числами вважаємо лише ASCII-цифри.
    segments = tuple((0, int(seg)) if seg.isascii() and seg.isdigit() else (1, seg) for seg in v.split("."))
    return date_part, segments


def is_older_version(candidate: str, baseline: str) -> bool:
    return version_key(candidate) < version_key(baseline)


def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def claim_setting(key: str, value: str) -> bool:
    """Атомарно: якщо settings[key] != value — записує value і повертає True ("захопив"); якщо вже дорівнює
    — False. Одна транзакція (BEGIN IMMEDIATE): прапорці "про це вже сповіщено" не можна робити як get
    -> сповістити -> set — два одночасні виклики обидва бачили б "ще ні" (8 потоків → 8 сповіщень).
    """
    key, value = _clean_text(key), _clean_text(value)
    with get_conn(durable=True) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is not None and row["value"] == value:
            return False
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
            (key, value),
        )
        return True


def set_setting(key: str, value: str) -> None:
    key, value = _clean_text(key), _clean_text(value)
    with get_conn(durable=True) as conn:
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


def set_auto_reboot_enabled(enabled: bool) -> None:
    set_setting("auto_reboot_enabled", "1" if enabled else "0")
