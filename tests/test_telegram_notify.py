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


# ---- Retry для мережевих помилок (не для HTTP-рівня відповідей) ----

def _setup_telegram(chat_id="123"):
    db.set_setting("telegram_enabled", "1")
    db.set_setting("telegram_bot_token", "FAKE_TOKEN")
    db.set_setting("telegram_chat_ids", chat_id)


def test_network_error_then_success_retries_and_succeeds(db_path):
    """Реальна мета retry: тимчасова мережева помилка (timeout,
    розрив з'єднання) не має губити сповіщення назавжди, якщо
    повторна спроба вдається."""
    _setup_telegram()
    call_count = [0]

    def fake_request(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            import requests
            raise requests.exceptions.Timeout("timeout")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True}
        return resp

    with patch("requests.request", side_effect=fake_request), patch("time.sleep") as mock_sleep:
        ok, msg = telegram_notify.send_message("тест")

    assert ok is True
    assert call_count[0] == 2
    mock_sleep.assert_called_once()


def test_http_level_error_does_not_retry(db_path):
    """HTTP-рівня відповідь (напр. 'chat not found') - повтор нічого
    не змінить, НЕ має витрачати час на затримку/повторну спробу."""
    _setup_telegram()
    call_count = [0]

    def fake_request(*args, **kwargs):
        call_count[0] += 1
        resp = MagicMock()
        resp.status_code = 400
        resp.json.return_value = {"ok": False, "description": "chat not found"}
        return resp

    with patch("requests.request", side_effect=fake_request), patch("time.sleep") as mock_sleep:
        ok, msg = telegram_notify.send_message("тест")

    assert ok is False
    assert call_count[0] == 1
    mock_sleep.assert_not_called()


def test_all_retries_exhausted_reports_network_error(db_path):
    """Якщо ВСІ спроби (включно з повторними) дають мережеву помилку -
    результат МАЄ це чесно відобразити, не мовчати."""
    _setup_telegram()
    import requests

    with patch("requests.request", side_effect=requests.exceptions.Timeout("timeout")), \
         patch("time.sleep"):
        ok, msg = telegram_notify.send_message("тест")

    assert ok is False
    assert "timeout" in msg.lower() or "123" in msg


def test_retries_respect_configured_count(db_path):
    """config.TELEGRAM_SEND_RETRIES реально визначає кількість спроб,
    не лише існує як параметр."""
    _setup_telegram()
    config.TELEGRAM_SEND_RETRIES = 3
    import requests
    call_count = [0]

    def fake_request(*args, **kwargs):
        call_count[0] += 1
        raise requests.exceptions.Timeout("timeout")

    try:
        with patch("requests.request", side_effect=fake_request), patch("time.sleep"):
            telegram_notify.send_message("тест")
        assert call_count[0] == 4, "1 початкова спроба + 3 повтори = 4 виклики"
    finally:
        config.TELEGRAM_SEND_RETRIES = 1
