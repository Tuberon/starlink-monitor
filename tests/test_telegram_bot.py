"""
Тести для app/telegram_bot.py - /checkupdates команда. Викликає ту
саму monitor.check_updates_now(), що веб-кнопка "Перевірити
оновлення" (webapp.py /api/check-updates) - не дублює логіку.
"""
import time
from unittest.mock import patch

import pytest

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


# ---- _api_call() - решта шляхів (успіх, мережева помилка) ----

def test_api_call_returns_data_on_success():
    with patch("app.telegram_notify._request_with_eth0_fallback") as mock_req:
        mock_response = type("R", (), {"json": lambda self: {"ok": True, "result": [1, 2, 3]}})()
        mock_req.return_value = mock_response
        result = telegram_bot._api_call("getUpdates", "FAKE_TOKEN", 10)
    assert result == {"ok": True, "result": [1, 2, 3]}


def test_api_call_returns_none_on_network_error(caplog):
    import requests
    with patch("app.telegram_notify._request_with_eth0_fallback", side_effect=requests.exceptions.Timeout("timeout")):
        with caplog.at_level("WARNING"):
            result = telegram_bot._api_call("sendMessage", "FAKE_TOKEN", 10, chat_id="123", text="x")
    assert result is None
    assert "провалився" in caplog.text


# ---- _extract_chat_id() - чиста логіка, обидва формати update ----

def test_extract_chat_id_from_regular_message():
    update = {"message": {"chat": {"id": 123}}}
    assert telegram_bot.TelegramBot._extract_chat_id(update) == "123"


def test_extract_chat_id_from_callback_query():
    update = {"callback_query": {"message": {"chat": {"id": 456}}}}
    assert telegram_bot.TelegramBot._extract_chat_id(update) == "456"


def test_extract_chat_id_missing_data_returns_empty_string():
    """Реальний edge case: malformed update без chat-поля - не має
    кидати виняток, лише повернути порожній рядок."""
    assert telegram_bot.TelegramBot._extract_chat_id({}) == ""


# ---- _handle_update() - авторизація + dispatch команд (критична, безпекова логіка) ----

def test_handle_update_unauthorized_chat_id_is_rejected_and_notified(db_path):
    bot = telegram_bot.TelegramBot()
    sent = []
    update = {"message": {"chat": {"id": 999}, "text": "/status"}}
    with patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append((c, text))):
        bot._handle_update("FAKE_TOKEN", {"123"}, update)
    assert len(sent) == 1
    assert sent[0][0] == "999"
    assert "не авторизований" in sent[0][1]


def test_handle_update_empty_text_is_ignored(db_path):
    """Реальний сценарій: Telegram-повідомлення без тексту (стікер,
    фото без підпису) - НЕ має викликати жодну команду чи відповідь."""
    bot = telegram_bot.TelegramBot()
    sent = []
    update = {"message": {"chat": {"id": 123}, "text": ""}}
    with patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._handle_update("FAKE_TOKEN", {"123"}, update)
    assert sent == []


def test_handle_update_unknown_command_gets_help_hint(db_path):
    bot = telegram_bot.TelegramBot()
    sent = []
    update = {"message": {"chat": {"id": 123}, "text": "/nonexistent"}}
    with patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._handle_update("FAKE_TOKEN", {"123"}, update)
    assert len(sent) == 1
    assert "/help" in sent[0]


def test_handle_update_strips_botname_suffix(db_path):
    """Реальний Telegram-формат у групових чатах: /status@MyBot -
    суфікс МАЄ прибиратись перед розпізнаванням команди."""
    bot = telegram_bot.TelegramBot()
    called = []
    update = {"message": {"chat": {"id": 123}, "text": "/help@SomeBotName"}}
    with patch.object(bot, "_cmd_help", side_effect=lambda t, c: called.append(c)):
        bot._handle_update("FAKE_TOKEN", {"123"}, update)
    assert called == ["123"]


def test_handle_update_dispatches_reboot_command(db_path):
    bot = telegram_bot.TelegramBot()
    called = []
    update = {"message": {"chat": {"id": 123}, "text": "/reboot"}}
    with patch.object(bot, "_cmd_reboot_request", side_effect=lambda t, c: called.append(c)):
        bot._handle_update("FAKE_TOKEN", {"123"}, update)
    assert called == ["123"]


def test_handle_update_id_command_passes_argument(db_path):
    bot = telegram_bot.TelegramBot()
    called = []
    update = {"message": {"chat": {"id": 123}, "text": "/id dish-42"}}
    with patch.object(bot, "_cmd_id", side_effect=lambda t, c, arg: called.append(arg)):
        bot._handle_update("FAKE_TOKEN", {"123"}, update)
    assert called == ["dish-42"]


def test_handle_update_callback_query_routes_separately(db_path):
    """callback_query (inline-кнопка) МАЄ йти в _handle_callback(),
    не в звичайний message-dispatch."""
    bot = telegram_bot.TelegramBot()
    called = []
    update = {"callback_query": {"data": "reboot_cancel"}}
    with patch.object(bot, "_handle_callback", side_effect=lambda t, a, cb: called.append(cb)):
        bot._handle_update("FAKE_TOKEN", {"123"}, update)
    assert called == [{"data": "reboot_cancel"}]


# ---- _handle_callback() - reboot_confirm/cancel з TTL-захистом (реальна фізична дія) ----

def test_callback_reboot_confirm_within_ttl_executes_reboot(db_path):
    bot = telegram_bot.TelegramBot()
    bot._pending_reboot_confirm["123"] = time.time()  # щойно запитано
    sent = []
    callback = {"message": {"chat": {"id": 123}}, "data": "reboot_confirm", "id": "cb1"}
    with patch.object(bot.client, "reboot_dish", return_value=(True, "ok")), \
         patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)), \
         patch("app.telegram_bot._api_call"):
        bot._handle_callback("FAKE_TOKEN", {"123"}, callback)
    assert any("успішно" in s for s in sent)
    assert "123" not in bot._pending_reboot_confirm  # pending-стан прибраний


def test_callback_reboot_confirm_expired_ttl_does_not_reboot(db_path):
    """Реальна мета TTL-захисту: підтвердження, надіслане ПІСЛЯ
    TELEGRAM_CONFIRM_TTL_SEC - НЕ має реально виконати reboot Starlink
    Mini (фізична дія на реальному обладнанні)."""
    from app import config
    bot = telegram_bot.TelegramBot()
    bot._pending_reboot_confirm["123"] = time.time() - config.TELEGRAM_CONFIRM_TTL_SEC - 10
    sent = []
    reboot_called = []
    callback = {"message": {"chat": {"id": 123}}, "data": "reboot_confirm", "id": "cb1"}
    with patch.object(bot.client, "reboot_dish", side_effect=lambda: reboot_called.append(1)), \
         patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)), \
         patch("app.telegram_bot._api_call"):
        bot._handle_callback("FAKE_TOKEN", {"123"}, callback)
    assert reboot_called == [], "reboot_dish() НЕ мав викликатись для застарілого підтвердження"
    assert any("застарів" in s for s in sent)


def test_callback_reboot_confirm_without_pending_request_does_not_reboot(db_path):
    """Реальний edge case: підтвердження без попереднього /reboot
    (напр. повторний клік на старе повідомлення після рестарту бота,
    коли _pending_reboot_confirm скинувся) - НЕ має виконувати reboot."""
    bot = telegram_bot.TelegramBot()
    sent = []
    reboot_called = []
    callback = {"message": {"chat": {"id": 123}}, "data": "reboot_confirm", "id": "cb1"}
    with patch.object(bot.client, "reboot_dish", side_effect=lambda: reboot_called.append(1)), \
         patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)), \
         patch("app.telegram_bot._api_call"):
        bot._handle_callback("FAKE_TOKEN", {"123"}, callback)
    assert reboot_called == []
    assert any("застарів" in s for s in sent)


def test_callback_reboot_cancel_clears_pending_without_reboot(db_path):
    bot = telegram_bot.TelegramBot()
    bot._pending_reboot_confirm["123"] = time.time()
    sent = []
    reboot_called = []
    callback = {"message": {"chat": {"id": 123}}, "data": "reboot_cancel", "id": "cb1"}
    with patch.object(bot.client, "reboot_dish", side_effect=lambda: reboot_called.append(1)), \
         patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)), \
         patch("app.telegram_bot._api_call"):
        bot._handle_callback("FAKE_TOKEN", {"123"}, callback)
    assert reboot_called == []
    assert "123" not in bot._pending_reboot_confirm
    assert any("Скасовано" in s for s in sent)


def test_callback_unauthorized_chat_id_is_rejected(db_path):
    bot = telegram_bot.TelegramBot()
    api_calls = []
    callback = {"message": {"chat": {"id": 999}}, "data": "reboot_confirm", "id": "cb1"}
    with patch("app.telegram_bot._api_call", side_effect=lambda *a, **kw: api_calls.append(kw)):
        bot._handle_callback("FAKE_TOKEN", {"123"}, callback)
    assert len(api_calls) == 1
    assert "Не авторизовано" in api_calls[0].get("text", "")


# ---- _cmd_status() - найчастіше використовувана команда ----

def test_cmd_status_both_online_shows_full_info(db_path):
    bot = telegram_bot.TelegramBot()
    sent = []
    dish = _dish(software_version="v1.2.3")
    router = _router(software_version="r4.5.6")
    with patch.object(bot.client, "get_status", return_value=dish), \
         patch.object(bot.client, "get_router_info", return_value=router), \
         patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._cmd_status("FAKE_TOKEN", "123")
    assert len(sent) == 1
    assert "v1.2.3" in sent[0]
    assert "r4.5.6" in sent[0]
    assert "online" in sent[0]


def test_cmd_status_dish_offline_shows_error(db_path):
    bot = telegram_bot.TelegramBot()
    sent = []
    dish = DishStatus(timestamp=time.time(), online=False, error="timeout")
    router = _router()
    with patch.object(bot.client, "get_status", return_value=dish), \
         patch.object(bot.client, "get_router_info", return_value=router), \
         patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._cmd_status("FAKE_TOKEN", "123")
    assert "offline" in sent[0]
    assert "timeout" in sent[0]


def test_cmd_status_shows_active_alerts_count(db_path):
    bot = telegram_bot.TelegramBot()
    sent = []
    dish = _dish()
    dish.active_alerts = ["thermal_throttle", "motors_stuck"]
    router = _router()
    with patch.object(bot.client, "get_status", return_value=dish), \
         patch.object(bot.client, "get_router_info", return_value=router), \
         patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._cmd_status("FAKE_TOKEN", "123")
    assert "Попереджень: 2" in sent[0]


# ---- _cmd_id() з аргументом - пошук конкретної тарілки ----

def test_cmd_id_exact_match(db_path):
    db.upsert_known_device_dish("dish-exact-123", "rev3", "v1.0")
    bot = telegram_bot.TelegramBot()
    sent = []
    with patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._cmd_id("FAKE_TOKEN", "123", "dish-exact-123")
    assert "dish-exact-123" in sent[0]
    assert "v1.0" in sent[0]


def test_cmd_id_unique_partial_match(db_path):
    db.upsert_known_device_dish("dish-unique-abc", "rev3", "v1.0")
    bot = telegram_bot.TelegramBot()
    sent = []
    with patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._cmd_id("FAKE_TOKEN", "123", "unique")
    assert "dish-unique-abc" in sent[0]


def test_cmd_id_ambiguous_partial_match_lists_candidates(db_path):
    db.upsert_known_device_dish("dish-abc-1", "rev3", "v1.0")
    db.upsert_known_device_dish("dish-abc-2", "rev3", "v1.0")
    bot = telegram_bot.TelegramBot()
    sent = []
    with patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._cmd_id("FAKE_TOKEN", "123", "abc")
    assert "dish-abc-1" in sent[0]
    assert "dish-abc-2" in sent[0]
    assert "кілька" in sent[0]


def test_cmd_id_no_match_reports_not_found(db_path):
    bot = telegram_bot.TelegramBot()
    sent = []
    with patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._cmd_id("FAKE_TOKEN", "123", "nonexistent-id")
    assert "не знайдено" in sent[0]


# ---- _fmt_ago() - чиста логіка, усі часові діапазони ----

def test_fmt_ago_zero_timestamp_is_unknown(db_path):
    assert telegram_bot.TelegramBot._fmt_ago(0) == "невідомо"


def test_fmt_ago_recent_is_just_now(db_path):
    assert telegram_bot.TelegramBot._fmt_ago(time.time() - 5) == "щойно"


def test_fmt_ago_minutes(db_path):
    result = telegram_bot.TelegramBot._fmt_ago(time.time() - 300)
    assert "хв тому" in result


def test_fmt_ago_hours(db_path):
    result = telegram_bot.TelegramBot._fmt_ago(time.time() - 7200)
    assert "год тому" in result


def test_fmt_ago_days(db_path):
    result = telegram_bot.TelegramBot._fmt_ago(time.time() - 3 * 86400)
    assert "3 дн тому" == result


# ---- Решта простих, але не покритих сценаріїв ----

def test_cmd_status_shows_router_alerts_count(db_path):
    """Контрольний тест: router-попередження (окрема гілка від
    dish-попереджень, перевірених вище) теж реально показуються."""
    bot = telegram_bot.TelegramBot()
    sent = []
    dish = _dish()
    router = _router()
    router.active_alerts = ["thermal_shutdown"]
    with patch.object(bot.client, "get_status", return_value=dish), \
         patch.object(bot.client, "get_router_info", return_value=router), \
         patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._cmd_status("FAKE_TOKEN", "123")
    assert "Попереджень: 1" in sent[0]


def test_cmd_reboot_request_sets_pending_and_sends_confirmation(db_path):
    bot = telegram_bot.TelegramBot()
    api_calls = []
    with patch("app.telegram_bot._api_call", side_effect=lambda *a, **kw: api_calls.append(kw)):
        bot._cmd_reboot_request("FAKE_TOKEN", "123")
    assert "123" in bot._pending_reboot_confirm
    assert len(api_calls) == 1
    assert "Перезавантажити" in api_calls[0].get("text", "")


def test_cmd_help_lists_all_commands(db_path):
    bot = telegram_bot.TelegramBot()
    sent = []
    with patch.object(bot, "_send", side_effect=lambda t, c, text: sent.append(text)):
        bot._cmd_help("FAKE_TOKEN", "123")
    for cmd in ("/status", "/checkupdates", "/reboot", "/id", "/help"):
        assert cmd in sent[0]


# ---- Memory leak fix: застарілі pending_reboot_confirm записи ----

def test_reboot_request_cleans_up_expired_pending_from_other_chats(db_path):
    """Реальний memory leak, знайдений під час аудиту оптимізації:
    якщо chat_id A робить /reboot і ІГНОРУЄ inline-кнопки (не тисне
    ні "підтвердити", ні "скасувати") - запис лишався б у пам'яті
    watchdog-процесу назавжди (лише _handle_callback() видаляв
    запис, а без callback його ніхто не викликає). Новий /reboot
    від БУДЬ-ЯКОГО chat_id тепер прибирає застарілі (TTL минув)
    записи з УСІХ chat_id, не лише поточного."""
    from app import config
    bot = telegram_bot.TelegramBot()
    with patch("app.telegram_bot._api_call"):
        bot._pending_reboot_confirm["stale-user"] = time.time() - config.TELEGRAM_CONFIRM_TTL_SEC - 10
        bot._cmd_reboot_request("FAKE_TOKEN", "new-user")
    assert "stale-user" not in bot._pending_reboot_confirm
    assert "new-user" in bot._pending_reboot_confirm


def test_reboot_request_does_not_remove_still_valid_pending(db_path):
    """Контрольний тест: pending-запис, для якого TTL ЩЕ не минув,
    НЕ має видалятись новим /reboot-запитом від іншого chat_id."""
    from app import config
    bot = telegram_bot.TelegramBot()
    with patch("app.telegram_bot._api_call"):
        bot._pending_reboot_confirm["still-valid-user"] = time.time()
        bot._cmd_reboot_request("FAKE_TOKEN", "new-user")
    assert "still-valid-user" in bot._pending_reboot_confirm


# ---- _poll_once() - groups updates за chat_id, submit до executor ----

def test_poll_once_no_data_sleeps_and_returns(db_path):
    bot = telegram_bot.TelegramBot()
    with patch("app.telegram_bot._api_call", return_value=None), \
         patch("time.sleep") as mock_sleep:
        bot._poll_once("TOKEN", {"123"})
    mock_sleep.assert_called_once_with(3)


def test_poll_once_api_not_ok_sleeps_and_returns(db_path):
    bot = telegram_bot.TelegramBot()
    with patch("app.telegram_bot._api_call", return_value={"ok": False}), \
         patch("time.sleep") as mock_sleep:
        bot._poll_once("TOKEN", {"123"})
    mock_sleep.assert_called_once_with(3)


def test_poll_once_updates_last_update_id(db_path):
    bot = telegram_bot.TelegramBot()
    data = {"ok": True, "result": [
        {"update_id": 100, "message": {"chat": {"id": 123}, "text": "/status"}},
        {"update_id": 105, "message": {"chat": {"id": 123}, "text": "/help"}},
    ]}
    with patch("app.telegram_bot._api_call", return_value=data), \
         patch.object(bot._executor, "submit"):
        bot._poll_once("TOKEN", {"123"})
    assert bot._last_update_id == 105


def test_poll_once_groups_updates_by_chat_id(db_path):
    """Реальна мета групування: updates того самого чату обробляються
    послідовно в ОДНОМУ виклику _handle_updates_sequential (гарантує
    порядок), різні чати - окремими submit-викликами (паралельно)."""
    bot = telegram_bot.TelegramBot()
    data = {"ok": True, "result": [
        {"update_id": 1, "message": {"chat": {"id": 111}, "text": "/status"}},
        {"update_id": 2, "message": {"chat": {"id": 222}, "text": "/status"}},
        {"update_id": 3, "message": {"chat": {"id": 111}, "text": "/help"}},
    ]}
    submitted = []
    with patch("app.telegram_bot._api_call", return_value=data), \
         patch.object(bot._executor, "submit", side_effect=lambda fn, token, chats, updates: submitted.append((fn, updates))):
        bot._poll_once("TOKEN", {"111", "222"})

    assert len(submitted) == 2  # 2 окремих чати - 2 окремих submit
    chat_111_updates = next(u for fn, u in submitted if len(u) == 2)
    assert len(chat_111_updates) == 2  # обидва update чату 111 разом, в одному виклику


# ---- _handle_updates_sequential() ----

def test_handle_updates_sequential_processes_all_updates_in_order(db_path):
    bot = telegram_bot.TelegramBot()
    processed = []
    updates = [
        {"message": {"chat": {"id": 123}, "text": "/status"}},
        {"message": {"chat": {"id": 123}, "text": "/help"}},
    ]
    with patch.object(bot, "_handle_update", side_effect=lambda t, c, u: processed.append(u)):
        bot._handle_updates_sequential("TOKEN", {"123"}, updates)
    assert processed == updates


def test_handle_updates_sequential_one_failure_does_not_stop_the_rest(db_path):
    """Реальна мета: помилка обробки ОДНОГО update (напр. несподіваний
    формат) НЕ має зупинити обробку решти updates у тій самій групі -
    інакше одна погана команда заблокувала б усі наступні для того
    самого chat_id."""
    bot = telegram_bot.TelegramBot()
    processed = []
    updates = [
        {"message": {"chat": {"id": 123}, "text": "/broken"}},
        {"message": {"chat": {"id": 123}, "text": "/help"}},
    ]

    def flaky_handle(token, chats, update):
        if update["message"]["text"] == "/broken":
            raise RuntimeError("несподівана помилка")
        processed.append(update)

    with patch.object(bot, "_handle_update", side_effect=flaky_handle):
        bot._handle_updates_sequential("TOKEN", {"123"}, updates)
    assert len(processed) == 1  # /help реально оброблений, попри провал /broken


# ---- _run_loop() - головний polling-цикл, зупиняється через SystemExit ----

def test_run_loop_enabled_calls_poll_once(db_path):
    bot = telegram_bot.TelegramBot()
    poll_calls = []

    def fake_poll_once(token, chats):
        poll_calls.append((token, chats))
        raise SystemExit()

    with patch("app.telegram_notify.get_telegram_config", return_value=("TOKEN", ["123"], True)), \
         patch.object(bot, "_poll_once", side_effect=fake_poll_once):
        with pytest.raises(SystemExit):
            bot._run_loop()

    assert poll_calls == [("TOKEN", {"123"})]


def test_run_loop_disabled_sleeps_without_polling(db_path):
    """Реальна мета: бот вимкнений/не налаштований (немає token) -
    НЕ опитує API даремно, лише періодично перевіряє, чи налаштування
    з'явились."""
    bot = telegram_bot.TelegramBot()
    poll_calls = []

    with patch("app.telegram_notify.get_telegram_config", return_value=("", [], False)), \
         patch.object(bot, "_poll_once", side_effect=lambda t, c: poll_calls.append(1)), \
         patch("time.sleep", side_effect=SystemExit()):
        with pytest.raises(SystemExit):
            bot._run_loop()

    assert poll_calls == []


def test_run_loop_poll_once_exception_is_caught_and_loop_continues(db_path):
    """Реальна мета: неочікувана помилка всередині _poll_once() (напр.
    Telegram API повернув щось несподіване) НЕ має завершити весь
    бот-потік - цикл продовжується до наступної ітерації."""
    bot = telegram_bot.TelegramBot()
    call_count = {"n": 0}

    def flaky_poll_once(token, chats):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("несподівана помилка Telegram API")
        raise SystemExit()

    with patch("app.telegram_notify.get_telegram_config", return_value=("TOKEN", ["123"], True)), \
         patch.object(bot, "_poll_once", side_effect=flaky_poll_once), \
         patch("time.sleep"):
        with pytest.raises(SystemExit):
            bot._run_loop()

    assert call_count["n"] == 2  # цикл реально продовжився після помилки


# ---- start()/stop() ----

def test_start_creates_daemon_thread(db_path):
    bot = telegram_bot.TelegramBot()
    with patch.object(bot, "_run_loop"):
        bot.start()
    assert bot._thread is not None
    assert bot._thread.daemon is True
    bot.stop()


def test_start_is_idempotent_does_not_create_second_thread(db_path):
    """Реальна мета: повторний виклик start() (напр. помилковий
    подвійний виклик) НЕ має створити другий потік - лише один
    polling-цикл має працювати одночасно."""
    bot = telegram_bot.TelegramBot()
    with patch.object(bot, "_run_loop"):
        bot.start()
        first_thread = bot._thread
        bot.start()
    assert bot._thread is first_thread
    bot.stop()


def test_stop_sets_stop_event_and_shuts_down_executor(db_path):
    bot = telegram_bot.TelegramBot()
    with patch.object(bot._executor, "shutdown") as mock_shutdown:
        bot.stop()
    assert bot._stop_event.is_set()
    mock_shutdown.assert_called_once_with(wait=False)
