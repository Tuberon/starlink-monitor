"""
Збір системних метрик самого Raspberry Pi: uptime, завантаження CPU,
пам'ять, місце на диску, температура SoC.

Температура читається з /sys/class/thermal/thermal_zone0/temp - цей шлях
є стандартним на Raspberry Pi OS (і Linux загалом) і не потребує
додаткових утиліт на кшталт vcgencmd.
"""
import logging
import os
import subprocess
import time
from typing import Any, Optional

import psutil

logger = logging.getLogger("system_metrics")

THERMAL_ZONE_PATH = "/sys/class/thermal/thermal_zone0/temp"
APT_STAMP_PATH = "/var/lib/apt/periodic/update-success-stamp"
# apt list читає лише локальний кеш пакетів (без мережевого запиту) -
# сам кеш оновлює системний apt-daily.timer (стандартний на Raspberry
# Pi OS) раз на добу, тому частіша перевірка тут не додає нової
# інформації, лише зайве навантажує Pi Zero 2 W субпроцесом на кожен
# запит дашборду. Кешується в пам'яті процесу webui.
_APT_CHECK_INTERVAL_SEC = 3600
_apt_cache: dict[str, Optional[float]] = {"updates_count": None, "last_apt_update_ts": None, "checked_ts": None}


def get_apt_updates_info(force: bool = False) -> dict[str, Any]:
    """Кількість доступних оновлень пакетів (apt) і час останньої
    системної перевірки (mtime apt-daily.timer's stamp-файлу). Ніколи
    не кидає виняток - при будь-якій помилці count/last_apt_update_ts
    лишаються None (фронтенд показує "—"). force=True обходить
    internal-кеш - потрібно для кнопки "Перевірити оновлення пакетів"
    на дашборді (після примусового sudo apt update користувач очікує
    СВІЖЕ число, не те, що досі кешоване до година наперед)."""
    now = time.time()
    if not force and _apt_cache["checked_ts"] is not None and now - _apt_cache["checked_ts"] < _APT_CHECK_INTERVAL_SEC:
        return dict(_apt_cache)

    count = None
    try:
        result = subprocess.run(
            ["apt", "list", "--upgradable"],
            capture_output=True, text=True, timeout=15,
        )
        lines = [
            line for line in result.stdout.strip().split("\n")
            if line and not line.startswith("Listing")
        ]
        count = len(lines)
    except Exception as e:
        logger.debug("Не вдалося перевірити оновлення apt: %s", e)

    last_apt_update_ts = None
    try:
        last_apt_update_ts = os.path.getmtime(APT_STAMP_PATH)
    except OSError:
        pass

    _apt_cache.update(updates_count=count, last_apt_update_ts=last_apt_update_ts, checked_ts=now)
    return dict(_apt_cache)


def _read_temp_c() -> Optional[float]:
    try:
        with open(THERMAL_ZONE_PATH) as f:
            raw = f.read().strip()
        return round(int(raw) / 1000.0, 1)
    except Exception as e:
        logger.debug("Не вдалося прочитати температуру: %s", e)
        return None


def get_system_metrics() -> dict[str, Any]:
    """Збирає поточні системні метрики. Ніколи не кидає виняток -
    відсутні/недоступні метрики просто лишаються None/0."""
    result: dict[str, Any] = {"timestamp": time.time()}

    try:
        result["uptime_s"] = int(time.time() - psutil.boot_time())
    except Exception as e:
        logger.warning("Не вдалося отримати uptime: %s", e)
        result["uptime_s"] = 0

    try:
        # interval=None - миттєве значення відносно попереднього виклику
        # (psutil кешує попередній замір усередині процесу)
        result["cpu_percent"] = round(psutil.cpu_percent(interval=None), 1)
    except Exception as e:
        logger.warning("Не вдалося отримати завантаження CPU: %s", e)
        result["cpu_percent"] = 0.0

    try:
        mem = psutil.virtual_memory()
        result["mem_total_mb"] = round(mem.total / 1e6, 1)
        result["mem_used_mb"] = round((mem.total - mem.available) / 1e6, 1)
        result["mem_free_mb"] = round(mem.available / 1e6, 1)
    except Exception as e:
        logger.warning("Не вдалося отримати дані про пам'ять: %s", e)
        result["mem_total_mb"] = result["mem_used_mb"] = result["mem_free_mb"] = 0.0

    try:
        disk = psutil.disk_usage("/")
        result["disk_total_gb"] = round(disk.total / 1e9, 2)
        result["disk_used_gb"] = round(disk.used / 1e9, 2)
        result["disk_free_gb"] = round(disk.free / 1e9, 2)
    except Exception as e:
        logger.warning("Не вдалося отримати дані про диск: %s", e)
        result["disk_total_gb"] = result["disk_used_gb"] = result["disk_free_gb"] = 0.0

    result["temp_c"] = _read_temp_c()

    return result
