"""Атомарний запис (app/atomic_io.py) і його застосування: env-файл і автобекап. `open(path, "w")` спершу
обнуляв файл, тож обрив між обнуленням і записом лишав порожній чи обірваний /etc/starlink-monitor/env
(мовчазна втрата всіх перевизначень).
"""
import os
import stat

import pytest

from app import atomic_io, config_editor


def _leftovers(directory):
    return [n for n in os.listdir(directory) if n.startswith(".atomic-")]


def test_atomic_write_creates_a_new_file_with_private_permissions(tmp_path):
    target = tmp_path / "env"
    atomic_io.atomic_write_text(str(target), "A=1\n")
    assert target.read_text(encoding="utf-8") == "A=1\n"
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600           # у файлі може бути токен Telegram
    assert _leftovers(tmp_path) == []


def test_atomic_write_preserves_the_permissions_of_an_existing_file(tmp_path):
    target = tmp_path / "env"
    target.write_text("old\n", encoding="utf-8")
    os.chmod(target, 0o640)
    atomic_io.atomic_write_text(str(target), "new\n")
    assert target.read_text(encoding="utf-8") == "new\n" and stat.S_IMODE(os.stat(target).st_mode) == 0o640


def test_atomic_write_honours_an_explicit_mode(tmp_path):
    target = tmp_path / "b.json"
    atomic_io.atomic_write_text(str(target), "{}", mode=0o600)
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600


def test_atomic_write_writes_utf8(tmp_path):
    target = tmp_path / "f"
    atomic_io.atomic_write_text(str(target), "ключ=значення ❄️\n")
    assert target.read_text(encoding="utf-8") == "ключ=значення ❄️\n"


@pytest.mark.parametrize("failing", ["fsync", "replace", "chmod"])
def test_failure_before_the_swap_leaves_the_original_intact_and_no_temp_file(tmp_path, monkeypatch, failing):
    """Збій на будь-якому кроці ДО перейменування: старий файл неушкоджений, тимчасового сміття немає."""
    target = tmp_path / "env"
    target.write_text("STARLINK_POLL_INTERVAL=7\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("диск зник")
    monkeypatch.setattr(os, failing, boom)
    with pytest.raises(OSError):
        atomic_io.atomic_write_text(str(target), "ЗОВСІМ ІНШЕ\n")
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "STARLINK_POLL_INTERVAL=7\n"
    assert _leftovers(tmp_path) == []


def test_crash_in_the_middle_of_the_write_leaves_the_original_intact(tmp_path, monkeypatch):
    """Головна властивість: обрив ПІД ЧАС запису не чіпає працюючий файл (раніше він уже був обнулений)."""
    target = tmp_path / "env"
    target.write_text("A=1\nB=2\n", encoding="utf-8")
    real_fdopen = os.fdopen

    class CrashingFile:
        def __init__(self, f):
            self._f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()

        def write(self, text):
            self._f.write(text[:3])
            raise OSError("живлення зникло посеред запису")
    monkeypatch.setattr(os, "fdopen", lambda fd, *a, **k: CrashingFile(real_fdopen(fd, *a, **k)))
    with pytest.raises(OSError):
        atomic_io.atomic_write_text(str(target), "A=100\nB=200\nC=300\n")
    monkeypatch.undo()
    assert target.read_text(encoding="utf-8") == "A=1\nB=2\n" and _leftovers(tmp_path) == []


def test_save_values_does_not_touch_the_env_file_when_the_write_fails(tmp_path, monkeypatch, db_path):
    env = tmp_path / "env"
    env.write_text("# мій коментар\nSTARLINK_POLL_INTERVAL=7\nCUSTOM=keep\n", encoding="utf-8")
    monkeypatch.setattr(config_editor, "ENV_FILE_PATH", str(env))
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only file system")))
    ok, message = config_editor.save_values({"STARLINK_HISTORY_DAYS": "21"})
    monkeypatch.undo()
    assert ok is False and message
    assert env.read_text(encoding="utf-8") == "# мій коментар\nSTARLINK_POLL_INTERVAL=7\nCUSTOM=keep\n"
    assert _leftovers(tmp_path) == []


def test_save_values_keeps_unknown_lines_comments_and_permissions(tmp_path, monkeypatch, db_path):
    env = tmp_path / "env"
    env.write_text("# мій коментар\nSTARLINK_POLL_INTERVAL=7\nCUSTOM=keep\n", encoding="utf-8")
    os.chmod(env, 0o600)
    monkeypatch.setattr(config_editor, "ENV_FILE_PATH", str(env))
    ok, _ = config_editor.save_values({"STARLINK_HISTORY_DAYS": "21", "STARLINK_POLL_INTERVAL": ""})
    assert ok
    assert env.read_text(encoding="utf-8") == "# мій коментар\nCUSTOM=keep\nSTARLINK_HISTORY_DAYS=21\n"
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600


def test_auto_backup_is_written_atomically_with_private_permissions(db_path, monkeypatch, tmp_path):
    from app import config, services
    monkeypatch.setattr(config, "AUTO_BACKUP_DIR", str(tmp_path / "b"))
    services.perform_auto_backup()
    files = os.listdir(tmp_path / "b")
    assert len(files) == 1 and files[0].startswith("backup-") and _leftovers(tmp_path / "b") == []
    assert stat.S_IMODE(os.stat(tmp_path / "b" / files[0]).st_mode) == 0o600


def test_failed_auto_backup_write_leaves_no_partial_file(db_path, monkeypatch, tmp_path):
    """Обірваний backup-<epoch>.json вважався б "найновішим" при ротації й відправці."""
    from app import config, services
    monkeypatch.setattr(config, "AUTO_BACKUP_DIR", str(tmp_path / "b"))
    monkeypatch.setattr(os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("збій")))
    with pytest.raises(OSError):
        services.perform_auto_backup()
    monkeypatch.undo()
    assert os.listdir(tmp_path / "b") == []


# ---- прибирання тимчасових файлів, що пережили SIGKILL ----

def _make_temp(directory, name, age_sec):
    import time
    path = directory / name
    path.write_text("залишок", encoding="utf-8")
    old = time.time() - age_sec
    os.utime(path, (old, old))
    return path


def test_stale_temp_files_from_killed_writers_are_removed_on_the_next_write(tmp_path):
    """60 SIGKILL у випадковий момент запису лишили до 22 файлів .atomic-*.tmp."""
    stale = [_make_temp(tmp_path, f".atomic-dead{i}.tmp", 86400) for i in range(3)]
    atomic_io.atomic_write_text(str(tmp_path / "env"), "A=1\n")
    assert not any(p.exists() for p in stale)


def test_fresh_temp_files_of_a_concurrent_writer_are_kept(tmp_path):
    fresh = _make_temp(tmp_path, ".atomic-concurrent.tmp", 5)
    atomic_io.atomic_write_text(str(tmp_path / "env"), "A=1\n")
    assert fresh.exists()


def test_cleanup_touches_only_atomic_temp_files(tmp_path):
    keep = [_make_temp(tmp_path, name, 86400) for name in (".atomic-notes.txt", ".atomic-x.tmp.bak", "other.tmp", ".hidden", "env.old")]
    atomic_io.atomic_write_text(str(tmp_path / "env"), "A=1\n")
    assert all(p.exists() for p in keep)


def test_failure_to_remove_a_stale_file_does_not_fail_the_write(tmp_path, monkeypatch):
    _make_temp(tmp_path, ".atomic-dead.tmp", 86400)
    real_unlink = os.unlink
    monkeypatch.setattr(os, "unlink", lambda p, *a, **k: (_ for _ in ()).throw(PermissionError("не мій")) if ".atomic-dead" in str(p) else real_unlink(p, *a, **k))
    atomic_io.atomic_write_text(str(tmp_path / "env"), "A=1\n")
    assert (tmp_path / "env").read_text(encoding="utf-8") == "A=1\n"
