"""
Тести для app/telegram_bot.py - /checkupdates команда. Викликає ту
саму monitor.check_updates_now(), що веб-кнопка "Перевірити
оновлення" (webapp.py /api/check-updates) - не дублює логіку.
"""
import time
from unittest.mock import patch

from app import db, telegram_bot
from app.starlink_client import DishStatus, RouterInfo


def _dish(software_version: str = "v1", online: bool = True) -> DishStatus:
    return DishStatus(
        timestamp=time.time(), online=online, uptime_s=100,
        dish_id="dish1", hardware_version="rev3", software_version=software_version,
    )


def _router(software_version: str = "r1", online: bool = True) -> RouterInfo:
    return RouterInfo(
        timestamp=time.time(), online=online,
        hardware_version="rev2", software_version=software_version,
    )


def test_check_updates_command_sends_status_message(db_path):
    bot = telegram_bot.TelegramBot()
    sent = []
    with patch.object(bot.client, "get_status", return_value=_dish()), \
         patch.object(bot.client, "get_router_info", return_value=_router()), \
         patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)), \
         patch("app.telegram_notify.send_message"):
        bot._cmd_check_updates("FAKE_TOKEN", "123")

    assert len(sent) == 1
    assert "Перевірка оновлень" in sent[0]
    assert "v1" in sent[0]
    assert "r1" in sent[0]


def test_check_updates_command_updates_known_devices(db_path):
    """Реальна регресійна перевірка: раніше ручна перевірка (веб-
    кнопка) взагалі не записувала known_devices - той самий баг
    міг би повторитись тут, якщо команда НЕ використовує спільну
    monitor.check_updates_now()."""
    bot = telegram_bot.TelegramBot()
    with patch.object(bot.client, "get_status", return_value=_dish()), \
         patch.object(bot.client, "get_router_info", return_value=_router()), \
         patch.object(bot, "_send"), \
         patch("app.telegram_notify.send_message"):
        bot._cmd_check_updates("FAKE_TOKEN", "123")

    known = db.get_known_device("dish1")
    assert known is not None
    assert known["dish_software_version"] == "v1"


def test_check_updates_command_notifies_target_version_match(db_path):
    """Та сама перевірка, що для веб-кнопки - target-версія, яка
    саме збіглась у момент ручної перевірки, реально сповіщає."""
    db.set_setting("dish_target_version", "v1")
    bot = telegram_bot.TelegramBot()
    with patch.object(bot.client, "get_status", return_value=_dish("v1")), \
         patch.object(bot.client, "get_router_info", return_value=_router()), \
         patch.object(bot, "_send"), \
         patch("app.telegram_notify.send_message") as mock_notify:
        bot._cmd_check_updates("FAKE_TOKEN", "123")

    assert mock_notify.called
    assert "v1" in mock_notify.call_args[0][0]


def test_check_updates_command_handles_offline_dish(db_path):
    bot = telegram_bot.TelegramBot()
    sent = []
    with patch.object(bot.client, "get_status", return_value=_dish(online=False)), \
         patch.object(bot.client, "get_router_info", return_value=_router()), \
         patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)), \
         patch("app.telegram_notify.send_message"):
        bot._cmd_check_updates("FAKE_TOKEN", "123")

    assert "offline" in sent[0]


def test_checkupdates_command_dispatches_correctly(db_path):
    """Диспетчер команд (_handle_update) реально розпізнає /checkupdates
    і викликає правильний метод, не потрапляючи в 'Невідома команда'."""
    bot = telegram_bot.TelegramBot()
    update = {"message": {"chat": {"id": 123}, "text": "/checkupdates"}}
    with patch.object(bot, "_cmd_check_updates") as mock_cmd:
        bot._handle_update("FAKE_TOKEN", {"123"}, update)
        mock_cmd.assert_called_once_with("FAKE_TOKEN", "123")


# ---- Реальний баг, знайдений на запиті користувача: /id "не відповідає" ----

def test_api_call_logs_warning_when_telegram_rejects_message(db_path, caplog):
    """Реальна причина бага: раніше _api_call() перевіряла ЛИШЕ
    requests.RequestException (мережеві помилки) - якщо Telegram API
    відхиляв запит (напр. "message is too long", HTTP 400 з валідним
    JSON {"ok": false, ...}), це проходило непоміченим, виглядаючи як
    "команда взагалі не відповідає". Тепер ok=False явно логується."""
    with patch("app.telegram_notify._request_with_eth0_fallback") as mock_req:
        mock_response = type("R", (), {"json": lambda self: {"ok": False, "description": "message is too long"}})()
        mock_req.return_value = mock_response
        with caplog.at_level("WARNING"):
            telegram_bot._api_call("sendMessage", "FAKE_TOKEN", 10, chat_id="123", text="x")
    assert "message is too long" in caplog.text


def test_id_command_list_limited_to_max_items(db_path):
    """Telegram обмежує повідомлення 4096 символами - без явного
    обмеження довгий список known_devices (десятки записів за час
    роботи) міг би бути повністю відхилений API. Перевіряє, що
    список реально обмежується і явно повідомляє про приховані
    записи, не просто мовчки обрізається без пояснення."""
    from app import config
    config.TELEGRAM_ID_LIST_MAX_ITEMS = 3
    try:
        for i in range(10):
            db.upsert_known_device_dish(f"dish-{i:03d}", "rev3", "v1.0")

        bot = telegram_bot.TelegramBot()
        sent = []
        with patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
            bot._cmd_id("FAKE_TOKEN", "123", "")

        assert len(sent) == 1
        assert sent[0].count("<code>") == 3
        assert "і ще 7" in sent[0]
    finally:
        config.TELEGRAM_ID_LIST_MAX_ITEMS = 40


def test_id_command_no_truncation_note_when_under_limit(db_path):
    """Контрольний тест: коли записів МЕНШЕ за ліміт, пояснення про
    приховані записи НЕ з'являється (не вводить в оману)."""
    db.upsert_known_device_dish("dish-only-one", "rev3", "v1.0")
    bot = telegram_bot.TelegramBot()
    sent = []
    with patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._cmd_id("FAKE_TOKEN", "123", "")
    assert "і ще" not in sent[0]
