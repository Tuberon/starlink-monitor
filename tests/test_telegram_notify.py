"""
Тести для app/telegram_notify.py - перевірка, що config.TELEGRAM_
NOTIFY_TIMEOUT_SEC (раніше hardcoded module-level константа, винесено
в config.py для консистентності з рештою "усе через env" паттерну
проєкту) реально впливає на HTTP-запит, не лише існує як параметр.
"""
from unittest.mock import MagicMock, patch

from app import config, db, telegram_notify


def test_send_message_uses_configured_timeout(db_path):
    """Реальна регресійна перевірка: після винесення REQUEST_TIMEOUT
    з hardcoded module-level константи в config.TELEGRAM_NOTIFY_
    TIMEOUT_SEC - значення має РЕАЛЬНО дійти до requests.request(),
    не лише існувати в config.py непов'язано."""
    config.TELEGRAM_NOTIFY_TIMEOUT_SEC = 42
    try:
        db.set_setting("telegram_enabled", "1")
        db.set_setting("telegram_bot_token", "FAKE_TOKEN")
        db.set_setting("telegram_chat_ids", "123")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"ok": True}

        with patch("requests.request", return_value=mock_response) as mock_req:
            telegram_notify.send_message("тест")
            assert mock_req.call_args.kwargs.get("timeout") == 42
    finally:
        config.TELEGRAM_NOTIFY_TIMEOUT_SEC = 10
