# 🛰️ Starlink Mini Monitor & Watchdog — Raspberry Pi Zero 2 W

*Читати українською: [README.md](README.md)*

Autonomous monitor and watchdog for Starlink Mini on Raspberry Pi Zero 2 W.

**Contents**: [What it does](#-what-it-does) · [Wiring diagram](#-wiring-diagram-optional-hardware) ·
[GPIO button](#-physical-shutdown-button-gpio) ·
[Activity LED](#-sd-card-activity-led-gpio) ·
[TFT display](#️-physical-tft-display-st7789-spi) ·
[Telegram](#-telegram-notifications-and-commands) · [Backup](#-settings-backuprestore) ·
[healthz/PWA](#-extra-healthz-pwa) · [Reliability](#️-reliability) ·
[Manual update check](#-about-manual-update-checks) · [Network](#-important-network-notes) · [Security](#-security) ·
[Load reduction](#-reducing-system-load-optional-during-installation) ·
[Type checking](#-type-checking-mypy) · [Tests](#-tests) ·
[Structure](#️-project-structure) · [Installation](#-installation-and-updates) ·
[Configuration](#️-configuration) · [Dependencies](#-dependencies) · [License](#-license)

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
6. Telegram: notifications + commands `/status /checkupdates /reboot /id /help` (`/start` is the same as `/help`).
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
Pi actually starts shutting down (`STARLINK_DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC`, 2s by default).

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
firmware versions). Disabled by default (`STARLINK_DISPLAY_ENABLED=0`). Library — Adafruit CircuitPython ST7789.

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
- Never logged or shown: the "obstruction map reset" dish alert and the
  router "update download failed" state (a transient SpaceX cloud error,
  the router retries on its own)
- **Starlink off** (night, power outage): if even the router does not
  respond (3 polls in a row, ~30 s; shorter WiFi drops are ignored), the watchdog does not reboot the dish and does not log a line
  per poll. In the morning — one message "✅ Starlink is available again,
  was off for N h" (only if longer than `NOTIFICATIONS_MUTE_AFTER`, 15 min)
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

- **`GET /healthz`** — for external monitoring (UptimeRobot etc.): DB
  available + the watchdog is actually polling the dish (metric
  freshness no older than 3 poll cycles + the batch-write interval
  `DISH_METRICS_BATCH_INTERVAL_SEC`, 60 s by default). `200 ok` / `503
  degraded`, writes nothing to the log — safe to poll frequently.
- **Installable dashboard (PWA)** — "Add to home screen" in
  Chrome/Edge. The service worker caches only static assets
  (CSS/JS/icons) — API data is always live, the service worker never
  caches it. Separately: if the Pi is unreachable from the phone
  (out of the Pi's WiFi range) — the dashboard shows the last known
  state from the browser's `localStorage` with an explicit banner
  "last known state (X min ago)", to avoid confusion with live data.
- **Dark/light theme** — a switch on `/settings`, persists across sessions.
- **Interface language** — a switch on `/settings` (Ukrainian/English),
  applies to the web dashboard and Telegram bot together, including
  automatic notifications.

## 🛡️ Reliability

- **A watchdog for the watchdog** — `starlink-monitor-healthcheck.timer`
  (once/min) polls `/healthz`, forces a restart on a hang (not a crash)
- **Fewer writes to the SD card** — dish metrics are batch-INSERTed
  once per `DISH_METRICS_BATCH_INTERVAL_SEC` (30s), not every time (a
  state change — online/offline or update — is written at once); a
  graceful shutdown flushes the buffer before exiting; the SQLite WAL is
  not flushed to disk after every write (~150× fewer forced syncs), while
  settings are synced at once
- **ANALYZE + DB integrity check** — once a day (`VACUUM` only when ≥ 30%
  of pages are free: it rewrites the whole DB); corruption →
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
IPv6 is also disabled on the Starlink WiFi profile: when Starlink has
no internet, system DNS/HTTPS requests would otherwise go through a
dead IPv6 route instead of working IPv4 via `eth0`. On an already
installed system (the network block isn't repeated on updates) —
manually: `sudo nmcli connection modify "<WiFi profile>" ipv6.method disabled`.

**WAN failover**: `starlink-wan-failover.timer` (~30s) checks real
internet access via `wlan0`; when Starlink is unavailable —
`nmcli` lowers `wlan0`'s priority, `eth0` becomes the default for the
whole system. Routes to dish/router are unaffected. Telegram requests
have their own additional fallback (`SO_BINDTODEVICE` on `eth0` +
manual DNS resolution if needed) — works independently, no
configuration required.

## 🔒 Security

- **The web interface has no authentication** (port `8080`,
  `STARLINK_WEBUI_HOST=0.0.0.0`) — a deliberate decision for a trusted
  local network. Do not expose the port to the internet (port
  forwarding, public tunnels): whoever can see the dashboard can reboot
  the dish and shut the Pi down.
- **Cross-site request protection**: a POST from another website (judged
  by the `Origin` and `Sec-Fetch-Site` headers, which the browser sets
  itself) is rejected with 403; `curl` and scripts without those headers
  work. DNS rebinding is not covered (a request from a foreign name that
  resolves to the station's IP).
- **Privileges**: the services run as an unprivileged user; `sudo`
  allows it exactly four commands (`systemctl restart` of two services,
  `reboot`, `poweroff`). Only the healthcheck and WAN-failover run as
  root, and the scripts they execute belong to root.
- **Secrets**: the Telegram token is in the `env` file (mode 600) and in
  the DB (data directory 700); it is masked in logs automatically. **A
  backup contains the token** — keep it as a secret.

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

Conversely, `install.sh` enables `fstrim.timer` (periodic TRIM for the
SD card; if the card doesn't support it, it does nothing and does no
harm).

## 🔍 Type checking (mypy)

All of the project's code (except `app/vendor/` — third-party code) is
typed and passes `mypy` in **strict mode** (`app/*` — the equivalent of
`--strict`, flags in `mypy.ini`; typing in `tests/` is lenient). Calls
into `requests`, `psutil`, `dns` are checked against real stubs
(`types-*` in `requirements-dev.txt`); import-ignoring is allowed only
for the Pi hardware libraries — strictness is held by
`test_mypy_config_stays_strict`. The `mypy` result depends on the
installed packages (over 30 errors without Flask, all in `webapp.py`),
so run it in the full dev environment. Check before committing/updating:
```bash
pip install -r requirements-dev.txt
mypy app/
ruff check .
```
`ruff check` is code linting (unused code, common pitfalls, security;
rules in `ruff.toml`), also run as one of the tests. Not
auto-formatting: `ruff format` is deliberately not used. No CI — for a solo hobby project of this scale,
running it locally as one more live check alongside `py_compile` is
more proportionate than investing in a full CI setup (details of the
decision — `docs/decisions-log.md`).

## ✅ Tests

1108 tests (`pytest-randomly` — resilient to execution order), 27
files in `tests/`. Besides stateful logic (reboot-spam grouping,
target-version notification deduplication, version comparator,
eth0 fallback for Telegram) — hardware-dependent code (GPIO/SPI
display, shutdown button) is tested by substituting `sys.modules` for
`gpiod`/`board`/`digitalio`/`busio`/`adafruit_rgb_display` before
calling the function (these libraries are imported only inside the
functions themselves, not at module level). Every test automatically
gets a temporary DB, settings file and backups directory
(`tests/conftest.py`) — tests never touch the live
`/etc/starlink-monitor` and `/var/lib/starlink-monitor`. To run (on
the development machine):
```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest
```
Coverage by module: `pytest --cov=app --cov-report=term-missing`.

The root-ownership tests (`test_install_security.py`) run only as root
and are skipped for an ordinary user.

**On the Pi itself** — in a separate environment (not the live
`/opt/.../venv`, no `--break-system-packages`) and without hardware
libraries (`adafruit-*`), so no test can touch real GPIO/SPI. The
services may keep running:
```bash
cd ~/starlink-monitor            # extracted archive
python3 -m venv ~/dw-dev
grep -vi adafruit requirements.txt > /tmp/req-tests.txt
~/dw-dev/bin/pip install -r /tmp/req-tests.txt -r requirements-dev.txt
~/dw-dev/bin/mypy app/ && ~/dw-dev/bin/ruff check . && ~/dw-dev/bin/pytest
```

## 🗂️ Project structure

```
starlink-monitor/
├── app/            # Python: monitoring, Flask, Telegram, GPIO, display, i18n
├── templates/      # HTML (index, settings, stats)
├── static/         # JS/CSS/icons
├── tests/          # 1108 tests (27 files), pytest-randomly
├── systemd/        # service unit files
├── scripts/        # install/update/uninstall + system checks
├── docs/           # architecture.md, index.md (full description of every file), decisions-log.md, plan.md
├── requirements.txt      # production dependencies (direct, exact pins)
├── constraints.txt       # pinned TRANSITIVE versions (pip install -c)
├── requirements-dev.txt  # mypy/pytest/ruff/pip-audit/hypothesis
├── mypy.ini              # mypy: strict mode for app/*
├── pytest.ini            # pytest settings
├── ruff.toml             # ruff: rules F, B, E9, PLE, S
└── LICENSE               # MIT
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

After installation the `/opt/starlink-monitor` directory and `scripts/`
belong to root: root executes them (the healthcheck and WAN-failover
units and `update.sh` under `sudo`), so the unprivileged service user
cannot replace them. Edit them manually via `sudo`; `app/` and `venv`
stay with the service user.

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

3 parameters are intentionally unavailable via `/settings` (manual
`.env`-file editing only) — `STARLINK_DB_PATH` (changing the DB path
without migrating data), `STARLINK_WEBUI_HOST` (self-lockout — you
could cut yourself off from `/settings` by changing the listen
address) and `STARLINK_AUTO_BACKUP_DIR` (default is computed next to
the DB).

`/settings` checks not only the type but also the allowed range of every
numeric parameter: it rejects `nan`/`inf`, negative values and `0` for timers
(there `0` would mean "on every iteration", not "disabled"); zero stays
allowed only where it really means "disabled" (pins, backlight auto-off).
The addresses `STARLINK_DISH_ADDR`/`STARLINK_ROUTER_ADDR` are accepted only as
`host:port` (port 1–65535). Manual editing of the `env` file is not range-checked, but it does not break the services either: an invalid value (empty, `abc`) is replaced by the default with a warning in the log; booleans accept `1/true/yes/on` and `0/false/no/off`.

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

## 🧩 Dependencies

`requirements.txt` pins the direct packages, `constraints.txt` the
transitive ones (`install.sh` runs `pip install -r requirements.txt -c
constraints.txt`; without it pip never upgraded already-installed,
including system, versions). The hardware `adafruit-*` packages are
deliberately not pinned. Vulnerability audit: `pip install pip-audit &&
scripts/audit_deps.sh`.

**Vendored `starlink_grpc.py`.** Its origin is recorded in
`app/vendor/PROVENANCE` (upstream commit, date, sha256; a test checks
them against the file). Updating to the latest upstream version is
optional, via `scripts/fetch_starlink_grpc.sh` (download to a temporary
file, checks, `.prev` backup, atomic replace; `--commit=<sha>` — a
specific revision; `--wait-for-dish` — wait until the dish is reachable;
`--restart-services` — restart the services after success).

## 📄 License

© 2026 JunioR. Distributed under the MIT license — see the
[`LICENSE`](./LICENSE) file.

The project includes (vendored, `app/vendor/starlink_grpc.py`) a file
from a third-party repository
[sparky8512/starlink-grpc-tools](https://github.com/sparky8512/starlink-grpc-tools)
for build reproducibility (not downloaded dynamically during
installation) — **its license terms are set by its own author**,
separate from this repository's MIT license (Unlicense — public domain).
Its origin and update procedure are in the “Dependencies” section.
