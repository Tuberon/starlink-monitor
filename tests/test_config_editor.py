"""
Тести для app/config_editor.py - модуль записує `/etc/starlink-monitor/env`,
від якого залежать УСІ параметри проєкту, тому некоректний запис зламав би
конфігурацію цілком (і, на відміну від помилки в одному ендпоінті, це
проявилось би лише після рестарту сервісів - складно діагностувати).

Ключові ризики, які перевіряються: (1) валідація типів, (2) атомарність
(жодного часткового запису при помилці валідації), (3) збереження
довільного вмісту файлу (коментарі, невідомі змінні - користувач міг
додати їх вручну), (4) видалення перевизначення порожнім значенням.
"""
import pytest

from app import config_editor


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    """Ізольований тимчасовий env-файл замість реального
    /etc/starlink-monitor/env (той самий патерн ізоляції, що db_path
    fixture для БД - тест НЕ має торкатись реальної системної
    конфігурації)."""
    path = tmp_path / "env"
    monkeypatch.setattr(config_editor, "ENV_FILE_PATH", str(path))
    return path


# ---- Валідація типів ----

@pytest.mark.parametrize("type_name,value,expected_ok", [
    ("int", "42", True),
    ("int", "не_число", False),
    ("int", "3.14", False),          # float НЕ є валідним int
    ("float", "0.05", True),
    ("float", "42", True),           # int валідний як float
    ("float", "abc", False),
    ("bool", "0", True),
    ("bool", "1", True),
    ("bool", "2", False),            # лише 0/1, не будь-яке число
    ("bool", "true", False),         # текстове "true" НЕ приймається
    ("str", "будь-що", True),
])
def test_validate_value_type_checking(type_name, value, expected_ok):
    param = {"type": type_name, "label": "тест"}
    ok, _ = config_editor._validate_value(param, value)
    assert ok is expected_ok


def test_validate_empty_value_means_remove_override():
    """Порожнє значення = прибрати перевизначення (лишити default із
    config.py), не помилка валідації."""
    ok, result = config_editor._validate_value({"type": "int", "label": "т"}, "")
    assert ok is True
    assert result is None


def test_validate_strips_whitespace():
    ok, result = config_editor._validate_value({"type": "int", "label": "т"}, "  42  ")
    assert ok is True
    assert result == "42"


# ---- Запис у файл ----

def test_save_creates_file_with_valid_value(env_file):
    ok, msg = config_editor.save_values({"STARLINK_POLL_INTERVAL": "20"})
    assert ok is True
    assert "STARLINK_POLL_INTERVAL=20" in env_file.read_text()


def test_save_updates_existing_value_without_duplicating(env_file):
    """Повторне збереження МАЄ замінити рядок, не додати другий -
    інакше файл ріс би з кожним збереженням, а який рядок виграє
    залежало б від порядку читання."""
    env_file.write_text("STARLINK_POLL_INTERVAL=10\n")
    config_editor.save_values({"STARLINK_POLL_INTERVAL": "99"})

    content = env_file.read_text()
    assert content.count("STARLINK_POLL_INTERVAL") == 1
    assert "STARLINK_POLL_INTERVAL=99" in content


def test_save_preserves_comments_and_unknown_variables(env_file):
    """Користувач міг вручну додати коментарі чи власні змінні
    (напр. PATH-налаштування для сервісу) - вони МАЮТЬ зберегтись,
    веб-редактор не володіє файлом одноосібно."""
    env_file.write_text(
        "# мій коментар\n"
        "MY_CUSTOM_VAR=значення\n"
        "STARLINK_POLL_INTERVAL=10\n"
    )
    config_editor.save_values({"STARLINK_POLL_INTERVAL": "20"})

    content = env_file.read_text()
    assert "# мій коментар" in content
    assert "MY_CUSTOM_VAR=значення" in content
    assert "STARLINK_POLL_INTERVAL=20" in content


def test_save_empty_value_removes_line_from_file(env_file):
    """Порожнє значення реально ВИДАЛЯЄ рядок (повертає default із
    config.py), не записує порожнє `KEY=`."""
    env_file.write_text("STARLINK_POLL_INTERVAL=99\nSTARLINK_WEBUI_PORT=8080\n")
    config_editor.save_values({"STARLINK_POLL_INTERVAL": ""})

    content = env_file.read_text()
    assert "STARLINK_POLL_INTERVAL" not in content
    assert "STARLINK_WEBUI_PORT=8080" in content  # інші не зачеплені


def test_save_ignores_unknown_keys(env_file):
    """Ключі поза EDITABLE_PARAMS ігноруються - захист від довільного
    запису в системний env-файл через API."""
    ok, _ = config_editor.save_values({"НЕВІДОМИЙ_КЛЮЧ": "значення"})
    assert ok is True
    assert not env_file.exists() or "НЕВІДОМИЙ_КЛЮЧ" not in env_file.read_text()


# ---- Атомарність: помилка валідації НЕ має писати нічого ----

def test_save_validation_error_writes_nothing(env_file):
    """НАЙВАЖЛИВІШЕ: якщо ХОЧ ОДИН параметр невалідний, файл НЕ
    змінюється взагалі - інакше частина значень записалась би,
    а частина ні, лишивши конфігурацію в незрозумілому
    напів-застосованому стані."""
    env_file.write_text("STARLINK_POLL_INTERVAL=10\n")
    original = env_file.read_text()

    ok, msg = config_editor.save_values({
        "STARLINK_POLL_INTERVAL": "20",        # валідний
        "STARLINK_WEBUI_PORT": "не_число",     # НЕвалідний
    })

    assert ok is False
    assert "Порт веб-інтерфейсу" in msg  # label невалідного параметра
    assert env_file.read_text() == original, "файл НЕ мав змінитись при помилці валідації"


def test_save_reports_all_validation_errors_at_once(env_file):
    ok, msg = config_editor.save_values({
        "STARLINK_POLL_INTERVAL": "abc",
        "STARLINK_WEBUI_PORT": "xyz",
    })
    assert ok is False
    assert "Інтервал опитування dish" in msg
    assert "Порт веб-інтерфейсу" in msg


# ---- Читання поточних значень ----

def test_read_current_values_marks_overridden(env_file):
    env_file.write_text("STARLINK_POLL_INTERVAL=99\n")
    values = config_editor.read_current_values()

    poll = next(v for v in values if v["key"] == "STARLINK_POLL_INTERVAL")
    assert poll["current"] == "99"
    assert poll["overridden"] is True

    other = next(v for v in values if v["key"] == "STARLINK_WEBUI_PORT")
    assert other["overridden"] is False
    assert other["current"] == ""


def test_read_current_values_without_file_returns_all_params(env_file):
    """Файл ще не існує (свіжа установка) - НЕ падає, повертає всі
    параметри як не-перевизначені."""
    assert not env_file.exists()
    values = config_editor.read_current_values()
    assert len(values) == len(config_editor.EDITABLE_PARAMS)
    assert all(v["overridden"] is False for v in values)


def test_read_current_values_ignores_malformed_lines(env_file):
    """Рядки без '=' чи коментарі не мають ламати парсинг."""
    env_file.write_text(
        "# коментар\n"
        "\n"
        "рядок_без_знаку_рівності\n"
        "STARLINK_POLL_INTERVAL=42\n"
    )
    values = config_editor.read_current_values()
    poll = next(v for v in values if v["key"] == "STARLINK_POLL_INTERVAL")
    assert poll["current"] == "42"


# ---- Узгодженість EDITABLE_PARAMS із config.py ----

def test_all_editable_params_are_read_by_config():
    """Кожен env-ключ UI МАЄ реально читатись у config.py - інакше
    користувач редагував би значення, яке ніде не використовується
    (мовчазна, заплутана поведінка: 'змінив, зберіг, рестартував -
    нічого не сталось').

    Перевіряє саме ENV-КЛЮЧ у тексті config.py, не назву Python-
    змінної: вони навмисно різні (STARLINK_POLL_INTERVAL читається в
    POLL_INTERVAL_SEC - суфікс одиниці виміру для читабельності коду),
    тому hasattr()-перевірка тут була б хибною."""
    import os
    config_src = open(os.path.join(os.path.dirname(config_editor.__file__), "config.py")).read()
    missing = [
        p["key"] for p in config_editor.EDITABLE_PARAMS
        if f'"{p["key"]}"' not in config_src
    ]
    assert missing == [], f"env-ключі UI, які config.py ніде не читає: {missing}"


def test_no_orphan_env_keys_in_config():
    """Зворотна перевірка: кожен STARLINK_*-ключ, який config.py
    читає, МАЄ бути редагованим через UI - інакше параметр існує,
    але користувач не може його змінити без ручного редагування
    env-файлу (та сама тристороння звірка, що робиться вручну
    протягом усієї розробки, тепер персистентно).

    NOT_IN_UI - НАВМИСНІ винятки: інфраструктурні параметри, які
    небезпечно міняти через сам веб-інтерфейс, що на них працює
    (помилкове значення відрізало б доступ до дашборду, після чого
    виправити можна лише через SSH)."""
    import os
    import re
    NOT_IN_UI = {
        "STARLINK_DB_PATH",     # шлях до БД: зміна "на льоту" лишила б webapp і monitor на РІЗНИХ базах
        "STARLINK_WEBUI_HOST",  # bind-адреса: помилка (напр. 127.0.0.1) відрізала б доступ із мережі
        "STARLINK_AUTO_BACKUP_DIR",  # обчислюваний дефолт (поруч з DB_PATH) - не вписується в простий {"default": "..."} UI-формат
    }
    config_src = open(os.path.join(os.path.dirname(config_editor.__file__), "config.py")).read()
    config_keys = set(re.findall(r'os\.environ\.get\("(STARLINK_[A-Z0-9_]+)"', config_src))
    ui_keys = {p["key"] for p in config_editor.EDITABLE_PARAMS}
    orphans = config_keys - ui_keys - NOT_IN_UI
    assert orphans == set(), f"параметри config.py, відсутні в UI-редакторі: {orphans}"
