"""
Збір системних метрик самого Raspberry Pi: uptime, завантаження CPU,
пам'ять, місце на диску, температура SoC.

Температура читається з /sys/class/thermal/thermal_zone0/temp - цей шлях
є стандартним на Raspberry Pi OS (і Linux загалом) і не потребує
додаткових утиліт на кшталт vcgencmd.
"""
import logging
import time

import psutil

logger = logging.getLogger("system_metrics")

THERMAL_ZONE_PATH = "/sys/class/thermal/thermal_zone0/temp"


def _read_temp_c():
    try:
        with open(THERMAL_ZONE_PATH) as f:
            raw = f.read().strip()
        return round(int(raw) / 1000.0, 1)
    except Exception as e:
        logger.debug("Не вдалося прочитати температуру: %s", e)
        return None


def get_system_metrics() -> dict:
    """Збирає поточні системні метрики. Ніколи не кидає виняток -
    відсутні/недоступні метрики просто лишаються None/0."""
    result = {"timestamp": time.time()}

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
