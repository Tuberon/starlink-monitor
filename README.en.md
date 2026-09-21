# 🛰️ Starlink Mini Monitor & Watchdog — Raspberry Pi Zero 2 W

*Читати українською: [README.md](README.md)*

Autonomous monitor and watchdog for Starlink Mini on Raspberry Pi Zero 2 W.

**Contents**: [What it does](#-what-it-does) · [Wiring diagram](#-wiring-diagram-optional-hardware) ·
[GPIO button](#-physical-shutdown-button-gpio) ·
[Activity LED](#-sd-card-activity-led-gpio) ·
[TFT display](#️-physical-tft-display-st7789-spi) ·
[Telegram](#-telegram-notifications-and-commands) · [Backup](#-settings-backuprestore) ·
[healthz/PWA](#-extra-healthz-pwa) · [Reliability](#️-reliability) ·
[Manual update check](#-about-manual-update-checks) · [Network](#-important-network-notes) ·
[Load reduction](#-reducing-system-load-optional-during-installation) ·
[Type checking](#-type-checking-mypy) · [Tests](#-tests) ·
[Structure](#️-project-structure) · [Installation](#-installation-and-updates) ·
[Configuration](#️-configuration) · [License](#-license)

## 📋 What it does

1. Connects to Starlink Mini's WiFi as a client.
2. Polls the dish (`192.168.100.1:9200`, every 10s) and router
   (`192.168.1.1:9000`, every 35s) — the Mini consists of TWO logical
   devices in one enclosure, each with its own firmware.
3. Auto-reboots the whole Mini on a dish watchdog timeout or a ready
   firmware update (dish/router), with shared reboot-loop protection.
4. Collects Pi metrics and Starlink history in SQLite with auto-cleanup.
5. Web dashboard (Flask): live metrics, firmware versions,
   WiFi clients, last 5 log events, manual reboot/update check.
   The `/stats` page — full event log.
6. Telegram: notifications + commands `/status /checkupdates /reboot /id /help`.
7. Pi reboot/shutdown from the web interface or a physical GPIO
   button, backup/restore of all settings in one file (optionally to
   Telegram too, automatically or manually), `/healthz` for external
   monitoring, installable as a PWA.
8. **Multilingual interface** (Ukrainian/English) — switch on the
   `/settings` page, applies to the web dashboard and Telegram bot
   together.

## 🔌 Wiring diagram (optional hardware)

```
                    ┌──────────────────┐
                    │ TFT display 1.9" │
                    │ ST7789, SPI      │
                    └─────────┬────────┘
                              │ SPI: CS=GPIO8, DC=GPIO25,
                              │ RST=GPIO24, BL=GPIO18
                  ┌───────────┴───────────┐
                  │ Raspberry Pi Zero 2 W │
                  └─┬───────────────────┬─┘
           GPIO27 + │                   │ USB OTG port
           GND      │                   │ (single, data)
           ┌────────┴────────┐  ┌───────┴───────┐
           │ Shutdown /      │  │ USB-Ethernet  │
           │ backlight button│  │ wired eth0    │
           └─────────────────┘  └───────────────┘

  (not shown: optional SD-card activity LED — GPIO17 + GND,
   details below)
```

The TFT display and button are optional (`STARLINK_DISPLAY_ENABLED=0`
by default). USB-Ethernet is optional too — a WAN-failover fallback,
if present (more in [Network](#-important-network-notes)).

## 🔘 Physical shutdown button (GPIO)

A normally-open button between a chosen GPIO pin (BCM) and GND,
internal pull-up configured by the service. Enabled by default on
`GPIO27` (`STARLINK_SHUTDOWN_BUTTON_PIN=27`); `0` disables it, to
change the pin (if needed) — in `/etc/starlink-monitor/env`:
```
STARLINK_SHUTDOWN_BUTTON_PIN=27
STARLINK_SHUTDOWN_BUTTON_HOLD_SEC=3
```
then `sudo systemctl restart starlink-shutdown-button.service`.
Holding longer than the configured time (3s by default) →
`systemctl poweroff`, with an event in the log and Telegram.

If the physical TFT display is enabled (`STARLINK_DISPLAY_ENABLED=1`)
— **the same button** gets a second action: a **short** press
toggles the display backlight, a **long** press (as before) shuts
down the Pi. Handled then inside `starlink-display.service`, while
`starlink-shutdown-button.service` disables itself (to avoid
competing for the same GPIO pin). On reboot/poweroff (from the
button or the web dashboard) — if the display is enabled, the screen
shows "Shutting down.../Rebooting..." for a few seconds before the
Pi actually starts shutting down (`STARLINK_DISPLAY_SHUTDOWN_
MESSAGE_DELAY_SEC`, 2s by default).

## 💡 SD-card activity LED (GPIO)

An additional LED that briefly blinks on every real SQLite write
(dish metrics, system metrics, event log, router status, automatic
backup, VACUUM) — a visual activity indicator, similar to the
built-in activity LED on desktop drives. Disabled by default
(`STARLINK_ACTIVITY_LED_PIN=0`, the same principle as the button and
display — optional peripheral). Wiring: LED (with a ~330Ω resistor)
between a chosen GPIO pin (BCM) and GND. Enable in
`/etc/starlink-monitor/env`:
```
STARLINK_ACTIVITY_LED_PIN=17
STARLINK_ACTIVITY_LED_BLINK_MS=50
```
then `sudo systemctl restart starlink-monitor.service`. Handled only
by the watchdog process (not the web dashboard) — avoids a conflict
between two processes over one exclusive GPIO request.

## 🖥️ Physical TFT display (ST7789, SPI)

Shows the live dish status (online/offline, uptime, firmware update,
firmware versions). Disabled by default (`STARLINK_DISPLAY_
ENABLED=0`). Library — Adafruit CircuitPython ST7789.

> ⚠️ SPI is usually disabled: `sudo raspi-config nonint do_spi 0 &&
> sudo reboot` (`install.sh` warns if it's not enabled yet).

Rated resolution — 170×320 (SKU MSP1901, portrait). Pins depend on
the wiring:
```
STARLINK_DISPLAY_ENABLED=1
STARLINK_DISPLAY_DC_PIN=25
STARLINK_DISPLAY_RST_PIN=24
STARLINK_DISPLAY_BL_PIN=18
STARLINK_DISPLAY_WIDTH=170
STARLINK_DISPLAY_HEIGHT=320
STARLINK_DISPLAY_ROTATION=0
STARLINK_DISPLAY_OFFSET_LEFT=35
```
then `sudo systemctl restart starlink-display.service`. `BLK`
wired directly to 3.3V — set `BL_PIN=0`. `ROTATION`: `0/90/180/270`.
`OFFSET_LEFT=35` — an empirical value for this model (without an
offset — color noise, adjust in ~10-15px steps). Don't swap WIDTH/HEIGHT.

Backlight control — the same shutdown button (short=toggle,
long=shut down Pi). Auto-off via `BACKLIGHT_AUTO_OFF_SEC` (60s). On
an update status change, the backlight flashes for `UPDATE_FLASH_SEC`
(5s) — a physical notification without a phone.

## 💬 Telegram notifications and commands

Configured on `/settings`:
1. Create a bot via [@BotFather](https://t.me/BotFather), get a bot token.
2. Find your chat_id via [@userinfobot](https://t.me/userinfobot).
3. Enter the token and chat_id (comma-separated — multiple recipients), "Save",
   "Send test", enable the toggle.

> ⚠️ The bot token is stored in local SQLite (`settings`), always masked in the web UI.

**Commands** (only for chat_ids on the list):
- `/status` — a quick look: online, firmware version, alerts
- `/checkupdates` — force-poll dish/router, check target
  versions — the same logic as the dashboard button
- `/reboot` — confirmation via inline buttons, 2 minutes
- `/id [ID or part]` — list of dishes, or details of a specific one
- `/help` — command list

Long polling (no webhook), in the `starlink-monitor.service` thread.

**Notification behavior:**
- `STARLINK_NOTIFY_PI_STARTUP=0` — disables notifications about a real Pi reboot
- Silence beyond `STARLINK_NOTIFICATIONS_MUTE_AFTER` (15 min) of
  unavailability — recovery is reported with the downtime duration
- `STARLINK_NOTIFY_DISH_RECOVERY=0` — disables "✅ Dish back online"
- Frequent reboots are grouped silently (`REBOOT_SPAM_THRESHOLD`/`_WINDOW_SEC`),
  a summary follows the lull
- `STARLINK_MAX_LOGGED_FAILURES` (15) — logging stops, reboot attempts continue
- Always muted: router check error, "update pending", roaming
- Firmware rollback (SpaceX occasionally rolls back builds) —
  intentionally not notified, `known_devices` is updated silently
- **Expected versions** (`/settings`) — comma-separated, only newer ones are accepted

## 💾 Settings backup/restore

On `/settings`: "Download backup" — a JSON with bot token, chat_id,
auto-reboot, expected firmware versions, device history, and
overridden monitoring parameters; "Restore from backup" — applies
the file (device history is only appended, not overwriting existing entries).

> ⚠️ The bot token in the file is in plain text — keep the backup as a secret.

**Sending backup to Telegram** — automatically, periodically
(`STARLINK_TELEGRAM_BACKUP_ENABLED=1`, a separate interval
`_INTERVAL_HOURS`, a week by default) and/or manually via the "📨 Send
backup to Telegram" button (independent of the automatic schedule).
Always sends the latest existing file, doesn't create a new one — a
safeguard in case the DB and backup remain on the same SD card.

## ➕ Extra: healthz, PWA

- **`GET /healthz`** — for external monitoring (UptimeRobot etc.):
  DB available + the watchdog is actually polling the dish (metric
  freshness < 3 poll cycles). `200 ok` / `503 degraded`, writes
  nothing to the log — safe to poll frequently.
- **Installable dashboard (PWA)** — "Add to home screen" in
  Chrome/Edge. The service worker caches only static assets
  (CSS/JS/icons) — API data is always live, the service worker never
  caches it. Separately: if the Pi is unreachable from the phone
  (out of the Pi's WiFi range) — the dashboard shows the last known
  state from the browser's `localStorage` with an explicit banner
  "last known state (X min ago)", to avoid confusion with live data.
- **Dark/light theme** — a switch on `/settings`, persists across sessions.
- **Interface language** — a switch on `/settings` (Ukrainian/English),
  applies to the web dashboard and Telegram bot together.

## 🛡️ Reliability

- **A watchdog for the watchdog** — `healthcheck.timer` (once/min)
  polls `/healthz`, forces a restart on a hang (not a crash)
- **Fewer writes to the SD card** — dish metrics are batch-INSERTed
  once per `DISH_METRICS_BATCH_INTERVAL_SEC` (30s), not every time;
  a graceful shutdown flushes the buffer before exiting
- **VACUUM/ANALYZE + DB integrity check** — once a day; corruption →
  Telegram notification and an emergency backup
- **Automatic backup** — once a week, keeps the last
  `AUTO_BACKUP_KEEP_COUNT` copies (4 by default)
- **Telegram retry** — on a network error, `SEND_RETRIES`
- **systemd log rotation** — `SystemMaxUse=200M`

## 🔄 About manual update checks

The "Check update status" button forces an immediate local poll of
dish/router. The local gRPC API has no "check for update in SpaceX's
cloud" command (that's done by the official app's cloud backend) —
`software_update` returns `FailedPrecondition`/`Unimplemented`, so
the button does the maximum that's technically possible.

## 🌐 Important network notes

Starlink Mini broadcasts its own WiFi. The RPi Zero 2 W has a single
WiFi module, so simultaneous access to Starlink and the regular
internet requires USB-Ethernet (recommended), or Starlink WiFi alone
(monitoring and dish reboot don't depend on external internet).

With USB-Ethernet, `install.sh` offers (only on first installation)
static IPs for both interfaces — DHCP on both can cause route conflicts.

**WAN failover**: `starlink-wan-failover.timer` (~20s) checks real
internet access via `wlan0`; when Starlink is unavailable —
`nmcli` lowers `wlan0`'s priority, `eth0` becomes the default for the
whole system. Routes to dish/router are unaffected. Telegram requests
have their own additional fallback (`SO_BINDTODEVICE` on `eth0` +
manual DNS resolution if needed) — works independently, no
configuration required.

## 📉 Reducing system load (optional, during installation)

`install.sh` (only on first installation) offers to disable services
unnecessary for headless monitoring: `bluetooth`/`hciuart`,
`avahi-daemon` (mDNS), `triggerhappy`, `ModemManager` — checks each
individually (only the ones actually active), disables (doesn't
remove, easy to restore with `systemctl enable --now`).

**Careful with `avahi-daemon`**: disabling it removes access via
`raspberrypi.local` (not by IP) — the script warns if it's active.

Network services (`ssh`, `NetworkManager`, `dhcpcd`) are never
offered — a headless device without physical access can't risk
being locked out of SSH. Separately — `apt-get autoremove`/`clean`.

## 🔍 Type checking (mypy)

All of the project's code (except `app/vendor/` — third-party code)
is typed and passes `mypy` cleanly. Check before committing/updating:
```bash
pip install -r requirements-dev.txt
mypy app/
```
Not `--strict` and no CI — for a solo hobby project of this scale,
running it locally as one more live check alongside `py_compile` is
more proportionate than investing in a full CI setup (details of the
decision — `docs/decisions-log.md`).

## ✅ Tests

441 tests (`pytest-randomly` — resilient to execution order), 16
files in `tests/`. Besides stateful logic (reboot-spam grouping,
target-version notification deduplication, version comparator,
eth0 fallback for Telegram) — hardware-dependent code (GPIO/SPI
display, shutdown button) is tested by substituting `sys.modules` for
`gpiod`/`board`/`digitalio`/`busio`/`adafruit_rgb_display` before
calling the function (these libraries are imported only inside the
functions themselves, not at module level). Every test uses an
isolated temporary DB (`tmp_path`), no side effects on real data.
To run:
```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```
Coverage by module: `pytest --cov=app --cov-report=term-missing`.

## 🗂️ Project structure

```
starlink-monitor/
├── app/            # Python: monitoring, Flask, Telegram, GPIO, display, i18n
├── templates/      # HTML (index, settings, stats)
├── static/         # JS/CSS/icons
├── tests/          # 441 tests (16 files), pytest-randomly
├── systemd/        # service unit files
├── scripts/        # install/update/uninstall + system checks
├── docs/           # architecture.md, index.md (full description of every file), decisions-log.md
├── requirements.txt      # production dependencies
└── requirements-dev.txt  # mypy/pytest/pytest-cov
```

Detailed description of every file — [`docs/index.md`](docs/index.md).

## 📦 Installation and updates

**📥 First installation:**
```bash
tar -xzf starlink-monitor.tar.gz && cd starlink-monitor
sudo bash scripts/install.sh
```
Installs system packages, `grpcurl`, a Python venv, sudo permissions,
systemd services; at the end — an optional prompt for static IPs and
an optional disabling of system services unnecessary for headless
monitoring (only on first installation, see the sections above —
"Important network notes" and "Reducing system load").

**🔁 Update** (archive in the home directory, e.g. via `scp`):
```bash
sudo bash /opt/starlink-monitor/scripts/update.sh
```
Unpacks and calls `install.sh` in update mode: system packages
aren't touched, only changed files are synced, the network prompt
isn't repeated. SHA-256 prevents re-installing the same archive
twice; the path can be given as an argument.

**✅ After installation:**
1. `sudo nmcli device wifi connect "<SSID>" password "<password>" ifname wlan0`
2. Dashboard: `http://<device-ip>:8080`

**🗑️ Full removal:**
```bash
sudo bash /opt/starlink-monitor/scripts/uninstall.sh
```
Stops services, removes the code and the sudoers rule. Removes
metrics history and Telegram settings only after a separate
confirmation — by default they're kept for a future reinstall.

## ⚙️ Configuration

Edited on the `/settings` page ("Monitoring parameters" panel,
53 parameters grouped by category: monitoring/reliability/
Telegram/GPIO) or manually in `/etc/starlink-monitor/env`. Full list
with comments — in `app/config.py`.

2 parameters are intentionally unavailable via `/settings` (manual
`.env`-file editing only) — `STARLINK_DB_PATH` (changing the DB path
without migrating data) and `STARLINK_WEBUI_HOST` (self-lockout —
you could cut yourself off from `/settings` by changing the listen
address).

Most important for initial setup:

| Variable | Default | Description |
|---|---|---|
| `STARLINK_DISH_ADDR` | `192.168.100.1:9200` | dish address |
| `STARLINK_ROUTER_ADDR` | `192.168.1.1:9000` | Mini's router-component address |
| `STARLINK_WEBUI_PORT` | `8080` | web interface port |
| `STARLINK_HISTORY_DAYS` | `14` | how many days to keep metrics/event history |
| `STARLINK_MAX_FAILURES` | `6` | how many failed polls before a watchdog reboot |

GPIO pins for optional features (button/LED/display) — see the
corresponding sections above, each optional feature is described
separately with a wiring diagram.

Parameters are read once at process startup — after saving on
`/settings`, click "Save and restart services" (button nearby), or
manually:
```bash
sudo systemctl restart starlink-monitor.service starlink-webui.service
```

## 📄 License

© 2026 JunioR. Distributed under the MIT license — see the
[`LICENSE`](./LICENSE) file.

The project includes (vendored, `app/vendor/starlink_grpc.py`) a file
from a third-party repository
[sparky8512/starlink-grpc-tools](https://github.com/sparky8512/starlink-grpc-tools)
for build reproducibility (not downloaded dynamically during
installation) — **its license terms are set by its own author**,
separate from this repository's MIT license. Updating to the latest
upstream version is optional, via `scripts/fetch_starlink_grpc.sh`.
