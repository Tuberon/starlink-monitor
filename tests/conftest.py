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
    return wd
