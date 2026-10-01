"""
Спільні fixtures для pytest. Кожен тест отримує ІЗОЛЬОВАНУ тимчасову
SQLite БД (через pytest'ів вбудований tmp_path - автоматично
прибирається після тесту, не впливає на реальну БД і на інші тести).
Той самий патерн, що використовувався для живого тестування протягом
усієї розробки (config.DB_PATH = тимчасовий шлях, db.init_db()),
тепер персистентний, не одноразовий ad-hoc скрипт.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

# Робочі шляхи Pi. Тести НІКОЛИ не мають їх торкатись: на Pi тека
# /etc/starlink-monitor належить користувачу сервісів - запуск тестів там
# змінив би робочі налаштування (так і було: 11 тестів писали в справжній
# /etc/starlink-monitor/env, бо фікстура client ізолювала БД, але не env).
REAL_PATHS = ("/etc/starlink-monitor", "/var/lib/starlink-monitor")


@pytest.fixture(autouse=True)
def _isolate_real_paths(tmp_path, monkeypatch):
    """Для КОЖНОГО тесту: файл налаштувань, тека бекапів і БД за
    замовчуванням - у тимчасовій теці. БД тут навмисно НЕ ініціалізована:
    тест, якому потрібна БД, бере фікстуру db_path (вона перевизначає
    шлях), а тест, що звертається до БД без неї, падає на відсутніх
    таблицях замість тихо читати стан іншого тесту."""
    from app import config, config_editor
    monkeypatch.setattr(config_editor, "ENV_FILE_PATH", str(tmp_path / "env"))
    monkeypatch.setattr(config, "AUTO_BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "not-initialized.db"))


@pytest.fixture
def db_path(tmp_path):
    """Ізольована тимчасова БД. Явний import всередині fixture (не на
    рівні модуля) - config.DB_PATH має бути встановлено ДО першого
    db.get_conn() виклику, а порядок імпорту тестових файлів інакше
    міг би призвести до того, що якийсь модуль закешував старий шлях."""
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
    # Явна передумова наявних тестів: роутер Starlink досяжний (збій dish
    # = проблема тарілки -> звичайний watchdog). Без цього poll_once()
    # з offline-статусом робив би РЕАЛЬНЕ TCP-з'єднання з 192.168.1.1
    # (2 с таймауту, результат залежить від мережі середовища). Тести
    # стану "Starlink вимкнено" перевизначають це явно.
    wd.client.router_reachable = lambda timeout=2.0: True
    return wd


_TELEGRAM_TAGS = ("b", "i", "u", "s", "code", "pre")


def assert_valid_telegram_html(text):
    """Telegram (parse_mode=HTML) відхиляє ВСЕ повідомлення, якщо в ньому є
    непідтримуваний тег або "голі" `<`, `>`, `&`. Перевіряємо це самі:
    дозволені теги мають бути збалансовані, а після їх вилучення (і
    сутностей &amp; &lt; &gt;) жодного `<`, `>`, `&` не лишається."""
    import re
    for tag in _TELEGRAM_TAGS:
        assert len(re.findall(rf"<{tag}>", text)) == len(re.findall(rf"</{tag}>", text)), (tag, text)
    stripped = re.sub(rf"</?({'|'.join(_TELEGRAM_TAGS)})>", "", text)
    stripped = re.sub(r"&(amp|lt|gt);", "", stripped)
    assert not re.search(r"[<>&]", stripped), stripped
