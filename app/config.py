"""
Конфігурація Starlink Monitor. Перевизначається через змінні
середовища (systemd EnvironmentFile) або /etc/starlink-monitor/config.local.py.
Повний опис змінних - README.md, таблиця "Конфігурація".
"""
import os

DISH_ADDR = os.environ.get("STARLINK_DISH_ADDR", "192.168.100.1:9200")
DISH_HTTP_TIMEOUT = float(os.environ.get("STARLINK_DISH_TIMEOUT", "5"))
ROUTER_ADDR = os.environ.get("STARLINK_ROUTER_ADDR", "192.168.1.1:9000")

POLL_INTERVAL_SEC = int(os.environ.get("STARLINK_POLL_INTERVAL", "10"))
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("STARLINK_MAX_FAILURES", "6"))  # 6*10s = 60s недоступності
MIN_REBOOT_INTERVAL_SEC = int(os.environ.get("STARLINK_MIN_REBOOT_INTERVAL", "120"))  # захист від reboot-loop
OBSTRUCTION_WARN_FRACTION = float(os.environ.get("STARLINK_OBSTRUCTION_WARN", "0.05"))

# reboot при software_update_state==REBOOT_REQUIRED або alerts.install_pending
AUTO_REBOOT_ON_UPDATE_READY = os.environ.get("STARLINK_AUTO_REBOOT_ON_UPDATE", "1") == "1"

DB_PATH = os.environ.get("STARLINK_DB_PATH", "/var/lib/starlink-monitor/history.db")
HISTORY_RETENTION_DAYS = int(os.environ.get("STARLINK_HISTORY_DAYS", "30"))

WEBUI_HOST = os.environ.get("STARLINK_WEBUI_HOST", "0.0.0.0")
WEBUI_PORT = int(os.environ.get("STARLINK_WEBUI_PORT", "8080"))

# GPIO BCM pin для фізичної кнопки виключення; 0 = вимкнено (за замовчуванням)
SHUTDOWN_BUTTON_GPIO_PIN = int(os.environ.get("STARLINK_SHUTDOWN_BUTTON_PIN", "0"))
SHUTDOWN_BUTTON_HOLD_SEC = float(os.environ.get("STARLINK_SHUTDOWN_BUTTON_HOLD_SEC", "3"))

_local_cfg = "/etc/starlink-monitor/config.local.py"
if os.path.exists(_local_cfg):
    with open(_local_cfg) as f:
        exec(f.read())
