"""Токен Telegram-бота не потрапляє в логи, повідомлення дашборду й БД.

Причина: `requests` вставляє ПОВНИЙ URL запиту в текст винятку, а в Telegram
токен - частина шляху. Мережеві збої з Telegram - основний сценарій проєкту
(Starlink недоступний), тож токен раніше писався в журнал systemd щоразу.
"""
import ast
import glob
import io
import logging
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import requests

from app import config, log_redact, telegram_bot, telegram_notify

TOKEN = "1234567890:" + "A" * 35          # синтетичний токен формату Telegram
URL_ERROR = (f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded with url: "
             f"/bot{TOKEN}/sendMessage (Caused by NameResolutionError(\"Failed to resolve\"))")


# ---- redact() ----

def test_redact_hides_token_in_url_but_keeps_bot_id():
    out = log_redact.redact(URL_ERROR)
    assert "A" * 35 not in out and "/bot1234567890:***/sendMessage" in out


def test_redact_handles_several_tokens_and_is_idempotent():
    text = f"{TOKEN} і ще {TOKEN}"
    once = log_redact.redact(text)
    assert TOKEN not in once and once.count(":***") == 2
    assert log_redact.redact(once) == once


def test_redact_exact_secret_even_when_format_differs():
    """Токен нестандартного вигляду прибирається за точним значенням."""
    assert log_redact.redact("помилка для SECRET-TOKEN-VALUE у запиті", "SECRET-TOKEN-VALUE") == "помилка для *** у запиті"


def test_redact_ignores_too_short_secret_instead_of_mangling_text():
    assert log_redact.redact("Telegram сповіщення вимкнені", "T") == "Telegram сповіщення вимкнені"
    assert log_redact.redact("abc def", "") == "abc def" and log_redact.redact("abc def", None) == "abc def"


@pytest.mark.parametrize("text", [
    "Опитування кожні 10 с", "192.168.100.1:9200", "2026-09-29 16:38:00,008",
    "dish_id ut51c88d90-02724404-198fc3bd", "https://192.168.0.95:8080/healthz",
    "версія 2026.09.14.mr86848", "12345678:short", "chat_id=-1001234567890",
])
def test_redact_leaves_ordinary_text_alone(text):
    assert log_redact.redact(text) == text


# ---- фільтр на справжньому логері ----

def _logger_with_filtered_handler():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    handler.addFilter(log_redact.RedactingFilter())
    logger = logging.getLogger(f"redact-test-{id(stream)}")
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    return logger, stream


def test_filter_redacts_message_args_and_traceback():
    logger, stream = _logger_with_filtered_handler()
    logger.warning("збій: %s", URL_ERROR)
    try:
        raise requests.exceptions.ConnectionError(URL_ERROR)
    except requests.exceptions.ConnectionError:
        logger.exception("виняток у циклі")        # токен у traceback, а не в повідомленні
    out = stream.getvalue()
    assert "A" * 35 not in out
    assert "Traceback" in out and "виняток у циклі" in out       # сам traceback не втрачено


def test_filter_never_breaks_logging_on_odd_records():
    logger, stream = _logger_with_filtered_handler()
    logger.warning("відсоток 100%% і %s", None)
    logger.warning("без аргументів з % символом")
    assert "100%" in stream.getvalue()


def test_install_attaches_filter_once_to_root_handlers():
    root = logging.getLogger()
    handler = logging.StreamHandler(io.StringIO())
    root.addHandler(handler)
    try:
        log_redact.install()
        log_redact.install()
        assert handler.filters.count(log_redact._FILTER) == 1
    finally:
        root.removeHandler(handler)


# ---- наскрізно: справжні шляхи помилок Telegram ----

@pytest.fixture
def no_safety_net():
    """Знімає фільтр із обробників кореневого логера на час тесту. Фільтр
    змінює запис НА МІСЦІ, тож усі наступні обробники (зокрема caplog) бачать
    уже очищений текст - без цієї фікстури тест не відрізнив би "очистили в
    джерелі" від "врятувала захисна сітка"."""
    removed = []
    for handler in logging.getLogger().handlers:
        if log_redact._FILTER in handler.filters:
            handler.removeFilter(log_redact._FILTER)
            removed.append(handler)
    yield
    for handler in removed:
        handler.addFilter(log_redact._FILTER)


@pytest.fixture
def configured_telegram(db_path, monkeypatch):
    telegram_notify.set_telegram_config(token=TOKEN, chat_ids=["1"], enabled=True)
    monkeypatch.setattr(config, "TELEGRAM_SEND_RETRIES", 0)
    with patch("app.telegram_notify._get_eth0_ip", return_value=None), patch("time.sleep"):
        yield


def _boom(*args, **kwargs):
    raise requests.exceptions.ConnectionError(URL_ERROR)


def test_send_message_error_has_no_token_in_result_or_logs(configured_telegram, caplog, no_safety_net):
    with patch("requests.request", side_effect=_boom), caplog.at_level(logging.DEBUG):
        ok, message = telegram_notify.send_message("тест")
    assert ok is False
    assert "A" * 35 not in message and "A" * 35 not in caplog.text
    assert "api.telegram.org" in message                  # корисна частина помилки лишилась


def test_test_connection_error_has_no_token(configured_telegram):
    with patch("requests.request", side_effect=_boom):
        ok, message = telegram_notify.test_connection()
    assert ok is False and "A" * 35 not in message


def test_monitor_warning_about_failed_notification_has_no_token(configured_telegram, caplog, no_safety_net):
    from app.monitor import Watchdog
    with patch("requests.request", side_effect=_boom), caplog.at_level(logging.DEBUG):
        Watchdog()._send_now("x")
    assert "A" * 35 not in caplog.text and "Telegram сповіщення не надіслано" in caplog.text


def test_bot_api_call_failure_has_no_token_in_log(caplog, no_safety_net):
    with patch("requests.request", side_effect=_boom), patch("app.telegram_notify._get_eth0_ip", return_value=None), \
         caplog.at_level(logging.DEBUG):
        assert telegram_bot._api_call("getUpdates", TOKEN, 5) is None
    assert "A" * 35 not in caplog.text


def test_settings_page_test_button_never_shows_token(configured_telegram):
    """Результат кнопки "тест Telegram" на /settings іде в браузер."""
    from app.webapp import app as flask_app
    with patch("requests.request", side_effect=_boom), flask_app.test_client() as client:
        body = client.post("/api/telegram-test").get_data(as_text=True)
    assert "A" * 35 not in body


# ---- статична гарантія: кожна точка входу з basicConfig встановлює фільтр ----

def test_every_entry_point_with_basicconfig_installs_the_filter():
    """AST, а не пошук підрядка: згадка в докстрінгу чи коментарі не рахується."""
    app_dir = os.path.join(os.path.dirname(__file__), "..", "app")

    def calls(tree, obj, attr):
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == attr
                   and isinstance(n.func.value, ast.Name) and n.func.value.id == obj for n in ast.walk(tree))

    entry_points, offenders = [], []
    for path in glob.glob(os.path.join(app_dir, "*.py")):
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        if calls(tree, "logging", "basicConfig"):
            entry_points.append(os.path.basename(path))
            if not calls(tree, "log_redact", "install"):
                offenders.append(os.path.basename(path))
    assert sorted(entry_points) == ["display.py", "monitor.py", "shutdown_button.py", "webapp.py"]
    assert offenders == [], f"додайте log_redact.install() після basicConfig: {offenders}"
