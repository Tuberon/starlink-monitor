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
import math
import os
import re
from typing import Any, Callable, Optional

from app import config, i18n
from app.atomic_io import atomic_write_text

logger = logging.getLogger("config_editor")

ENV_FILE_PATH = "/etc/starlink-monitor/env"

EDITABLE_PARAMS = [
    {"key": "STARLINK_DISH_ADDR", "type": "str", "default": "192.168.100.1:9200", "label": "param_dish_addr", "category": "monitoring"},
    {"key": "STARLINK_DISH_TIMEOUT", "type": "float", "default": "5", "label": "param_dish_timeout", "category": "monitoring"},
    {"key": "STARLINK_ROUTER_ADDR", "type": "str", "default": "192.168.1.1:9000", "label": "param_router_addr", "category": "monitoring"},
    {"key": "STARLINK_POLL_INTERVAL", "type": "int", "default": "10", "label": "param_poll_interval", "category": "monitoring"},
    {"key": "STARLINK_ROUTER_POLL_INTERVAL_SEC", "type": "int", "default": "35", "label": "param_router_poll_interval", "category": "monitoring"},
    {"key": "STARLINK_SYSTEM_METRICS_INTERVAL_SEC", "type": "int", "default": "60", "label": "param_system_metrics_interval", "category": "monitoring"},
    {"key": "STARLINK_DISH_METRICS_BATCH_INTERVAL_SEC", "type": "int", "default": "30", "label": "param_dish_metrics_batch_interval", "category": "monitoring"},
    {"key": "STARLINK_OBSTRUCTION_WARN", "type": "float", "default": "0.05", "label": "param_obstruction_warn", "category": "monitoring"},
    {"key": "STARLINK_HISTORY_DAYS", "type": "int", "default": "14", "label": "param_history_days", "category": "monitoring"},
    {"key": "STARLINK_WEBUI_PORT", "type": "int", "default": "8080", "label": "param_webui_port", "category": "monitoring"},

    {"key": "STARLINK_DB_INTEGRITY_CHECK_INTERVAL_SEC", "type": "int", "default": "86400", "label": "param_db_integrity_check_interval", "category": "reliability"},
    {"key": "STARLINK_AUTO_BACKUP_ENABLED", "type": "bool", "default": "1", "label": "param_auto_backup_enabled", "category": "reliability"},
    {"key": "STARLINK_AUTO_BACKUP_INTERVAL_SEC", "type": "int", "default": "604800", "label": "param_auto_backup_interval", "category": "reliability"},
    {"key": "STARLINK_AUTO_BACKUP_KEEP_COUNT", "type": "int", "default": "4", "label": "param_auto_backup_keep_count", "category": "reliability"},
    {"key": "STARLINK_TELEGRAM_BACKUP_ENABLED", "type": "bool", "default": "0", "label": "param_telegram_backup_enabled", "category": "reliability"},
    {"key": "STARLINK_TELEGRAM_BACKUP_INTERVAL_HOURS", "type": "float", "default": "168", "label": "param_telegram_backup_interval_hours", "category": "reliability"},
    {"key": "STARLINK_MAX_FAILURES", "type": "int", "default": "6", "label": "param_max_failures", "category": "reliability"},
    {"key": "STARLINK_MIN_REBOOT_INTERVAL", "type": "int", "default": "180", "label": "param_min_reboot_interval", "category": "reliability"},
    {"key": "STARLINK_AUTO_REBOOT_ON_UPDATE", "type": "bool", "default": "1", "label": "param_auto_reboot_on_update", "category": "reliability"},
    {"key": "STARLINK_MAX_LOGGED_FAILURES", "type": "int", "default": "15", "label": "param_max_logged_failures", "category": "reliability"},
    {"key": "STARLINK_REBOOT_SPAM_THRESHOLD", "type": "int", "default": "3", "label": "param_reboot_spam_threshold", "category": "reliability"},
    {"key": "STARLINK_REBOOT_SPAM_WINDOW_SEC", "type": "int", "default": "1800", "label": "param_reboot_spam_window", "category": "reliability"},

    {"key": "STARLINK_NOTIFICATIONS_MUTE_AFTER", "type": "int", "default": "900", "label": "param_notifications_mute_after", "category": "telegram"},
    {"key": "STARLINK_NOTIFY_DISH_RECOVERY", "type": "bool", "default": "1", "label": "param_notify_dish_recovery", "category": "telegram"},
    {"key": "STARLINK_NOTIFY_PI_STARTUP", "type": "bool", "default": "1", "label": "param_notify_pi_startup", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_SEND_TIMEOUT_SEC", "type": "float", "default": "10", "label": "param_telegram_send_timeout", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_POLL_TIMEOUT_SEC", "type": "float", "default": "30", "label": "param_telegram_poll_timeout", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_CONFIRM_TTL_SEC", "type": "float", "default": "120", "label": "param_telegram_confirm_ttl", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_NOTIFY_TIMEOUT_SEC", "type": "float", "default": "10", "label": "param_telegram_notify_timeout", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_SEND_RETRIES", "type": "int", "default": "1", "label": "param_telegram_send_retries", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_SEND_RETRY_DELAY_SEC", "type": "float", "default": "2", "label": "param_telegram_send_retry_delay", "category": "telegram"},
    {"key": "STARLINK_TELEGRAM_ID_LIST_MAX_ITEMS", "type": "int", "default": "40", "label": "param_telegram_id_list_max", "category": "telegram"},

    {"key": "STARLINK_SHUTDOWN_BUTTON_PIN", "type": "int", "default": "27", "label": "param_shutdown_button_pin", "category": "gpio"},
    {"key": "STARLINK_SHUTDOWN_BUTTON_HOLD_SEC", "type": "float", "default": "3", "label": "param_shutdown_button_hold", "category": "gpio"},
    {"key": "STARLINK_SHUTDOWN_BUTTON_POLL_INTERVAL_SEC", "type": "float", "default": "0.1", "label": "param_shutdown_button_poll", "category": "gpio"},
    {"key": "STARLINK_ACTIVITY_LED_PIN", "type": "int", "default": "0", "label": "param_activity_led_pin", "category": "gpio"},
    {"key": "STARLINK_ACTIVITY_LED_BLINK_MS", "type": "int", "default": "50", "label": "param_activity_led_blink_ms", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_ENABLED", "type": "bool", "default": "0", "label": "param_display_enabled", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_BUTTON_POLL_INTERVAL_SEC", "type": "float", "default": "0.1", "label": "param_display_button_poll", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_SPI_CS_PIN", "type": "int", "default": "8", "label": "param_display_spi_cs_pin", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_DC_PIN", "type": "int", "default": "25", "label": "param_display_dc_pin", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_RST_PIN", "type": "int", "default": "24", "label": "param_display_rst_pin", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_BL_PIN", "type": "int", "default": "18", "label": "param_display_bl_pin", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_WIDTH", "type": "int", "default": "170", "label": "param_display_width", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_HEIGHT", "type": "int", "default": "320", "label": "param_display_height", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_ROTATION", "type": "int", "default": "0", "label": "param_display_rotation", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_OFFSET_LEFT", "type": "int", "default": "35", "label": "param_display_offset_left", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_OFFSET_TOP", "type": "int", "default": "0", "label": "param_display_offset_top", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_REFRESH_SEC", "type": "int", "default": "5", "label": "param_display_refresh", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC", "type": "float", "default": "2", "label": "param_display_shutdown_delay", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_SPI_SPEED_HZ", "type": "int", "default": "40000000", "label": "param_display_spi_speed", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_BACKLIGHT_AUTO_OFF_SEC", "type": "int", "default": "60", "label": "param_display_backlight_auto_off", "category": "gpio"},
    {"key": "STARLINK_DISPLAY_UPDATE_FLASH_SEC", "type": "int", "default": "5", "label": "param_display_update_flash", "category": "gpio"},
]

# Порядок і підписи категорій для групування на /settings - тримати
# синхронізованим із фактичними category-значеннями вище (тест
# перевіряє, що кожен параметр має валідну категорію з цього списку).
# Значення тут - i18n-КЛЮЧІ (app/i18n.py), не готовий текст - той
# самий принцип, що EDITABLE_PARAMS[].label нижче, для підтримки
# мультимовного інтерфейсу. Переклад відбувається на стороні
# webapp.py (api_get_env_config()) перед поверненням JSON.
CATEGORY_LABELS = {
    "monitoring": "cat_monitoring",
    "reliability": "cat_reliability",
    "telegram": "cat_telegram",
    "gpio": "cat_gpio",
}

_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


# Межі значень числових параметрів (включно). Раніше /settings перевіряв
# лише ТИП: float() приймає "nan"/"inf"/"1e999", а 0 для таймера означає
# "на кожній ітерації" (не "вимкнено"): POLL_INTERVAL=-5/nan валив монітор
# (time.sleep поза обробником винятків), HISTORY_DAYS=0 видаляв УСЮ
# історію щогодини, TELEGRAM_BACKUP_INTERVAL_HOURS=0 слав бекап кожні
# 10 с, WEBUI_PORT=0 відкривав дашборд на випадковому порту. Нуль лишено
# допустимим лише там, де він означає "вимкнено" (піни, автовимкнення
# підсвітки, KEEP_COUNT, затримки). Кожен числовий параметр МАЄ межі
# (тест), дефолти й перевизначення користувача - у межах (тести).
LIMITS: dict[str, tuple[float, float]] = {
    "STARLINK_DISH_TIMEOUT": (0.5, 60),
    "STARLINK_POLL_INTERVAL": (1, 3600),
    "STARLINK_ROUTER_POLL_INTERVAL_SEC": (5, 86400),
    "STARLINK_SYSTEM_METRICS_INTERVAL_SEC": (5, 86400),
    "STARLINK_DISH_METRICS_BATCH_INTERVAL_SEC": (1, 3600),
    "STARLINK_OBSTRUCTION_WARN": (0, 1),
    "STARLINK_HISTORY_DAYS": (1, 3650),
    "STARLINK_WEBUI_PORT": (1, 65535),
    "STARLINK_DB_INTEGRITY_CHECK_INTERVAL_SEC": (60, 31_536_000),
    "STARLINK_AUTO_BACKUP_INTERVAL_SEC": (60, 31_536_000),
    "STARLINK_AUTO_BACKUP_KEEP_COUNT": (0, 1000),          # 0 = не видаляти старі
    "STARLINK_TELEGRAM_BACKUP_INTERVAL_HOURS": (1, 8760),
    "STARLINK_MAX_FAILURES": (1, 1000),
    "STARLINK_MIN_REBOOT_INTERVAL": (30, 86400),
    "STARLINK_MAX_LOGGED_FAILURES": (0, 10000),
    "STARLINK_REBOOT_SPAM_THRESHOLD": (1, 100),
    "STARLINK_REBOOT_SPAM_WINDOW_SEC": (60, 86400),
    "STARLINK_NOTIFICATIONS_MUTE_AFTER": (0, 2_592_000),
    "STARLINK_TELEGRAM_SEND_TIMEOUT_SEC": (1, 120),
    "STARLINK_TELEGRAM_POLL_TIMEOUT_SEC": (1, 60),
    "STARLINK_TELEGRAM_CONFIRM_TTL_SEC": (10, 3600),
    "STARLINK_TELEGRAM_NOTIFY_TIMEOUT_SEC": (1, 120),
    "STARLINK_TELEGRAM_SEND_RETRIES": (0, 10),
    "STARLINK_TELEGRAM_SEND_RETRY_DELAY_SEC": (0, 60),
    "STARLINK_TELEGRAM_ID_LIST_MAX_ITEMS": (1, 1000),
    "STARLINK_SHUTDOWN_BUTTON_PIN": (0, 27),               # 0 = вимкнено; BCM GPIO Pi Zero 2 W: 0-27
    "STARLINK_SHUTDOWN_BUTTON_HOLD_SEC": (0.5, 60),
    "STARLINK_SHUTDOWN_BUTTON_POLL_INTERVAL_SEC": (0.01, 5),
    "STARLINK_ACTIVITY_LED_PIN": (0, 27),                  # 0 = вимкнено
    "STARLINK_ACTIVITY_LED_BLINK_MS": (1, 5000),
    "STARLINK_DISPLAY_BUTTON_POLL_INTERVAL_SEC": (0.01, 5),
    "STARLINK_DISPLAY_SPI_CS_PIN": (0, 27),
    "STARLINK_DISPLAY_DC_PIN": (0, 27),
    "STARLINK_DISPLAY_RST_PIN": (0, 27),
    "STARLINK_DISPLAY_BL_PIN": (0, 27),                    # 0 = підсвітка прямо на 3.3V
    "STARLINK_DISPLAY_WIDTH": (1, 1000),
    "STARLINK_DISPLAY_HEIGHT": (1, 1000),
    "STARLINK_DISPLAY_ROTATION": (0, 270),
    "STARLINK_DISPLAY_OFFSET_LEFT": (0, 1000),
    "STARLINK_DISPLAY_OFFSET_TOP": (0, 1000),
    "STARLINK_DISPLAY_REFRESH_SEC": (1, 3600),
    "STARLINK_DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC": (0, 30),
    "STARLINK_DISPLAY_SPI_SPEED_HZ": (100_000, 125_000_000),
    "STARLINK_DISPLAY_BACKLIGHT_AUTO_OFF_SEC": (0, 86400),  # 0 = вимкнено
    "STARLINK_DISPLAY_UPDATE_FLASH_SEC": (0, 3600),         # 0 = вимкнено
}
# Параметри з фіксованим набором значень
CHOICES: dict[str, tuple[int, ...]] = {
    "STARLINK_DISPLAY_ROTATION": (0, 90, 180, 270),
}


def _fmt_limit(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else str(n)


def _range_problem(key: Optional[str], number: float) -> Optional[str]:
    """Текст помилки, якщо число поза межами параметра, інакше None."""
    limits = LIMITS.get(key or "")
    if limits is not None and not (limits[0] <= number <= limits[1]):
        return i18n.t("out_of_range", min=_fmt_limit(limits[0]), max=_fmt_limit(limits[1]))
    choices = CHOICES.get(key or "")
    if choices is not None and number not in choices:
        return i18n.t("expected_one_of", values=", ".join(str(c) for c in choices))
    return None


# Адреси (STARLINK_DISH_ADDR/ROUTER_ADDR - єдині вільнотекстові параметри): host:порт, де host - ім'я,
# IPv4 або IPv6 у дужках. Раніше приймався БУДЬ-ЯКИЙ друкований текст (URL, пробіли, лапки,
# зворотний слеш, порт 99999, 300 символів): помилка в адресі -> тарілка/роутер "недоступні"
# назавжди, а лапка чи слеш у кінці рядка за правилами systemd EnvironmentFile міняють розбір
# наступних рядків файлу.
_ADDRESS_KEYS = {"STARLINK_DISH_ADDR", "STARLINK_ROUTER_ADDR"}
_HOST_PORT_RE = re.compile(r"^(?:[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?|\[[0-9A-Fa-f:.]{2,45}\]):(\d{1,5})$")


def _address_problem(value: str) -> Optional[str]:
    match = _HOST_PORT_RE.match(value)
    if not match or not 1 <= int(match.group(1)) <= 65535:
        return i18n.t("invalid_address")
    return None


# Перевірки значення за ТИПОМ параметра: функція повертає текст помилки або None. Раніше це був
# ланцюжок if/elif усередині _validate_value (складність 17, і кожен новий тип чи ключ додавав
# гілку); невідомий тип проходив без перевірки мовчки - тепер тест вимагає, щоб кожен тип
# параметрів з EDITABLE_PARAMS мав запис у _VALIDATORS. ValueError (int("abc")) ловить викликач.
def _check_bool(param: dict[str, Any], value: str) -> Optional[str]:
    return None if value in ("0", "1") else i18n.t("expected_0_or_1")


def _check_number(param: dict[str, Any], value: str) -> Optional[str]:
    number = int(value) if param["type"] == "int" else float(value)
    # isfinite - лише для float: на дуже великому int вона кидає OverflowError (int не має
    # NaN/inf, але має довільну величину)
    if param["type"] == "float" and not math.isfinite(number):
        return i18n.t("not_finite")
    return _range_problem(param.get("key"), number)


def _check_str(param: dict[str, Any], value: str) -> Optional[str]:
    if param.get("key") in _ADDRESS_KEYS:
        return _address_problem(value)
    return None          # інший "str" - без додаткової перевірки


_VALIDATORS: dict[str, Callable[[dict[str, Any], str], Optional[str]]] = {
    "bool": _check_bool,
    "int": _check_number,
    "float": _check_number,
    "str": _check_str,
}


def _validate_value(param: dict[str, Any], raw_value: str) -> tuple[bool, Optional[str]]:
    """Перевіряє значення проти заявленого типу. Повертає (ok, error_or_value)."""
    t = param["type"]
    if not isinstance(raw_value, str):  # напр. число з JSON - дашборд надсилає рядки
        return False, i18n.t("expected_type", type=t)
    v = raw_value.strip()
    if v == "":
        return True, None  # порожньо = прибрати перевизначення, лишити default
    # Керуючі символи (перенесення рядка, \r, NUL, табуляція...) - файл
    # env рядковий: "\n" всередині значення записав би ДОДАТКОВИЙ параметр
    # в обхід валідації (навіть навмисно прибрані з /settings DB_PATH/
    # WEBUI_HOST), NUL - зламав би розбір EnvironmentFile у systemd.
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        return False, i18n.t("invalid_control_chars")
    check = _VALIDATORS.get(t)
    try:
        problem = check(param, v) if check else None
    except ValueError:
        return False, i18n.t("expected_type", type=t)
    return (False, problem) if problem else (True, v)


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
            errors.append(f"{i18n.t(param['label'])}: {value_or_err}")
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
        atomic_write_text(ENV_FILE_PATH, "".join(new_lines))      # не обнуляє файл до запису (обрив живлення)
        return True, "збережено"
    except OSError as e:
        logger.warning("Не вдалося записати %s: %s", ENV_FILE_PATH, e)
        return False, str(e)
