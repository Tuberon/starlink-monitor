"""
Відправка сповіщень про стан Starlink Mini через Telegram Bot API.

Налаштування (bot token, chat_id) зберігаються в БД (таблиця settings,
керується через веб-інтерфейс) - не в env-файлі, щоб можна було
змінити без перезапуску сервісу.

Bot token отримується в @BotFather, chat_id - id чату/користувача,
якому бот повинен писати (можна дізнатись через @userinfobot, або
через getUpdates після першого повідомлення боту).

Використовується прямий HTTP-виклик Bot API (requests), без сторонньої
бібліотеки python-telegram-bot - вона важка для Pi Zero 2 W і не потрібна
для простої відправки текстових повідомлень.
"""
import logging

import requests

from app import db

logger = logging.getLogger("telegram_notify")

API_BASE = "https://api.telegram.org/bot{token}/{method}"
REQUEST_TIMEOUT = 10


def get_telegram_config():
    """Повертає (token, chat_id, enabled) з БД. chat_id може містити
    кілька id через кому (сповіщення кільком отримувачам)."""
    token = db.get_setting("telegram_bot_token", "") or ""
    chat_ids_raw = db.get_setting("telegram_chat_ids", "") or ""
    enabled = db.get_setting("telegram_enabled", "0") == "1"
    chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
    return token, chat_ids, enabled


def set_telegram_config(token: str = None, chat_ids: list = None, enabled: bool = None):
    if token is not None:
        db.set_setting("telegram_bot_token", token.strip())
    if chat_ids is not None:
        db.set_setting("telegram_chat_ids", ",".join(str(c).strip() for c in chat_ids if str(c).strip()))
    if enabled is not None:
        db.set_setting("telegram_enabled", "1" if enabled else "0")


def send_message(text: str) -> tuple[bool, str]:
    """Надсилає text усім налаштованим chat_id. Ніколи не кидає виняток
    назовні - повертає (успіх, повідомлення). Якщо Telegram вимкнено
    або не налаштовано - тихо повертає (False, причина), не заважаючи
    основному циклу моніторингу."""
    token, chat_ids, enabled = get_telegram_config()

    if not enabled:
        return False, "Telegram сповіщення вимкнені"
    if not token:
        return False, "Не вказано bot token"
    if not chat_ids:
        return False, "Не вказано жодного chat_id"

    url = API_BASE.format(token=token, method="sendMessage")
    errors = []
    any_ok = False
    for chat_id in chat_ids:
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                timeout=REQUEST_TIMEOUT,
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("ok"):
                any_ok = True
            else:
                err_desc = data.get("description", f"HTTP {resp.status_code}")
                errors.append(f"{chat_id}: {err_desc}")
                logger.warning("Telegram sendMessage помилка для %s: %s", chat_id, err_desc)
        except requests.RequestException as e:
            errors.append(f"{chat_id}: {e}")
            logger.warning("Telegram sendMessage мережева помилка для %s: %s", chat_id, e)

    if any_ok and not errors:
        return True, "надіслано"
    if any_ok and errors:
        return True, f"надіслано частково, помилки: {'; '.join(errors)}"
    return False, "; ".join(errors) if errors else "невідома помилка"


def test_connection() -> tuple[bool, str]:
    """Перевіряє валідність bot token через getMe, незалежно від chat_id."""
    token, _, _ = get_telegram_config()
    if not token:
        return False, "Не вказано bot token"
    try:
        resp = requests.get(
            API_BASE.format(token=token, method="getMe"),
            timeout=REQUEST_TIMEOUT,
        )
        data = resp.json()
        if resp.status_code == 200 and data.get("ok"):
            bot_name = data.get("result", {}).get("username", "?")
            return True, f"Бот @{bot_name} доступний"
        return False, data.get("description", f"HTTP {resp.status_code}")
    except requests.RequestException as e:
        return False, str(e)
