# REST API

Flask app in [app/webapp.py](../app/webapp.py), served on `STARLINK_WEBUI_PORT` (default 8080). All endpoints return JSON.

| Method | Path | Purpose |
|---|---|---|
| GET | `/` | Dashboard HTML (`templates/index.html`) |
| GET | `/api/status` | Latest dish metric + 24h uptime % |
| GET | `/api/history?limit=` | Recent dish metrics (max 5000) |
| GET | `/api/events?limit=` | Recent event log entries (max 500) |
| DELETE | `/api/events` | Clear event log |
| GET | `/api/system-status` | Latest Pi system metric |
| GET | `/api/system-history?limit=` | Recent Pi system metrics (max 5000) |
| GET | `/api/router-status` | Latest router status (single row) |
| POST | `/api/reboot-dish` | Manual reboot (reboots both dish and router — see [architecture.md](architecture.md)) |
| POST | `/api/check-updates` | Force an immediate poll of dish + router (no cloud check exists locally) |
| GET | `/api/config` | Non-sensitive runtime config for display |
| POST | `/api/auto-reboot` | Toggle auto-reboot-on-update (`{"enabled": bool}`), persisted in `settings` table |
| GET | `/api/telegram-config` | Telegram config; bot token always returned masked |
| POST | `/api/telegram-config` | Update token / chat_ids (CSV string or list) / enabled |
| POST | `/api/telegram-test` | Send a test Telegram message |

Every state-changing endpoint (`reboot-dish`, `check-updates`, `auto-reboot`, `telegram-config`) also writes an `events` row for the dashboard's audit log.
