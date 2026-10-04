"""Атомарний запис текстового файлу: тимчасовий файл у тому ж каталозі -> fsync -> os.replace.

Раніше `open(path, "w")` спершу ОБНУЛЯВ файл і лише потім писав: обрив живлення чи збій між
цими двома кроками лишав порожній або обірваний /etc/starlink-monitor/env (станція мовчки
втрачала всі перевизначення параметрів) чи обірваний backup-<epoch>.json, який потім вважався б
"найновішим". Після os.replace читач бачить або старий файл, або новий - ніколи проміжний.
"""
import contextlib
import os
import stat
import tempfile
from typing import Optional


def atomic_write_text(path: str, text: str, mode: Optional[int] = None) -> None:
    """Записує text у path атомарно. mode: права нового файлу; за замовчуванням - права вже
    існуючого файлу, а якщо його немає - 0o600 (env і backup містять токен Telegram-бота)."""
    directory = os.path.dirname(os.path.abspath(path))
    if mode is None:
        mode = stat.S_IMODE(os.stat(path).st_mode) if os.path.exists(path) else 0o600
    fd, tmp = tempfile.mkstemp(prefix=".atomic-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    # fsync каталогу: щоб саме перейменування пережило втрату живлення
    with contextlib.suppress(OSError):
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
