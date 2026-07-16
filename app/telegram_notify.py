"""
Відправка сповіщень через Telegram Bot API (прямий HTTP, без важкої
бібліотеки python-telegram-bot). Налаштування - в БД (settings),
керуються з веб-інтерфейсу без перезапуску сервісу.
"""
import logging
import os
import random

import requests

from app import db

logger = logging.getLogger("telegram_notify")

API_BASE = "https://api.telegram.org/bot{token}/{method}"
REQUEST_TIMEOUT = 10
SIGNATURE_PHRASES_PATH = os.path.join(os.path.dirname(__file__), "signature_phrases.txt")


def _random_signature_phrase() -> str:
    """Повертає випадкову фразу з app/signature_phrases.txt (по одній на
    рядок). Якщо файл відсутній/порожній - повертає порожній рядок,
    щоб не ламати відправку повідомлень."""
    try:
        with open(SIGNATURE_PHRASES_PATH, encoding="utf-8") as f:
            phrases = [line.strip() for line in f if line.strip()]
        return random.choice(phrases) if phrases else ""
    except OSError as e:
        logger.warning("Не вдалося прочитати signature_phrases.txt: %s", e)
        return ""


def get_signature_phrases_text() -> str:
    """Повертає сирий вміст signature_phrases.txt для редагування у
    веб-інтерфейсі (одна фраза на рядок, як у файлі)."""
    try:
        with open(SIGNATURE_PHRASES_PATH, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        logger.warning("Не вдалося прочитати signature_phrases.txt: %s", e)
        return ""


def set_signature_phrases_text(text: str) -> tuple[bool, str]:
    """Записує вміст signature_phrases.txt з веб-інтерфейсу. Порожні рядки
    прибираються, дублікати не перевіряються (можна повторювати фрази).
    Порожній результат (жодної непорожньої фрази) відхиляється - інакше
    send_message лишиться зовсім без фраз."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False, "Потрібна хоча б одна непорожня фраза"
    try:
        with open(SIGNATURE_PHRASES_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        return True, f"Збережено {len(lines)} фраз(и)"
    except OSError as e:
        logger.warning("Не вдалося записати signature_phrases.txt: %s", e)
        return False, str(e)


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

    phrase = _random_signature_phrase()
    full_text = f"{text}\n\n{phrase}" if phrase else text

    url = API_BASE.format(token=token, method="sendMessage")
    errors = []
    any_ok = False
    for chat_id in chat_ids:
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": full_text, "parse_mode": "HTML"},
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
