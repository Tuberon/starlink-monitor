"""
Тести для app/system_metrics.py - кожна метрика (uptime/cpu/memory/
disk/temp) обгорнута у власний try/except (get_system_metrics()
ніколи не кидає виняток - недоступна метрика лишається 0/None), тому
кожна except-гілка тестується незалежно від решти.
"""
from unittest.mock import mock_open, patch

from app.system_metrics import _read_temp_c, get_system_metrics


# ---- _read_temp_c() ----

def test_read_temp_c_success():
    with patch("builtins.open", mock_open(read_data="45678\n")):
        assert _read_temp_c() == 45.7


def test_read_temp_c_file_missing_returns_none():
    with patch("builtins.open", side_effect=FileNotFoundError()):
        assert _read_temp_c() is None


def test_read_temp_c_malformed_content_returns_none():
    with patch("builtins.open", mock_open(read_data="не число")):
        assert _read_temp_c() is None


# ---- get_system_metrics() - успішний шлях ----

def test_get_system_metrics_success_path():
    with patch("psutil.boot_time", return_value=1000.0), \
         patch("time.time", return_value=1100.0), \
         patch("psutil.cpu_percent", return_value=12.345), \
         patch("psutil.virtual_memory") as mock_mem, \
         patch("psutil.disk_usage") as mock_disk, \
         patch("app.system_metrics._read_temp_c", return_value=42.0):
        mock_mem.return_value = type("M", (), {"total": 2_000_000_000, "available": 1_000_000_000})()
        mock_disk.return_value = type("D", (), {"total": 32_000_000_000, "used": 10_000_000_000, "free": 22_000_000_000})()
        result = get_system_metrics()

    assert result["uptime_s"] == 100
    assert result["cpu_percent"] == 12.3
    assert result["mem_total_mb"] == 2000.0
    assert result["mem_used_mb"] == 1000.0
    assert result["temp_c"] == 42.0
    assert result["disk_total_gb"] == 32.0


# ---- get_system_metrics() - кожна except-гілка окремо ----

def test_get_system_metrics_uptime_error_defaults_to_zero():
    with patch("psutil.boot_time", side_effect=RuntimeError("недоступно")), \
         patch("psutil.cpu_percent", return_value=0.0), \
         patch("psutil.virtual_memory") as mock_mem, \
         patch("psutil.disk_usage") as mock_disk:
        mock_mem.return_value = type("M", (), {"total": 1, "available": 1})()
        mock_disk.return_value = type("D", (), {"total": 1, "used": 1, "free": 1})()
        result = get_system_metrics()
    assert result["uptime_s"] == 0


def test_get_system_metrics_cpu_error_defaults_to_zero():
    with patch("psutil.boot_time", return_value=1000.0), \
         patch("psutil.cpu_percent", side_effect=RuntimeError("недоступно")), \
         patch("psutil.virtual_memory") as mock_mem, \
         patch("psutil.disk_usage") as mock_disk:
        mock_mem.return_value = type("M", (), {"total": 1, "available": 1})()
        mock_disk.return_value = type("D", (), {"total": 1, "used": 1, "free": 1})()
        result = get_system_metrics()
    assert result["cpu_percent"] == 0.0


def test_get_system_metrics_memory_error_defaults_to_zero():
    with patch("psutil.boot_time", return_value=1000.0), \
         patch("psutil.cpu_percent", return_value=0.0), \
         patch("psutil.virtual_memory", side_effect=RuntimeError("недоступно")), \
         patch("psutil.disk_usage") as mock_disk:
        mock_disk.return_value = type("D", (), {"total": 1, "used": 1, "free": 1})()
        result = get_system_metrics()
    assert result["mem_total_mb"] == 0.0
    assert result["mem_used_mb"] == 0.0
    assert result["mem_free_mb"] == 0.0


def test_get_system_metrics_disk_error_defaults_to_zero():
    with patch("psutil.boot_time", return_value=1000.0), \
         patch("psutil.cpu_percent", return_value=0.0), \
         patch("psutil.virtual_memory") as mock_mem, \
         patch("psutil.disk_usage", side_effect=RuntimeError("недоступно")):
        mock_mem.return_value = type("M", (), {"total": 1, "available": 1})()
        result = get_system_metrics()
    assert result["disk_total_gb"] == 0.0
    assert result["disk_used_gb"] == 0.0
    assert result["disk_free_gb"] == 0.0


def test_get_system_metrics_never_raises_when_everything_fails():
    """Реальна мета всього модуля: навіть якщо ВСІ psutil-виклики
    провалюються одночасно - функція МАЄ повернути dict з дефолтними
    значеннями, не кинути виняток (watchdog-цикл не має падати через
    недоступність системних метрик)."""
    with patch("psutil.boot_time", side_effect=RuntimeError()), \
         patch("psutil.cpu_percent", side_effect=RuntimeError()), \
         patch("psutil.virtual_memory", side_effect=RuntimeError()), \
         patch("psutil.disk_usage", side_effect=RuntimeError()), \
         patch("app.system_metrics._read_temp_c", return_value=None):
        result = get_system_metrics()
    assert result["uptime_s"] == 0
    assert result["temp_c"] is None
