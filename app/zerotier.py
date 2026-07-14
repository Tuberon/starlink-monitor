"""
Керування ZeroTier VPN для віддаленого доступу до Raspberry Pi.

Використовує системний zerotier-cli (встановлюється install.sh) через
subprocess. join/leave потребують sudo - відповідні NOPASSWD-правила
додаються install.sh (аналогічно до systemctl restart/reboot).

ZeroTier network ID зберігається в БД (settings), не в env-файлі, щоб
можна було змінити з веб-інтерфейсу без перезапуску сервісу.
"""
import logging
import re
import shutil
import subprocess

from app import db

logger = logging.getLogger("zerotier")

REQUEST_TIMEOUT = 15
NETWORK_ID_RE = re.compile(r"^[0-9a-fA-F]{16}$")


def _cli_path():
    return shutil.which("zerotier-cli")


def get_network_id() -> str:
    return db.get_setting("zerotier_network_id", "") or ""


def set_network_id(network_id: str):
    db.set_setting("zerotier_network_id", network_id.strip())


def get_status() -> dict:
    """Повертає стан ZeroTier: чи встановлено, чи запущено демон,
    чи приєднано до мережі, чи отримано IP. Ніколи не кидає виняток."""
    cli = _cli_path()
    if not cli:
        return {"installed": False, "running": False, "joined": False,
                "address": "", "network_id": get_network_id(), "assigned_ips": [], "error": ""}

    network_id = get_network_id()
    try:
        info = subprocess.run(["sudo", cli, "info"], capture_output=True, text=True, timeout=REQUEST_TIMEOUT)
        if info.returncode != 0:
            return {"installed": True, "running": False, "joined": False,
                    "address": "", "network_id": network_id, "assigned_ips": [],
                    "error": (info.stderr or info.stdout or "zerotier-cli info failed").strip()}

        address_match = re.search(r"^200 info (\w+)", info.stdout)
        address = address_match.group(1) if address_match else ""

        joined = False
        assigned_ips = []
        if network_id:
            listnw = subprocess.run(
                ["sudo", cli, "listnetworks"], capture_output=True, text=True, timeout=REQUEST_TIMEOUT
            )
            for line in listnw.stdout.splitlines():
                if network_id in line:
                    joined = True
                    parts = line.split()
                    if parts:
                        ip_field = parts[-1]
                        assigned_ips = [ip.strip() for ip in ip_field.split(",") if ip.strip() and ip.strip() != "-"]

        return {
            "installed": True,
            "running": True,
            "joined": joined,
            "address": address,
            "network_id": network_id,
            "assigned_ips": assigned_ips,
            "error": "",
        }
    except subprocess.TimeoutExpired:
        return {"installed": True, "running": False, "joined": False,
                "address": "", "network_id": network_id, "assigned_ips": [], "error": "timeout"}
    except Exception as e:
        logger.warning("Помилка отримання статусу ZeroTier: %s", e)
        return {"installed": True, "running": False, "joined": False,
                "address": "", "network_id": network_id, "assigned_ips": [], "error": str(e)}


def join_network(network_id: str) -> tuple[bool, str]:
    network_id = network_id.strip()
    if not NETWORK_ID_RE.match(network_id):
        return False, "Некоректний Network ID (очікується 16 hex-символів)"

    cli = _cli_path()
    if not cli:
        return False, "zerotier-cli не знайдено (ZeroTier не встановлено)"

    try:
        result = subprocess.run(
            ["sudo", cli, "join", network_id], capture_output=True, text=True, timeout=REQUEST_TIMEOUT
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "join failed").strip()
            return False, err
        set_network_id(network_id)
        return True, "приєднано до мережі"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


def leave_network() -> tuple[bool, str]:
    network_id = get_network_id()
    if not network_id:
        return False, "Мережу не налаштовано"

    cli = _cli_path()
    if not cli:
        return False, "zerotier-cli не знайдено"

    try:
        result = subprocess.run(
            ["sudo", cli, "leave", network_id], capture_output=True, text=True, timeout=REQUEST_TIMEOUT
        )
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "leave failed").strip()
            return False, err
        set_network_id("")
        return True, "від'єднано від мережі"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)
