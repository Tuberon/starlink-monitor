"""Очищення секретів (токен Telegram-бота) із текстів помилок і логів. `requests` вставляє у виняток ПОВНИЙ
URL, а токен — частина шляху (`/bot<токен>/sendMessage`); мережеві збої Telegram — якраз сценарій
проєкту, тож токен писався в журнал systemd на SD (по 4 рядки на відправку) і повертався в браузер
кнопкою "тест Telegram". Два шари: 1. `redact()` у місцях формування/повернення тексту помилки; 2.
`install()` вішає фільтр на обробники кореневого логера (усі записи, включно з traceback). Без важких
імпортів: його підключають і процеси дисплея й кнопки.
"""
import logging
import re
import traceback
from typing import Optional

# Формат токена Telegram: <числовий id бота>:<35 символів [A-Za-z0-9_-]>.
# Числовий id бота не секрет і лишається (видно, про якого бота мова).
_TOKEN_RE = re.compile(r"(\d{6,}):[A-Za-z0-9_-]{30,}")
_MIN_SECRET_LEN = 8       # коротші точні секрети не підставляємо - зіпсували б звичайний текст


def redact(text: str, *secrets: Optional[str]) -> str:
    """Замінює токен(и) на `***`: за форматом Telegram і за точним значенням."""
    text = _TOKEN_RE.sub(r"\1:***", text)
    for secret in secrets:
        if secret and len(secret) >= _MIN_SECRET_LEN:
            text = text.replace(secret, "***")
    return text


class RedactingFilter(logging.Filter):
    """Фільтр обробника: очищає повідомлення і traceback кожного запису."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            cleaned = redact(message)
            if cleaned != message:
                record.msg, record.args = cleaned, ()
            if record.exc_info and not record.exc_text:
                record.exc_text = "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
            if record.exc_text:
                record.exc_text = redact(record.exc_text)
        except Exception:
            pass     # фільтр логування ніколи не має ламати сам застосунок
        return True


_FILTER = RedactingFilter()


def install() -> None:
    """Викликати одразу після logging.basicConfig(): вішає фільтр на всі
    обробники кореневого логера (по одному разу)."""
    for handler in logging.getLogger().handlers:
        if _FILTER not in handler.filters:
            handler.addFilter(_FILTER)
