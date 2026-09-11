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
from typing import Any

from app import config

logger = logging.getLogger("config_editor")

ENV_FILE_PATH = "/etc/starlink-monitor/env"

EDITABLE_PARAMS = [
    {"key": "STARLINK_DISH_ADDR", "type": "str", "default": "192.168.100.1:9200", "label": "Адреса тарілки (dish)", "category": "monitoring"},
    {"key": "STARLINK_DISH_TIMEOUT", "type": "float", "default": "5", "label": "Timeout запиту до dish, сек", "category": "monitoring"},
    {"key": "STARLINK_ROUTER_ADDR", "type": "str", "default": "192.168.1.1:9000", "label": "Адреса роутера", "category": "monitoring"},
    {"key": "STARLINK_POLL_INTERVAL", "type": "int", "default": "10", "label": "Інтервал опитування dish, сек", "category": "monitoring"},
    {"key": "STARLINK_ROUTER_POLL_INTERVAL_SEC", "type": "int", "default": "35", "label": "Інтервал опитування router, сек", "category": "monitoring"},
    {"key": "STARLINK_SYSTEM_METRICS_INTERVAL_SEC", "type": "int", "default": "60", "label": "Інтервал запису CPU/пам'яті/температури, сек", "category": "monitoring"},
    {"key": "STARLINK_DISH_METRICS_BATCH_INTERVAL_SEC", "type": "int", "default": "30", "label": "Інтервал batch-запису dish-метрик, сек", "category": "monitoring"},
    {"key": "STARLINK_OBSTRUCTION_WARN", "type": "float", "default": "0.05", "label": "Поріг попередження про перешкоди (0-1)", "category": "monitoring"},
    {"key": "STARLINK_HISTORY_DAYS", "type": "int", "default": "30", "label": "Зберігати історію, днів", "category": "monitoring"},
    {"key": "STARLINK_WEBUI_PORT", "type": "int", "default": "8080", "label": "Порт веб-інтерфейсу", "category": "monitoring"},

    {"key": "STARLINK_DB_INTEGRITY_CHECK_INTERVAL_SEC", "type": "int", "default": "86400", "label": "Інтервал перевірки цілісності БД, сек", "category": "reliability"},
    {"key": "STARLINK_AUTO_BACKUP_ENABLED", "type": "bool", "default": "1", "label": "Автоматичний періодичний backup (0/1)", "category": "reliability"},
    {"key": "STARLINK_AUTO_BACKUP_INTERVAL_SEC", "type": "int", "default": "604800", "label": "Інтервал автоматичного backup, сек", "category": "reliability"},
    {"key": "STARLINK_AUTO_BACKUP_KEEP_COUNT", "type": "int", "default": "4", "label": "Скільки останніх backup-ів зберігати", "category": "reliability"},
    {"key": "STARLINK_TELEGRAM_BACKUP_ENABLED", "type": "bool", "default": "0", "label": "Періодично надсилати backup у Telegram (0/1)", "category": "reliability"},
    {"key": "STARLINK_TELEGRAM_BACKUP_INTERVAL_HOURS", "type": "float", "default": "168", "label": "Інтервал відправки backup у Telegram, годин", "category": "reliability"},
    {"key": "STARLINK_MAX_FAILURES", "type": "int", "default": "6", "label": "Невдалих опитувань перед watchdog-reboot", "category": "reliability"},
    {"key": "STARLINK_MIN_REBOOT_INTERVAL", "type": "int", "default": "180", "label": "Мін. інтервал між авто-ребутами, сек", "category": "reliability"},
    {"key": "STARLINK_AUTO_REBOOT_ON_UPDATE", "type": "bool", "default": "1", "label": "Авто-reboot при готовому оновленні", "category": "reliability"},
    {"key": "STARLINK_MAX_LOGGED_FAILURES", "type": "int", "default": "15", "label": "Макс. послідовних невдач у журналі перед припиненням запису", "category": "reliability"},
    {"key": "STARLINK_REBOOT_SPAM_THRESHOLD", "type": "int", "default": "3", "label": "Група reboot-сповіщень: поріг кількості за вікно", "category": "reliability"},
    {"key": "STARLINK_REBOOT_SPAM_WINDOW_SEC", "type": "int", "default": "1800", "label": "Група reboot-сповіщень: вікно часу, сек", "category": "reliability"},

    {"key": "STARLINK_NOTIFICATIONS_MUTE_AFTER", "type": "int", "default": "900", "label": "Приглушити Telegram після недоступності dish, сек", "category": "telegram"},
    {"key": "STARLINK_NOTIFY_DISH_RECOVERY", "type": "bool", "default": "1", "label": "Сповіщати 'Dish знову online' (0/1)", "category": "telegram"},
    {"key": "STARLINK_NOTIFY_FIRMWARE_ROLLBACK", "type": "bool", "default": "1", "label": "Сповіщати про відкат прошивки (0/1)", "category": "telegram"},
    {"key": "STARLINK_NOTIFY_PI_STARTUP", "type": "bool", "default": "1", "label": "Сповіщати про запуск Pi після перезавантаження (0/1)", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_SEND_TIMEOUT_SEC", "type": "float", "default": "10", "label": "Telegram-бот: timeout надсилання, сек", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_POLL_TIMEOUT_SEC", "type": "float", "default": "30", "label": "Telegram-бот: timeout long-polling, сек", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_CONFIRM_TTL_SEC", "type": "float", "default": "120", "label": "Telegram-бот: TTL підтвердження команд, сек", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_NOTIFY_TIMEOUT_SEC", "type": "float", "default": "10", "label": "Telegram-сповіщення: timeout запиту, сек", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_SEND_RETRIES", "type": "int", "default": "1", "label": "Telegram: повторних спроб при мережевій помилці", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_SEND_RETRY_DELAY_SEC", "type": "float", "default": "2", "label": "Telegram: затримка між повторами, сек", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_ID_LIST_MAX_ITEMS", "type": "int", "default": "40", "label": "/id: макс. тарілок у списку без аргументу", "category": "telegram"},

    {"key": "STARLINK_SHUTDOWN_BUTTON_PIN", "type": "int", "default": "27", "label": "GPIO-пін кнопки виключення (0=вимк.)", "category": "gpio"},
    {"key": "STARLINK_SHUTDOWN_BUTTON_HOLD_SEC", "type": "float", "default": "3", "label": "Утримання кнопки перед вимкненням, сек", "category": "gpio"},
    {"key": "STARLINK_SHUTDOWN_BUTTON_POLL_INTERVAL_SEC", "type": "float", "default": "0.1", "label": "Опитування GPIO кнопки виключення, сек", "category": "gpio"},
    {"key": "STARLINK_ACTIVITY_LED_PIN", "type": "int", "default": "0", "label": "GPIO-пін LED активності SD-картки (0=вимк.)", "category": "gpio"},
    {"key": "STARLINK_ACTIVITY_LED_BLINK_MS", "type": "int", "default": "50", "label": "Тривалість спалаху LED активності, мс", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_ENABLED", "type": "bool", "default": "0", "label": "Фізичний TFT-дисплей статусу (0/1)", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_BUTTON_POLL_INTERVAL_SEC", "type": "float", "default": "0.1", "label": "Опитування GPIO кнопки дисплея, сек", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_SPI_CS_PIN", "type": "int", "default": "8", "label": "Дисплей: GPIO-пін CS (bit-banged, не апаратний CE0/CE1)", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_DC_PIN", "type": "int", "default": "25", "label": "Дисплей: GPIO-пін DC", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_RST_PIN", "type": "int", "default": "24", "label": "Дисплей: GPIO-пін RES", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_BL_PIN", "type": "int", "default": "18", "label": "Дисплей: GPIO-пін підсвітки (0=не керувати)", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_WIDTH", "type": "int", "default": "170", "label": "Дисплей: ширина, px", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_HEIGHT", "type": "int", "default": "320", "label": "Дисплей: висота, px", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_ROTATION", "type": "int", "default": "0", "label": "Дисплей: поворот, ° (0/90/180/270)", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_OFFSET_LEFT", "type": "int", "default": "35", "label": "Дисплей: зміщення X відносно GRAM, px", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_OFFSET_TOP", "type": "int", "default": "0", "label": "Дисплей: зміщення Y відносно GRAM, px", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_REFRESH_SEC", "type": "int", "default": "5", "label": "Дисплей: інтервал оновлення, сек", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC", "type": "float", "default": "2", "label": "Дисплей: затримка перед reboot/poweroff, сек", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_SPI_SPEED_HZ", "type": "int", "default": "40000000", "label": "Дисплей: швидкість SPI, Гц", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_BACKLIGHT_AUTO_OFF_SEC", "type": "int", "default": "60", "label": "Дисплей: автовимкнення підсвітки, сек (0=вимк.)", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_UPDATE_FLASH_SEC", "type": "int", "default": "5", "label": "Дисплей: підсвітка при зміні статусу оновлення, сек (0=вимк.)", "category": "gpio"},
]

# Порядок і підписи категорій для групування на /settings - тримати
# синхронізованим із фактичними category-значеннями вище (тест
# перевіряє, що кожен параметр має валідну категорію з цього списку).
CATEGORY_LABELS = {
    "monitoring": "Моніторинг та мережа",
    "reliability": "Надійність",
    "telegram": "Telegram",
    "gpio": "GPIO та периферія",
}

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


def read_current_values() -> list[dict[str, Any]]:
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


def save_values(values: dict[str, str]) -> tuple[bool, str]:
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
