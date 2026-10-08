"""Спільні fixtures. Кожен тест отримує ІЗОЛЬОВАНУ тимчасову SQLite БД (tmp_path, прибирається автоматично,
реальну БД не чіпає): config.DB_PATH = тимчасовий шлях + db.init_db() — той самий патерн, що й у живому
тестуванні.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# Робочі шляхи Pi: тести НІКОЛИ не мають їх торкатись (на Pi /etc/starlink-monitor належить користувачу
# сервісів; раніше 11 тестів писали в справжній env, бо фікстура client ізолювала БД, але не env).
REAL_PATHS = ("/etc/starlink-monitor", "/var/lib/starlink-monitor")


_HYPOTHESIS_HOME = None


def pytest_configure(config):
    """Hypothesis пише кеш (constants, unicode_data) у .hypothesis/ поточного каталогу, тобто в теку проєкту: плагін -
    ще під час збору тестів, рушій - під час виконання. Змінна середовища перенаправляє обидві фази в тимчасову теку."""
    global _HYPOTHESIS_HOME
    _HYPOTHESIS_HOME = tempfile.TemporaryDirectory(prefix="hypothesis-")
    os.environ["HYPOTHESIS_STORAGE_DIRECTORY"] = _HYPOTHESIS_HOME.name


def pytest_unconfigure(config):
    if _HYPOTHESIS_HOME is not None:
        _HYPOTHESIS_HOME.cleanup()


@pytest.fixture(autouse=True)
def _isolate_real_paths(tmp_path, monkeypatch):
    """Для КОЖНОГО тесту: файл налаштувань, тека бекапів і БД за замовчуванням — у тимчасовій теці. БД
    навмисно НЕ ініціалізована: тесту з БД потрібна фікстура db_path, а звернення без неї падає на
    відсутніх таблицях замість тихого читання стану іншого тесту.
    """
    from app import config, config_editor
    monkeypatch.setattr(config_editor, "ENV_FILE_PATH", str(tmp_path / "env"))
    monkeypatch.setattr(config, "AUTO_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "not-initialized.db"))


@pytest.fixture
def db_path(tmp_path):
    """Ізольована тимчасова БД. Імпорт усередині fixture (не на рівні модуля): config.DB_PATH має бути
    встановлено ДО першого db.get_conn(), інакше порядок імпорту тестових файлів міг би закешувати
    старий шлях.
    """
    from app import config, db
    config.DB_PATH = str(tmp_path / "test.db")
    db.init_db()
    return config.DB_PATH


class NotificationSink:
    """Приймач сповіщень для тестів СЕРВІСНИХ функцій (app/services.py):
    інтерфейс той самий, що в watchdog-фікстури (`_notify`, `sent`,
    `last_known_dish_id`), але без створення Watchdog."""

    def __init__(self):
        self.sent = []
        self.last_known_dish_id = None

    def _notify(self, text):
        self.sent.append(text)


@pytest.fixture
def sink(db_path):
    return NotificationSink()


@pytest.fixture
def watchdog(db_path):
    """Watchdog з mock-ованим _notify() - зібрані повідомлення в
    список .sent, замість реальної відправки в Telegram. Той самий
    патерн, що застосовувався в усіх ad-hoc живих тестах monitor.py
    протягом розробки (wd._notify = lambda t: sent.append(t))."""
    from app.monitor import Watchdog
    wd = Watchdog()
    wd.sent = []
    wd._notify = lambda text: wd.sent.append(text)
    # Передумова наявних тестів: роутер Starlink досяжний (збій dish = проблема тарілки -> звичайний
    # watchdog). Інакше poll_once() з offline-статусом робив би РЕАЛЬНЕ TCP-з'єднання з 192.168.1.1 (2
    # с, залежить від мережі). Тести стану "Starlink вимкнено" перевизначають це явно.
    wd.client.router_reachable = lambda timeout=2.0: True
    return wd


_TELEGRAM_TAGS = ("b", "i", "u", "s", "code", "pre")


def assert_valid_telegram_html(text):
    """Telegram (parse_mode=HTML) відхиляє ВСЕ повідомлення з непідтримуваним тегом чи "голими" `<`, `>`,
    `&`. Перевіряємо самі: дозволені теги збалансовані, а після їх вилучення (і сутностей &amp; &lt;
    &gt;) жодного `<`, `>`, `&` не лишається.
    """
    import re
    for tag in _TELEGRAM_TAGS:
        assert len(re.findall(rf"<{tag}>", text)) == len(re.findall(rf"</{tag}>", text)), (tag, text)
    stripped = re.sub(rf"</?({'|'.join(_TELEGRAM_TAGS)})>", "", text)
    stripped = re.sub(r"&(amp|lt|gt);", "", stripped)
    assert not re.search(r"[<>&]", stripped), stripped
