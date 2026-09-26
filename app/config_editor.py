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

from app import config, i18n

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


def _validate_value(param: dict, raw_value: str) -> tuple:
    """Перевіряє значення проти заявленого типу. Повертає (ok, error_or_value)."""
    t = param["type"]
    v = raw_value.strip()
    if v == "":
        return True, None  # порожньо = прибрати перевизначення, лишити default
    # Керуючі символи (перенесення рядка, \r, NUL, табуляція...) - файл
    # env рядковий: "\n" всередині значення записав би ДОДАТКОВИЙ параметр
    # в обхід валідації (навіть навмисно прибрані з /settings DB_PATH/
    # WEBUI_HOST), NUL - зламав би розбір EnvironmentFile у systemd.
    if any(ord(c) < 32 or ord(c) == 127 for c in v):
        return False, i18n.t("invalid_control_chars")
    try:
        if t == "int":
            int(v)
        elif t == "float":
            float(v)
        elif t == "bool":
            if v not in ("0", "1"):
                return False, i18n.t("expected_0_or_1")
        # "str" - без додаткової перевірки
        return True, v
    except ValueError:
        return False, i18n.t("expected_type", type=t)


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
        with open(ENV_FILE_PATH, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True, "збережено"
    except OSError as e:
        logger.warning("Не вдалося записати %s: %s", ENV_FILE_PATH, e)
        return False, str(e)
