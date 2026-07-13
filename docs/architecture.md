# Architecture

## Two logical devices, one physical unit

Starlink Mini contains **two independent devices in one enclosure**, each with its own firmware version and update cycle:

- **dish** — `192.168.100.1:9200` (default `STARLINK_DISH_ADDR`)
- **router** — `192.168.1.1:9000` (default `STARLINK_ROUTER_ADDR`)

They must be polled, parsed, and tracked separately (separate protobuf/JSON schemas, separate update-state enums, separate alert flag sets). See [app/starlink_client.py](../app/starlink_client.py) for the exact field mappings, confirmed via `grpcurl describe` against a live device.

`reboot_dish()` is the **only** reboot path. Since dish and router are physically one device, rebooting the dish address reboots both. There is no `reboot_router()`.

## Polling loop (app/monitor.py)

`Watchdog.run_forever()`:
- polls dish every `STARLINK_POLL_INTERVAL` (default 10s)
- polls router every ~60s (separate, less frequent — firmware changes rarely, and polling adds WiFi load)
- collects local Pi system metrics each cycle (`app/system_metrics.py`)
- prunes DB rows older than `STARLINK_HISTORY_DAYS` once per hour

### Auto-reboot triggers

Three independent triggers, all guarded by the same `MIN_REBOOT_INTERVAL_SEC` (default 1800s) to prevent reboot loops:

1. **Watchdog**: dish fails to respond `STARLINK_MAX_FAILURES` polls in a row.
2. **Dish update ready**: `software_update_state == REBOOT_REQUIRED` or `alerts.install_pending`.
3. **Router update ready**: `WifiSoftwareUpdateState == REBOOT_PENDING` or `WifiAlerts.install_pending` (router has its own update cycle, independent of dish).

All three call `client.reboot_dish()`. Adding a new auto-reboot condition means adding a new `_maybe_reboot_for_*` method that checks `last_reboot_ts` against `MIN_REBOOT_INTERVAL_SEC` the same way — don't bypass the shared guard.

Auto-reboot-on-update can be toggled at runtime from the web UI (`db.get_auto_reboot_enabled()` / `set_auto_reboot_enabled()`, stored in the `settings` table, not env — takes effect without a service restart).

### Event log & Telegram

Every state transition (update state change, alert appeared/resolved, reboot attempt, dish back online) is written to the `events` table and optionally mirrored to Telegram (`app/telegram_notify.py`). Telegram sends are wrapped so failures only log a warning — they never interrupt the polling loop.

## Manual "check updates" limitation

The dish/router local gRPC API has **no command to force a cloud update check** against SpaceX's backend (confirmed by direct gRPC calls — `software_update` returns `FailedPrecondition` on dish, `Unimplemented` on router; that RPC is for manual sideload streaming, not a cloud check). The web UI's "Перевірити оновлення" button instead just polls both devices immediately instead of waiting for the next background cycle — see `api_check_updates` in [app/webapp.py](../app/webapp.py).

## Auto-updater (app/updater.py)

Runs periodically via `starlink-updater.timer` (not a continuous loop, to avoid holding the apt lock). Each run: `apt-get update/upgrade`, upgrade Python deps if `requirements.txt` changed, `git pull --ff-only` + restart `monitor`/`webui` services on change, and reboot the Pi itself only if `/var/run/reboot-required` exists **and** the current hour matches `STARLINK_UPDATE_REBOOT_WINDOW_HOUR` (a quiet window, so it doesn't interrupt active monitoring).
