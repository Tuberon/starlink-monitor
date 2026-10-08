"""Залежності: піни, constraints.txt, логіка install.sh."""
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _pins(path):
    """{канонічне ім'я: версія} для рядків name==version (коментарі й порожні - пропуск)."""
    pins = {}
    for raw in (ROOT / path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)==([\w.]+)\s*(;.*)?$", line)
        assert match, f"{path}: не точний пін name==version: {raw!r}"
        pins[re.sub(r"[-_.]+", "-", match.group(1)).lower()] = match.group(2)
    return pins


def test_requirements_and_constraints_are_exactly_pinned():
    assert len(_pins("requirements.txt")) >= 8
    assert len(_pins("constraints.txt")) >= 10


def test_constraints_hold_only_transitive_packages_never_direct_or_hardware():
    """Constraints лише ОБМЕЖУЮТЬ версії; пакет у обох файлах - дубль. Апаратні
    adafruit-* СВІДОМО не закріплюються (pip колись уже оновив adafruit-blinka
    8.x -> 9.2.0 і зламав дисплей; поза Pi їх не перевірити)."""
    direct, constraints = set(_pins("requirements.txt")), set(_pins("constraints.txt"))
    assert direct & constraints == set()
    assert not any(name.startswith("adafruit") for name in constraints)


def test_security_relevant_transitive_versions_are_at_or_above_fixed_releases():
    """urllib3 2.6.3 (PYSEC-2026-141/142) і idna 3.11 (PYSEC-2026-215) мали
    advisory; виправлено в urllib3 2.7.0 і idna 3.15."""
    def as_tuple(version):
        return tuple(int(p) for p in version.split("."))
    pins = _pins("constraints.txt")
    assert as_tuple(pins["urllib3"]) >= (2, 7, 0)
    assert as_tuple(pins["idna"]) >= (3, 15)


def test_every_pip_install_in_install_sh_uses_the_constraints_file():
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    installs = re.findall(r'^[^#\n]*pip" install -r [^\n]*$', script, re.M)
    assert len(installs) == 2                                    # перше встановлення і оновлення
    assert all('-c "$PROJECT_DIR/constraints.txt"' in line for line in installs), installs


# ---- логіка REQ_CHANGED з install.sh: виконуємо СПРАВЖНІЙ фрагмент скрипту ----

def _req_changed_fragment():
    """Рівно блок обчислення REQ_CHANGED: від `REQ_CHANGED=1` до першого `fi`
    БЕЗ відступу (внутрішні мають відступ). Ніколи не виконуємо решту
    install.sh - там apt-get, usermod, systemctl."""
    script = (ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    start = script.index("REQ_CHANGED=1\n")
    end = script.index("\nfi\n", start) + 4
    fragment = script[start:end]
    for forbidden in ("apt-get", "usermod", "systemctl", "rm -rf", "pip"):
        assert forbidden not in fragment, f"фрагмент захопив зайве ({forbidden}) - не виконуємо"
    return fragment


def _run_req_changed(tmp_path, src_files, installed_files, mode="update"):
    src, installed = tmp_path / "src", tmp_path / "installed"
    src.mkdir(), installed.mkdir()
    for name, text in src_files.items():
        (src / name).write_text(text, encoding="utf-8")
    for name, text in installed_files.items():
        (installed / name).write_text(text, encoding="utf-8")
    code = f'set -euo pipefail\nSRC_DIR="{src}"\nPROJECT_DIR="{installed}"\nMODE="{mode}"\n{_req_changed_fragment()}\necho "$REQ_CHANGED"'
    out = subprocess.run(["bash", "-c", code], capture_output=True, encoding="utf-8", errors="replace", timeout=20)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.mark.parametrize("name,src,installed,expected", [
    ("те саме", {"requirements.txt": "a==1", "constraints.txt": "b==2"}, {"requirements.txt": "a==1", "constraints.txt": "b==2"}, "0"),
    ("перше оновлення: constraints ще немає на Pi", {"requirements.txt": "a==1", "constraints.txt": "b==2"}, {"requirements.txt": "a==1"}, "1"),
    ("змінився лише constraints", {"requirements.txt": "a==1", "constraints.txt": "b==3"}, {"requirements.txt": "a==1", "constraints.txt": "b==2"}, "1"),
    ("змінився requirements", {"requirements.txt": "a==2", "constraints.txt": "b==2"}, {"requirements.txt": "a==1", "constraints.txt": "b==2"}, "1"),
])
def test_req_changed_logic_in_install_sh(tmp_path, name, src, installed, expected):
    assert _run_req_changed(tmp_path, src, installed) == expected, name


def test_req_changed_is_always_1_on_fresh_install(tmp_path):
    same = {"requirements.txt": "a==1", "constraints.txt": "b==2"}
    assert _run_req_changed(tmp_path, same, same, mode="install") == "1"
