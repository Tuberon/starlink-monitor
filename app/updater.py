"""
Автоматична перевірка та встановлення оновлень.

Викликається періодично через systemd timer (starlink-updater.timer),
НЕ безперервним циклом — це навмисно, щоб не тримати apt-lock довго
і не заважати основному моніторингу.

Що робить:
1. apt-get update && apt-get -y upgrade (тільки security/звичайні пакунки;
   unattended-upgrades далі підстраховує це системно).
2. Оновлює Python-залежності проєкту (pip install -r requirements.txt --upgrade),
   якщо змінився requirements.txt.
3. git pull у каталозі проєкту (якщо це git-репозиторій) і рестарт
   сервісів monitor/webui при змінах коду.
4. Якщо після оновлень система сигналізує /var/run/reboot-required —
   плановий reboot самого Pi у "тихе" вікно (щоб не рвати активний
   моніторинг посеред дня), контрольовано, з логуванням у БД.
"""
import logging
import os
import subprocess
import time
from datetime import datetime

from app import config, db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("updater")

PROJECT_DIR = os.environ.get("STARLINK_PROJECT_DIR", "/opt/starlink-monitor")


def run(cmd, timeout=600, **kwargs):
    logger.info("Виконую: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, **kwargs
        )
        if result.returncode != 0:
            logger.warning("Команда завершилась з кодом %d: %s", result.returncode, result.stderr[:2000])
        return result.returncode == 0, (result.stdout + result.stderr)[-4000:]
    except subprocess.TimeoutExpired:
        logger.error("Таймаут виконання команди: %s", cmd)
        return False, "timeout"
    except Exception as e:
        logger.error("Помилка виконання команди %s: %s", cmd, e)
        return False, str(e)


def apt_update_upgrade():
    ok1, out1 = run(["sudo", "apt-get", "update", "-qq"])
    ok2, out2 = run(
        ["sudo", "apt-get", "-y", "-qq", "upgrade"],
        env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"},
    )
    ok3, out3 = run(["sudo", "apt-get", "-y", "-qq", "autoremove"])
    success = ok1 and ok2 and ok3
    db.insert_event(
        "system_update",
        f"apt update/upgrade/autoremove: {'OK' if success else 'FAILED'}",
        success=success,
    )
    return success


def update_python_deps():
    req_file = os.path.join(PROJECT_DIR, "requirements.txt")
    venv_pip = os.path.join(PROJECT_DIR, "venv", "bin", "pip")
    if not os.path.exists(req_file) or not os.path.exists(venv_pip):
        return True
    ok, out = run([venv_pip, "install", "--upgrade", "-r", req_file])
    db.insert_event("system_update", f"pip upgrade: {'OK' if ok else 'FAILED'}", success=ok)
    return ok


def git_pull_project():
    if not os.path.isdir(os.path.join(PROJECT_DIR, ".git")):
        return False  # не git-репозиторій, немає що тягнути

    ok, before = run(["git", "-C", PROJECT_DIR, "rev-parse", "HEAD"])
    prev_commit = before.strip()

    ok, out = run(["git", "-C", PROJECT_DIR, "pull", "--ff-only"])
    if not ok:
        db.insert_event("system_update", f"git pull failed: {out[:500]}", success=False)
        return False

    _, after = run(["git", "-C", PROJECT_DIR, "rev-parse", "HEAD"])
    new_commit = after.strip()
    changed = prev_commit != new_commit

    if changed:
        db.insert_event("system_update", f"Код оновлено {prev_commit[:7]} -> {new_commit[:7]}", success=True)
        restart_services()

    return changed


def restart_services():
    for svc in ("starlink-monitor.service", "starlink-webui.service"):
        ok, out = run(["sudo", "systemctl", "restart", svc])
        db.insert_event("system_update", f"restart {svc}: {'OK' if ok else 'FAILED'}", success=ok)


def reboot_required() -> bool:
    return os.path.exists("/var/run/reboot-required")


def maybe_reboot_system():
    """Ребутить сам Pi лише якщо це потрібно і зараз "тихе" вікно (напр. 4 ранку)."""
    if not reboot_required():
        return False

    if not config.AUTO_UPDATE_REBOOT_IF_NEEDED:
        logger.info("Потрібен reboot системи, але AUTO_UPDATE_REBOOT_IF_NEEDED вимкнено.")
        return False

    now_hour = datetime.now().hour
    if now_hour != config.UPDATE_REBOOT_WINDOW_HOUR:
        logger.info(
            "Потрібен reboot системи, чекаю на вікно (%02d:00). Зараз %02d:00.",
            config.UPDATE_REBOOT_WINDOW_HOUR,
            now_hour,
        )
        return False

    logger.warning("Виконую плановий reboot системи (вікно %02d:00)", config.UPDATE_REBOOT_WINDOW_HOUR)
    db.insert_event("system_reboot", "Плановий reboot після оновлень системи", success=True)
    run(["sudo", "systemctl", "reboot"], timeout=10)
    return True


def main():
    db.init_db()
    if not config.AUTO_UPDATE_ENABLED:
        logger.info("Автооновлення вимкнено (STARLINK_AUTO_UPDATE=0)")
        return

    logger.info("Запуск циклу перевірки оновлень")
    apt_update_upgrade()
    update_python_deps()
    git_pull_project()
    maybe_reboot_system()
    logger.info("Цикл оновлень завершено")


if __name__ == "__main__":
    main()
