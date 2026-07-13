# Deployment

Target: Raspberry Pi Zero 2 W, Raspberry Pi OS (Bookworm).

## Install

```bash
tar -xzf starlink-monitor.tar.gz
cd starlink-monitor
sudo bash scripts/install.sh
```

`scripts/install.sh` installs system packages + `grpcurl` (apt or GitHub release binary matched to device arch) + a Python venv, copies the project to `/opt/starlink-monitor`, configures a scoped sudoers entry for the service user (specific `systemctl` commands only, not blanket access), and installs/enables the systemd units below.

## systemd units ([systemd/](../systemd/))

| Unit | Runs |
|---|---|
| `starlink-monitor.service` | `app/monitor.py` — background watchdog + metrics collection |
| `starlink-webui.service` | `app/webapp.py` — Flask dashboard |
| `starlink-grpc-fetch.service` | one-shot: fetches `starlink_grpc.py` into `app/vendor/` once WiFi/dish is reachable |
| `starlink-updater.timer` / `.service` | periodic `app/updater.py` — apt/pip/git updates, scheduled Pi reboot |

## Networking

The Pi has one WiFi radio, needed for both the Starlink Mini WiFi and normal internet. Preferred: USB-Ethernet for internet, WiFi (`wlan0`) dedicated to the Starlink Mini AP. Core monitoring/reboot functionality works without external internet either way.

## Config

All settings are environment variables in `/etc/starlink-monitor/env` (or `/etc/starlink-monitor/config.local.py` for a Python override) — see [app/config.py](../app/config.py) for the full list. Key ones: `STARLINK_DISH_ADDR`, `STARLINK_ROUTER_ADDR`, `STARLINK_POLL_INTERVAL`, `STARLINK_MAX_FAILURES`, `STARLINK_MIN_REBOOT_INTERVAL`, `STARLINK_AUTO_REBOOT_ON_UPDATE`, `STARLINK_WEBUI_PORT`. After editing: `sudo systemctl restart starlink-monitor.service starlink-webui.service`.

## Third-party dependency

`app/vendor/starlink_grpc.py` is fetched separately (`scripts/fetch_starlink_grpc.sh`) from [sparky8512/starlink-grpc-tools](https://github.com/sparky8512/starlink-grpc-tools) and is not part of this repo — it has its own license.
