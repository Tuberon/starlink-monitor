# Database schema

SQLite, single file at `STARLINK_DB_PATH` (default `/var/lib/starlink-monitor/history.db`). Schema and access layer live in [app/db.py](../app/db.py).

New columns on existing tables must go through `_migrate_table_columns()` (runs `ALTER TABLE`) in addition to `SCHEMA` — `CREATE TABLE IF NOT EXISTS` does not add columns to a database that already exists on a deployed Pi.

## Tables

**`metrics`** — one row per dish poll (~every 10s). Throughput, ping latency/loss, obstruction, firmware version, and dish update-state/progress/alerts (`active_alerts` stored as JSON array).

**`events`** — append-only log: `kind` (`dish_reboot`, `watchdog_trigger`, `update_state_change`, `dish_alert`/`dish_alert_resolved`, `router_update_state_change`, `router_alert`/`router_alert_resolved`, `obstruction_warning`, `manual_update_check`, `auto_reboot_toggled`, `telegram_config_updated`, `system_update`, `system_reboot`, ...), `message`, `success`.

**`system_metrics`** — Pi's own health per poll cycle: CPU, memory, disk, SoC temperature, uptime.

**`router_status`** — single row (`id = 1`, upserted), not a history table — router firmware changes rarely, and only current state is shown on the dashboard.

**`settings`** — generic key/value store for runtime-toggleable config that shouldn't require a service restart (`auto_reboot_enabled`, Telegram bot token/chat IDs/enabled flag).

## Retention

`prune_old()` deletes `metrics`/`events`/`system_metrics` rows older than `STARLINK_HISTORY_DAYS` (default 30), run hourly from the monitor loop. `router_status` and `settings` are not pruned (no history kept).
