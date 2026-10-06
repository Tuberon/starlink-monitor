"""Тести app/config_editor.py: модуль пише `/etc/starlink-monitor/env`, від якого залежать УСІ параметри, а
некоректний запис проявився б лише після рестарту сервісів. Перевіряється: (1) валідація типів, (2)
атомарність (жодного часткового запису при помилці), (3) збереження довільного вмісту файлу (коментарі,
невідомі змінні), (4) видалення перевизначення порожнім значенням.
"""
from pathlib import Path

import pytest

from app import config_editor, db


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
def test_validate_value_type_checking(type_name, value, expected_ok, db_path):
    # db_path: невалідні значення доходять до error-гілки, що викликає
    # i18n.t() -> db.get_setting(); без ізоляції тест читав БД, залишену
    # іншим тестом (sqlite3.OperationalError: no such table: settings).
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

def test_save_validation_error_writes_nothing(env_file, db_path):
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


def test_save_reports_all_validation_errors_at_once(env_file, db_path):
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
    """Кожен env-ключ UI МАЄ читатись у config.py, інакше користувач змінював би значення, яке ніде не діє.
    Перевіряється ENV-КЛЮЧ у тексті config.py, а не назва Python-змінної (вони навмисно різні:
    STARLINK_POLL_INTERVAL читається в POLL_INTERVAL_SEC), тож hasattr() був би хибним.
    """
    import os
    config_src = Path(os.path.dirname(config_editor.__file__), "config.py").read_text(encoding="utf-8")
    missing = [
        p["key"] for p in config_editor.EDITABLE_PARAMS
        if f'"{p["key"]}"' not in config_src
    ]
    assert missing == [], f"env-ключі UI, які config.py ніде не читає: {missing}"


def test_no_orphan_env_keys_in_config():
    """Зворотна перевірка: кожен STARLINK_*-ключ config.py МАЄ бути редагованим у UI (тристороння звірка,
    що раніше робилась вручну). NOT_IN_UI — навмисні винятки: інфраструктурні параметри, небезпечні для
    зміни через сам веб-інтерфейс (помилкове значення відрізало б дашборд, виправити можна лише через
    SSH).
    """
    import os
    import re
    NOT_IN_UI = {
        "STARLINK_DB_PATH",     # шлях до БД: зміна "на льоту" лишила б webapp і monitor на РІЗНИХ базах
        "STARLINK_WEBUI_HOST",  # bind-адреса: помилка (напр. 127.0.0.1) відрізала б доступ із мережі
        "STARLINK_AUTO_BACKUP_DIR",  # обчислюваний дефолт (поруч з DB_PATH) - не вписується в простий {"default": "..."} UI-формат
    }
    config_src = Path(os.path.dirname(config_editor.__file__), "config.py").read_text(encoding="utf-8")
    config_keys = set(re.findall(r'os\.environ\.get\("(STARLINK_[A-Z0-9_]+)"', config_src))
    ui_keys = {p["key"] for p in config_editor.EDITABLE_PARAMS}
    orphans = config_keys - ui_keys - NOT_IN_UI
    assert orphans == set(), f"параметри config.py, відсутні в UI-редакторі: {orphans}"


def test_every_param_has_valid_category():
    """Кожен параметр у EDITABLE_PARAMS МАЄ category, і кожна category
    МАЄ підпис у CATEGORY_LABELS - інакше /settings показав би "інше"
    чи сирий slug замість людського підпису для якоїсь групи."""
    for p in config_editor.EDITABLE_PARAMS:
        assert "category" in p, f"{p['key']} не має category"
        assert p["category"] in config_editor.CATEGORY_LABELS, (
            f"{p['key']}: category={p['category']!r} відсутня в CATEGORY_LABELS"
        )


def test_no_orphan_category_labels():
    """Зворотна перевірка: кожен запис CATEGORY_LABELS реально
    використовується хоча б одним параметром - інакше застарілий
    підпис категорії, з якої вже видалили всі параметри."""
    used = {p["category"] for p in config_editor.EDITABLE_PARAMS}
    orphan_labels = set(config_editor.CATEGORY_LABELS) - used
    assert orphan_labels == set(), f"category_labels без жодного параметра: {orphan_labels}"


def test_all_config_env_vars_are_in_settings_except_documented_exceptions():
    """Кожна STARLINK_-змінна, яку читає config.py, МАЄ бути редагованою через /settings (EDITABLE_PARAMS),
    інакше її можна змінити лише вручну в env. Єдині 2 навмисні винятки (з коментарями в config.py):
    DB_PATH (зміна шляху без міграції даних) і WEBUI_HOST (ризик self-lockout). Якщо тест падає —
    параметр забутий у /settings.
    """
    import re
    config_source = Path("app/config.py").read_text(encoding="utf-8")
    env_vars_read = set(re.findall(r'os\.environ\.get\("(STARLINK_[A-Z_]+)"', config_source))

    INTENTIONALLY_EXCLUDED = {"STARLINK_DB_PATH", "STARLINK_WEBUI_HOST"}
    editable_keys = {p["key"] for p in config_editor.EDITABLE_PARAMS}

    missing = env_vars_read - editable_keys - INTENTIONALLY_EXCLUDED
    assert missing == set(), f"Параметри відсутні в /settings без документованої причини: {missing}"


def test_intentionally_excluded_settings_are_still_read_by_config():
    """Контрольний тест: DB_PATH/WEBUI_HOST реально виключені з
    /settings НАВМИСНО (а не тому, що їх взагалі видалили) - вони й
    далі реально читаються config.py, лише недоступні для UI-
    редагування."""
    config_source = Path("app/config.py").read_text(encoding="utf-8")
    assert 'os.environ.get("STARLINK_DB_PATH"' in config_source
    assert 'os.environ.get("STARLINK_WEBUI_HOST"' in config_source
    editable_keys = {p["key"] for p in config_editor.EDITABLE_PARAMS}
    assert "STARLINK_DB_PATH" not in editable_keys
    assert "STARLINK_WEBUI_HOST" not in editable_keys


# ---- керуючі символи у значенні не потрапляють у файл env ----

@pytest.mark.parametrize("value", [
    "192.168.100.1:9200\nSTARLINK_WEBUI_PORT=1",   # вставка з буфера: другий рядок
    "192.168.100.1:9200\rX=1",                     # \r з буфера Windows
    "192.168.100.1\x009200",                        # NUL ламає EnvironmentFile
    "a\tb",
])
def test_control_characters_rejected_and_file_untouched(env_file, db_path, value):
    env_file.write_text("STARLINK_POLL_INTERVAL=10\n")
    original = env_file.read_text()
    ok, msg = config_editor.save_values({"STARLINK_DISH_ADDR": value})
    assert ok is False
    assert "керуючі символи" in msg
    assert env_file.read_text() == original


def test_trailing_newline_from_paste_still_accepted(env_file, db_path):
    """Перенесення лише по краях (типова вставка) - обрізається strip(),
    не вважається помилкою."""
    ok, _ = config_editor.save_values({"STARLINK_DISH_ADDR": "192.168.100.1:9200\n"})
    assert ok is True
    assert "STARLINK_DISH_ADDR=192.168.100.1:9200\n" in env_file.read_text()


def test_no_exec_or_eval_in_app_code():
    """config.py колись виконував як код будь-який вміст /etc/starlink-monitor/config.local.py (0
    використань) у КОЖНОМУ процесі, в обхід валідації /settings, а теку може писати користувач сервісів.
    Прибрано; тест не дає exec()/eval() повернутись у застосунок.
    """
    import ast
    import glob
    import os
    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")
    found = []
    for path in glob.glob(os.path.join(app_dir, "*.py")):
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("exec", "eval"):
                found.append(f"{os.path.basename(path)}:{node.lineno} {node.func.id}()")
    assert found == [], found


def test_ruff_check_clean():
    """`ruff check .` (правила в ruff.toml) - частина звичайного прогону
    тестів. Пропускається, якщо ruff не встановлено (requirements-dev.txt)."""
    import os
    import shutil
    import subprocess
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff не встановлено (pip install -r requirements-dev.txt)")
    root = os.path.join(os.path.dirname(__file__), "..")
    r = subprocess.run([ruff, "check", ".", "--no-cache", "--output-format", "concise"],
                       cwd=root, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr


def test_tests_never_use_real_pi_paths():
    """Запобіжник: під час тестів усі робочі шляхи - тимчасові."""
    import os
    from app import config, config_editor
    from conftest import REAL_PATHS
    for path in (config_editor.ENV_FILE_PATH, config.AUTO_BACKUP_DIR, config.DB_PATH):
        assert not os.path.abspath(path).startswith(REAL_PATHS), path


# ---- межі числових параметрів (/settings приймав будь-яке число, включно з nan/inf/0/-5) ----

def _numeric_params():
    return [p for p in config_editor.EDITABLE_PARAMS if p["type"] in ("int", "float")]


def test_every_numeric_param_has_limits_and_no_unknown_keys():
    """Новий числовий параметр без меж - тихий доступ до nan/0/-5."""
    keys = {p["key"] for p in _numeric_params()}
    assert sorted(keys - set(config_editor.LIMITS)) == []
    assert sorted(set(config_editor.LIMITS) - keys) == []          # опечатка в ключі таблиці
    assert set(config_editor.CHOICES) <= keys


def test_defaults_are_within_limits():
    """Таблиця меж не має відкидати дефолти застосунку."""
    for p in _numeric_params():
        ok, err = config_editor._validate_value(p, p["default"])
        assert ok, (p["key"], p["default"], err)


# Справжні перевизначення користувача (/etc/starlink-monitor/env з діагностичного звіту)
_REAL_USER_ENV = {
    "STARLINK_ACTIVITY_LED_BLINK_MS": "100", "STARLINK_ACTIVITY_LED_PIN": "23",
    "STARLINK_AUTO_BACKUP_INTERVAL_SEC": "172800", "STARLINK_AUTO_BACKUP_KEEP_COUNT": "2",
    "STARLINK_DISH_METRICS_BATCH_INTERVAL_SEC": "30", "STARLINK_DISPLAY_BACKLIGHT_AUTO_OFF_SEC": "30",
    "STARLINK_DISPLAY_ENABLED": "1", "STARLINK_DISPLAY_OFFSET_LEFT": "35", "STARLINK_DISPLAY_ROTATION": "90",
    "STARLINK_DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC": "2", "STARLINK_DISPLAY_UPDATE_FLASH_SEC": "30",
    "STARLINK_HISTORY_DAYS": "5", "STARLINK_MAX_FAILURES": "10", "STARLINK_MAX_LOGGED_FAILURES": "20",
    "STARLINK_MIN_REBOOT_INTERVAL": "300", "STARLINK_NOTIFY_DISH_RECOVERY": "0",
    "STARLINK_SHUTDOWN_BUTTON_PIN": "27", "STARLINK_TELEGRAM_BACKUP_ENABLED": "1",
    "STARLINK_TELEGRAM_BACKUP_INTERVAL_HOURS": "72", "STARLINK_TELEGRAM_ID_LIST_MAX_ITEMS": "30",
    "STARLINK_TELEGRAM_POLL_TIMEOUT_SEC": "25",
}


def test_real_user_overrides_pass_validation():
    """Обмеження не мають відкинути налаштування, які вже працюють на Pi."""
    by_key = {p["key"]: p for p in config_editor.EDITABLE_PARAMS}
    for key, value in _REAL_USER_ENV.items():
        ok, err = config_editor._validate_value(by_key[key], value)
        assert ok, (key, value, err)


@pytest.mark.parametrize("bad", [
    "nan", "inf", "-inf", "1e999", "-1e999", "-1", "abc",
    pytest.param("9" * 400, id="huge-int-400-digits"),      # isfinite(int) дав би OverflowError
])
def test_every_numeric_param_rejects_nonsense(bad):
    for p in _numeric_params():
        ok, _ = config_editor._validate_value(p, bad)       # "9"*400 раніше міг дати OverflowError
        assert ok is False, (p["key"], bad)


def test_limit_boundaries_accepted_and_just_outside_rejected():
    for p in _numeric_params():
        lo, hi = config_editor.LIMITS[p["key"]]
        step = 1 if p["type"] == "int" else 0.001
        fmt = (lambda n: str(int(n))) if p["type"] == "int" else (lambda n: repr(float(n)))
        choices = config_editor.CHOICES.get(p["key"])
        for inside in ((lo, hi) if choices is None else choices):
            assert config_editor._validate_value(p, fmt(inside))[0] is True, (p["key"], inside)
        for outside in (lo - step, hi + step):
            assert config_editor._validate_value(p, fmt(outside))[0] is False, (p["key"], outside)


@pytest.mark.parametrize("key,value", [
    ("STARLINK_POLL_INTERVAL", "0"),                    # цикл без пауз
    ("STARLINK_POLL_INTERVAL", "-5"),                   # time.sleep кидав ValueError - падав монітор
    ("STARLINK_HISTORY_DAYS", "0"),                     # видаляв би ВСЮ історію щогодини
    ("STARLINK_TELEGRAM_BACKUP_INTERVAL_HOURS", "0"),   # бекап у Telegram кожні 10 с
    ("STARLINK_AUTO_BACKUP_INTERVAL_SEC", "0"),         # файл бекапу щоітерації - знос SD
    ("STARLINK_MIN_REBOOT_INTERVAL", "0"),              # знімає захист від reboot-циклу тарілки
    ("STARLINK_WEBUI_PORT", "0"),                       # випадковий порт - дашборд не знайти
    ("STARLINK_WEBUI_PORT", "70000"),
    ("STARLINK_DISPLAY_ROTATION", "45"),
    ("STARLINK_OBSTRUCTION_WARN", "1.5"),
    ("STARLINK_SHUTDOWN_BUTTON_PIN", "99"),
])
def test_dangerous_values_from_real_audit_rejected(key, value):
    by_key = {p["key"]: p for p in config_editor.EDITABLE_PARAMS}
    assert config_editor._validate_value(by_key[key], value)[0] is False


def test_zero_still_allowed_where_it_means_disabled():
    by_key = {p["key"]: p for p in config_editor.EDITABLE_PARAMS}
    for key in ("STARLINK_SHUTDOWN_BUTTON_PIN", "STARLINK_ACTIVITY_LED_PIN", "STARLINK_DISPLAY_BL_PIN",
                "STARLINK_DISPLAY_BACKLIGHT_AUTO_OFF_SEC", "STARLINK_AUTO_BACKUP_KEEP_COUNT",
                "STARLINK_DISPLAY_UPDATE_FLASH_SEC", "STARLINK_TELEGRAM_SEND_RETRIES"):
        assert config_editor._validate_value(by_key[key], "0")[0] is True, key


def test_out_of_range_saves_nothing_and_explains_in_both_languages(env_file, db_path):
    env_file.write_text("STARLINK_POLL_INTERVAL=10\n")
    original = env_file.read_text()
    ok, msg = config_editor.save_values({"STARLINK_HISTORY_DAYS": "0", "STARLINK_POLL_INTERVAL": "20"})
    assert ok is False and "від 1 до 3650" in msg
    assert env_file.read_text() == original              # валідний сусід теж не записаний
    db.set_setting("ui_language", "en")
    ok, msg = config_editor.save_values({"STARLINK_DISPLAY_ROTATION": "45"})
    assert ok is False and "allowed values: 0, 90, 180, 270" in msg
    ok, msg = config_editor.save_values({"STARLINK_DISH_TIMEOUT": "nan"})
    assert ok is False and "finite number" in msg


def test_mypy_config_stays_strict():
    """Суворий режим mypy для app.* не можна тихо послабити. Еквівалент `--strict` (13 прапорців)
    розгорнуто в mypy.ini (`strict` у per-module секції недоступний); requests/psutil/dns мають справжні
    стаби, ігнорувати дозволено лише апаратні бібліотеки Pi.
    """
    import configparser
    import os
    cfg = configparser.ConfigParser()
    cfg.read(os.path.join(os.path.dirname(__file__), "..", "mypy.ini"), encoding="utf-8")
    strict = {
        "check_untyped_defs", "disallow_any_generics", "disallow_incomplete_defs",
        "disallow_subclassing_any", "disallow_untyped_calls", "disallow_untyped_decorators",
        "disallow_untyped_defs", "extra_checks", "no_implicit_reexport", "strict_equality",
        "warn_return_any", "warn_unused_ignores", "warn_redundant_casts",
    }
    enabled = {k for k, v in list(cfg["mypy-app.*"].items()) + list(cfg["mypy"].items()) if v.strip() == "True"}
    assert sorted(strict - enabled) == [], "ослаблено суворий режим mypy"
    ignored = {sec[5:] for sec in cfg.sections() if sec.startswith("mypy-") and cfg[sec].get("ignore_missing_imports") == "True"}
    assert ignored == {"gpiod.*", "board", "digitalio", "busio", "adafruit_rgb_display.*"}, ignored


def test_env_var_names_in_app_strings_exist_in_config():
    """Повідомлення й докстрінги не називають неіснуючих змінних середовища.
    Раніше попередження про поганий інтервал радило виправити
    STARLINK_POLL_INTERVAL_SEC, а справжня змінна - STARLINK_POLL_INTERVAL."""
    import ast
    import glob
    import re
    from pathlib import Path
    root = Path(__file__).parent.parent
    known = set(re.findall(r'"(STARLINK_[A-Z0-9_]+)"', (root / "app" / "config.py").read_text(encoding="utf-8")))
    assert len(known) > 50
    unknown = []
    for path in sorted(glob.glob(str(root / "app" / "*.py"))):
        if path.endswith("config.py"):
            continue
        for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):     # включно з частинами f-рядків і докстрінгами
                unknown += [(Path(path).name, node.lineno, name) for name in re.findall(r"\bSTARLINK_[A-Z0-9_]+\b", node.value) if name not in known]
    assert unknown == [], f"назви змінних, яких немає в config.py: {unknown}"


# ---- адреси dish/роутера: лише host:порт ----

_GOOD_ADDRESSES = ["192.168.100.1:9200", "192.168.1.1:9000", "router.lan:9000", "dish-1.local:9200", "[fd00::1]:9000", "a:1", "host:65535"]
_BAD_ADDRESSES = [
    "http://192.168.100.1:9200", "192.168.100.1 9200", "192.168.100.1;9200", '192.168.100.1:9200"', "192.168.100.1:9200'",
    "192.168.100.1:9200\\", "$HOME:9200", "192.168.100.1:99999", "192.168.100.1:0", ":9200", "192.168.100.1", "192.168.100.1:abc",
    "192.168.100.1:", "-host:9200", "host-:9200", "host name:9200", "#коментар:9200", "a" * 300 + ":9200", "[::1:9000", "хост:9200",
]


@pytest.mark.parametrize("key", ["STARLINK_DISH_ADDR", "STARLINK_ROUTER_ADDR"])
@pytest.mark.parametrize("address", _GOOD_ADDRESSES)
def test_valid_addresses_are_accepted(tmp_path, monkeypatch, key, address):
    monkeypatch.setattr(config_editor, "ENV_FILE_PATH", str(tmp_path / "env"))
    ok, message = config_editor.save_values({key: address})
    assert ok, message
    assert f"{key}={address}\n" in (tmp_path / "env").read_text(encoding="utf-8")


@pytest.mark.parametrize("key", ["STARLINK_DISH_ADDR", "STARLINK_ROUTER_ADDR"])
@pytest.mark.parametrize("address", _BAD_ADDRESSES)
def test_malformed_addresses_are_rejected_and_nothing_is_written(tmp_path, monkeypatch, db_path, key, address):
    """Раніше приймались усі: URL, лапки, слеш, порт 99999, 300 символів."""
    env = tmp_path / "env"
    env.write_text("STARLINK_POLL_INTERVAL=7\n", encoding="utf-8")
    monkeypatch.setattr(config_editor, "ENV_FILE_PATH", str(env))
    ok, message = config_editor.save_values({key: address})
    assert ok is False and message
    assert env.read_text(encoding="utf-8") == "STARLINK_POLL_INTERVAL=7\n"        # файл не змінено


def test_empty_address_removes_the_override(tmp_path, monkeypatch):
    env = tmp_path / "env"
    env.write_text("STARLINK_DISH_ADDR=10.0.0.1:9200\nSTARLINK_POLL_INTERVAL=7\n", encoding="utf-8")
    monkeypatch.setattr(config_editor, "ENV_FILE_PATH", str(env))
    ok, _ = config_editor.save_values({"STARLINK_DISH_ADDR": ""})
    assert ok and env.read_text(encoding="utf-8") == "STARLINK_POLL_INTERVAL=7\n"


def test_default_addresses_pass_their_own_validation():
    from app import config
    for key, default in (("STARLINK_DISH_ADDR", config.DISH_ADDR), ("STARLINK_ROUTER_ADDR", config.ROUTER_ADDR)):
        assert config_editor._address_problem(default) is None, (key, default)


# ---- таблиця валідаторів за типом (замість ланцюжка if/elif у _validate_value) ----

def test_every_param_type_has_a_validator():
    """Тип без запису в _VALIDATORS проходив би без перевірки мовчки."""
    types = {p["type"] for p in config_editor.EDITABLE_PARAMS}
    assert types <= set(config_editor._VALIDATORS), sorted(types - set(config_editor._VALIDATORS))


def test_bool_validator_accepts_only_zero_and_one(db_path):
    param = {"type": "bool", "key": "X"}
    assert config_editor._check_bool(param, "0") is None and config_editor._check_bool(param, "1") is None
    for bad in ("2", "true", "yes", "01"):
        assert config_editor._check_bool(param, bad)


def test_number_validator_int_float_range_and_finiteness(db_path):
    integer = {"type": "int", "key": "STARLINK_HISTORY_DAYS"}
    floating = {"type": "float", "key": "STARLINK_OBSTRUCTION_WARN"}
    assert config_editor._check_number(integer, "14") is None
    assert config_editor._check_number(integer, "0")                 # поза межами: HISTORY_DAYS=0 видаляв би всю історію
    assert config_editor._check_number(floating, "0.5") is None
    assert config_editor._check_number(floating, "nan") and config_editor._check_number(floating, "inf")
    with pytest.raises(ValueError):                                  # нечисло: ValueError ловить _validate_value
        config_editor._check_number(integer, "abc")


def test_str_validator_checks_only_address_parameters(db_path):
    assert config_editor._check_str({"type": "str", "key": "STARLINK_DISH_ADDR"}, "192.168.100.1:9200") is None
    assert config_editor._check_str({"type": "str", "key": "STARLINK_DISH_ADDR"}, "http://x")
    assert config_editor._check_str({"type": "str", "key": "STARLINK_SOMETHING_ELSE"}, "будь-який текст з пробілами") is None


def test_validate_value_reports_type_error_for_non_numbers(db_path):
    ok, message = config_editor._validate_value({"type": "int", "key": "STARLINK_HISTORY_DAYS"}, "abc")
    assert ok is False and message
