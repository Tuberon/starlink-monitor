"""
Редагування env-змінних Starlink Monitor (`/etc/starlink-monitor/env`)
через веб-інтерфейс. Параметри застосовуються лише після перезапуску
сервісів (env читається один раз при старті процесу) - веб-інтерфейс
сам пропонує рестарт після збереження.

EDITABLE_PARAMS - явний список (не довільний текст), узгоджений з
app/config.py: назва env-змінної, тип для валідації, значення за
замовчуванням (з config.py) і короткий опис для UI.
"""
import logging
import os
import re

from app import config

logger = logging.getLogger("config_editor")

ENV_FILE_PATH = "/etc/starlink-monitor/env"

EDITABLE_PARAMS = [
    {"key": "STARLINK_DISH_ADDR", "type": "str", "default": "192.168.100.1:9200", "label": "Адреса тарілки (dish)"},
    {"key": "STARLINK_ROUTER_ADDR", "type": "str", "default": "192.168.1.1:9000", "label": "Адреса роутера"},
    {"key": "STARLINK_POLL_INTERVAL", "type": "int", "default": "10", "label": "Інтервал опитування dish, сек"},
    {"key": "STARLINK_MAX_FAILURES", "type": "int", "default": "6", "label": "Невдалих опитувань перед watchdog-reboot"},
    {"key": "STARLINK_MIN_REBOOT_INTERVAL", "type": "int", "default": "120", "label": "Мін. інтервал між авто-ребутами, сек"},
    {"key": "STARLINK_NOTIFICATIONS_MUTE_AFTER", "type": "int", "default": "900", "label": "Приглушити Telegram після недоступності dish, сек"},
    {"key": "STARLINK_MAX_LOGGED_FAILURES", "type": "int", "default": "30", "label": "Макс. послідовних невдач у журналі перед припиненням запису"},
    {"key": "STARLINK_OBSTRUCTION_WARN", "type": "float", "default": "0.05", "label": "Поріг попередження про перешкоди (0-1)"},
    {"key": "STARLINK_AUTO_REBOOT_ON_UPDATE", "type": "bool", "default": "1", "label": "Авто-reboot при готовому оновленні"},
    {"key": "STARLINK_HISTORY_DAYS", "type": "int", "default": "30", "label": "Зберігати історію, днів"},
    {"key": "STARLINK_WEBUI_PORT", "type": "int", "default": "8080", "label": "Порт веб-інтерфейсу"},
    {"key": "STARLINK_SHUTDOWN_BUTTON_PIN", "type": "int", "default": "0", "label": "GPIO-пін кнопки виключення (0=вимк.)"},
    {"key": "STARLINK_SHUTDOWN_BUTTON_HOLD_SEC", "type": "float", "default": "3", "label": "Утримання кнопки перед вимкненням, сек"},
]

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _validate_value(param: dict, raw_value: str) -> tuple:
    """Перевіряє значення проти заявленого типу. Повертає (ok, error_or_value)."""
    t = param["type"]
    v = raw_value.strip()
    if v == "":
        return True, None  # порожньо = прибрати перевизначення, лишити default
    try:
        if t == "int":
            int(v)
        elif t == "float":
            float(v)
        elif t == "bool":
            if v not in ("0", "1"):
                return False, "очікується 0 або 1"
        # "str" - без додаткової перевірки
        return True, v
    except ValueError:
        return False, f"очікується {t}"


def read_current_values() -> dict:
    """Читає поточні значення з env-файлу (якщо параметр там не
    перевизначений - повертає значення з config.py, яке саме й діє
    зараз у запущеному процесі)."""
    file_values = {}
    if os.path.exists(ENV_FILE_PATH):
        with open(ENV_FILE_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                file_values[k.strip()] = v.strip()

    result = []
    for param in EDITABLE_PARAMS:
        key = param["key"]
        current = file_values.get(key, "")
        active = getattr(config, key.replace("STARLINK_", "", 1), None)
        result.append({
            **param,
            "current": current,
            "active": str(active) if active is not None else param["default"],
            "overridden": key in file_values,
        })
    return result


def save_values(values: dict) -> tuple:
    """values: {ENV_KEY: raw_value_str}. Валідує всі значення, потім
    перезаписує env-файл: рядки з відомих EDITABLE_PARAMS замінюються/
    додаються, довільний інший вміст файлу (коментарі, невідомі
    змінні) зберігається без змін."""
    known_keys = {p["key"] for p in EDITABLE_PARAMS}
    errors = []
    validated = {}
    for key, raw_value in values.items():
        if key not in known_keys:
            continue
        if not _KEY_RE.match(key):
            errors.append(f"{key}: недопустима назва")
            continue
        param = next(p for p in EDITABLE_PARAMS if p["key"] == key)
        ok, value_or_err = _validate_value(param, raw_value)
        if not ok:
            errors.append(f"{param['label']}: {value_or_err}")
        else:
            validated[key] = value_or_err  # None означає "прибрати з файлу"

    if errors:
        return False, "; ".join(errors)

    try:
        existing_lines = []
        if os.path.exists(ENV_FILE_PATH):
            with open(ENV_FILE_PATH, encoding="utf-8") as f:
                existing_lines = f.readlines()

        written_keys = set()
        new_lines = []
        for line in existing_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k = stripped.split("=", 1)[0].strip()
                if k in validated:
                    written_keys.add(k)
                    if validated[k] is not None:
                        new_lines.append(f"{k}={validated[k]}\n")
                    continue  # пропускаємо (видалено) якщо None
            new_lines.append(line)

        for key, value in validated.items():
            if key not in written_keys and value is not None:
                new_lines.append(f"{key}={value}\n")

        os.makedirs(os.path.dirname(ENV_FILE_PATH), exist_ok=True)
        with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True, "збережено"
    except OSError as e:
        logger.warning("Не вдалося записати %s: %s", ENV_FILE_PATH, e)
        return False, str(e)
