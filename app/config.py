"""
Конфігурація Starlink Monitor.
Значення можна перевизначити через змінні середовища (systemd EnvironmentFile)
або через /etc/starlink-monitor/config.local.py, якщо він існує.
"""
import os

# --- Starlink dish ---
DISH_ADDR = os.environ.get("STARLINK_DISH_ADDR", "192.168.100.1:9200")
DISH_HTTP_TIMEOUT = float(os.environ.get("STARLINK_DISH_TIMEOUT", "5"))

# --- Starlink router (окремий логічний пристрій, "cohoused" в тому ж
# корпусі Mini, зі своєю версією прошивки - відповідає на адресі роутера,
# а не dish, оскільки роутер сам роздає DHCP в цій підмережі) ---
ROUTER_ADDR = os.environ.get("STARLINK_ROUTER_ADDR", "192.168.1.1:9000")

# --- Опитування ---
POLL_INTERVAL_SEC = int(os.environ.get("STARLINK_POLL_INTERVAL", "10"))

# --- Watchdog: коли автоматично ребутити dish ---
# Скільки послідовних невдалих опитувань (dish не відповідає) перш ніж ребутити
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("STARLINK_MAX_FAILURES", "6"))  # 6*10s = 60s недоступності
# Мінімальний інтервал між авто-ребутами dish (захист від reboot-loop)
MIN_REBOOT_INTERVAL_SEC = int(os.environ.get("STARLINK_MIN_REBOOT_INTERVAL", "1800"))  # 30 хв
# Поріг фракції обструкції, вище якого просто попереджаємо (не ребутимо — це фізична перешкода)
OBSTRUCTION_WARN_FRACTION = float(os.environ.get("STARLINK_OBSTRUCTION_WARN", "0.05"))

# --- Авто-reboot dish при готовому оновленні ПЗ ---
# Якщо dish повідомляє software_update_state == REBOOT_REQUIRED, або
# alerts.install_pending == true - оновлення вже завантажене й готове,
# і dish просто чекає на reboot, щоб застосувати його. Вмикає автоматичний
# reboot у такому випадку (той самий MIN_REBOOT_INTERVAL_SEC захищає
# від повторних спроб, якщо перший reboot чомусь не допоміг).
AUTO_REBOOT_ON_UPDATE_READY = os.environ.get("STARLINK_AUTO_REBOOT_ON_UPDATE", "1") == "1"

# --- База даних ---
DB_PATH = os.environ.get("STARLINK_DB_PATH", "/var/lib/starlink-monitor/history.db")
HISTORY_RETENTION_DAYS = int(os.environ.get("STARLINK_HISTORY_DAYS", "30"))

# --- Веб-інтерфейс ---
WEBUI_HOST = os.environ.get("STARLINK_WEBUI_HOST", "0.0.0.0")
WEBUI_PORT = int(os.environ.get("STARLINK_WEBUI_PORT", "8080"))

# Локальне перевизначення (не в git), напр. /etc/starlink-monitor/config.local.py
_local_cfg = "/etc/starlink-monitor/config.local.py"
if os.path.exists(_local_cfg):
    with open(_local_cfg) as f:
        exec(f.read())
