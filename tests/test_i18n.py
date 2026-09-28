"""Цілісність перекладів і мова автоматичних Telegram-сповіщень."""
import glob
import os
import re
import sqlite3
import time
from unittest.mock import patch

import pytest

from app import config, db, i18n

APP_DIR = os.path.join(os.path.dirname(__file__), "..", "app")


# ---- цілісність словника ----

def test_placeholders_match_between_languages():
    """Різні плейсхолдери в uk/en: відсутній - інформація тихо зникає,
    зайвий - KeyError у .format() саме в момент сповіщення."""
    bad = []
    for key, v in i18n.TRANSLATIONS.items():
        uk, en = set(re.findall(r"\{(\w+)\}", v["uk"])), set(re.findall(r"\{(\w+)\}", v["en"]))
        if uk != en:
            bad.append(f"{key}: uk={sorted(uk)} en={sorted(en)}")
    assert bad == [], bad


def test_every_i18n_key_used_in_app_code_exists():
    """i18n.t("...")/tr("...") з літералом у app/*.py - ключ має бути в
    словнику, інакше користувач побачить сирий ключ."""
    used = set()
    for f in glob.glob(os.path.join(APP_DIR, "*.py")):
        src = open(f, encoding="utf-8").read()
        used |= set(re.findall(r"""(?:i18n\.t|\btr|\buk)\(\s*["']([a-z_0-9]+)["']""", src))
    assert used, "сканування не знайшло жодного виклику"
    assert sorted(used - set(i18n.TRANSLATIONS)) == []


def test_get_language_never_raises_on_broken_db(tmp_path):
    """Мову читають критичні шляхи (вимкнення Pi кнопкою, сторінки,
    Telegram-бот) - зламана БД не має їх обрушувати."""
    p = tmp_path / "empty.db"
    sqlite3.connect(p).close()          # файл є, таблиць немає
    old = config.DB_PATH
    config.DB_PATH = str(p)
    try:
        assert i18n.get_language() == "uk"
        assert i18n.t("tg_pi_started").startswith("🟢")
    finally:
        config.DB_PATH = old


# ---- англійські сповіщення реальними шляхами коду ----

@pytest.fixture
def client(db_path):
    from app.webapp import app as flask_app
    with flask_app.test_client() as c:
        yield c


@pytest.fixture
def en(db_path):
    db.set_setting("ui_language", "en")


def test_dish_alert_notification_in_english(watchdog, en):
    from app.starlink_client import DishStatus
    watchdog.prev_alerts = set()
    watchdog._log_alerts_change(DishStatus(timestamp=time.time(), online=True, active_alerts=["motors_stuck"]))
    assert watchdog.sent == ["⚠️ New dish alert: motors stuck"]


def test_target_version_notification_in_english(en):
    from app import monitor
    sent = []
    db.set_setting("dish_target_version", "v2.0")
    monitor.check_target_version_reached("dish", "v2.0", "dish_target_version", "dish_target_notified", "d1", sent.append)
    assert sent == ["✅ Latest dish update installed: version v2.0"]


def test_firmware_change_message_in_english(en):
    from app import monitor
    assert monitor._format_firmware_change_message("router", "r1", "r2") == "🔄 router firmware updated: r1 → r2"


def test_pi_reboot_failure_via_web_in_english(client, en):
    from types import SimpleNamespace
    sent = []
    with patch("app.telegram_notify.send_message", side_effect=lambda t: sent.append(t) or (True, "ok")), \
         patch("app.pi_power.subprocess.run", return_value=SimpleNamespace(returncode=1, stderr="denied", stdout="")), \
         patch("time.sleep"):
        client.post("/api/system-reboot")
    assert any(m.startswith("❌ Failed to reboot Raspberry Pi:") for m in sent), sent


def test_shutdown_button_texts_in_english(en):
    from app import shutdown_button
    calls = []
    with patch("app.pi_power.execute_pi_power_action", side_effect=lambda *a, **kw: calls.append(a)):
        shutdown_button._trigger_shutdown(27)
    assert "⏻ Raspberry Pi is shutting down via the physical button (GPIO27)" in calls[0]
    assert "shut down" in calls[0]


def test_ukrainian_default_unchanged(watchdog):
    """Без вибору мови - ті самі українські тексти, що були в коді."""
    from app.starlink_client import DishStatus
    watchdog.prev_alerts = set()
    watchdog._log_alerts_change(DishStatus(timestamp=time.time(), online=True, active_alerts=["motors_stuck"]))
    assert watchdog.sent == ["⚠️ Нове попередження dish: двигуни заклинило"]


# ---- плейсхолдери з іменами параметрів функцій перекладу ----

def test_placeholder_named_like_parameter_does_not_collide(db_path):
    """"Непідтримувана мова: {lang}" падало TypeError: _translate(lang, key,
    **kwargs) отримував lang двічі. Параметри тепер лише позиційні."""
    assert i18n.t("api_unsupported_language", lang="xx") == "Непідтримувана мова: xx"
    assert i18n.translator("en")("api_unsupported_language", lang="xx") == "Unsupported language: xx"


def test_set_language_unsupported_returns_400_not_500(client):
    r = client.post("/api/set-language", json={"lang": "xx"})
    assert r.status_code == 400
    assert "xx" in r.get_json()["message"]


# ---- повідомлення інтерфейсу - мовою інтерфейсу, журнал - українською ----

_CYR = __import__("re").compile("[А-Яа-яІіЇїЄєҐґ]")


def test_target_versions_messages_in_english_journal_in_ukrainian(client, en):
    db.insert_metric({"timestamp": 1.0, "online": True, "software_version": "2026.09.14.mr86848"})
    msgs = [client.post("/api/target-versions", json=b).get_json()["message"] for b in
            ({"dish_target": "2026.09.20.mr90000"}, {"dish_target": "2026.01.01.mr1"}, {}, {"dish_target": ""})]
    assert msgs[0] == "Saved: dish"
    assert msgs[1].startswith("Rejected (older version): dish: 2026.01.01.mr1 (vs ")
    assert msgs[2] == "No changes"
    assert msgs[3] == "Saved: dish (cleared)"
    assert not any(_CYR.search(m) for m in msgs)
    events = [e["message"] for e in db.get_recent_events(10)]
    assert "Очікувані версії прошивок оновлено: тарілка" in events


def test_settings_restore_messages_in_english_journal_in_ukrainian(client, en):
    assert client.post("/api/settings-restore", json={}).get_json()["message"] == "Invalid backup file format"
    r = client.post("/api/settings-restore", json={"format_version": 1, "auto_reboot_enabled": True,
                                                   "dish_target_version": "a"}).get_json()
    assert r["message"] == "Restored: auto-reboot, expected dish version"
    assert client.post("/api/settings-restore", json={"format_version": 1}).get_json()["message"] == "Restored: nothing"
    events = [e["message"] for e in db.get_recent_events(10)]
    assert "Відновлено з backup: auto-reboot, очікувана версія тарілки" in events


def test_telegram_notify_messages_in_english(en):
    from app import telegram_notify
    assert telegram_notify.send_message("x") == (False, "Telegram notifications are disabled")
    telegram_notify.set_telegram_config(token="", chat_ids=["1"], enabled=True)
    assert telegram_notify.test_connection() == (False, "Bot token is not set")
    telegram_notify.set_telegram_config(token="T", chat_ids=["1"], enabled=True)   # конфігурація перевіряється першою
    ok, msg = telegram_notify.send_document("/nonexistent/backup.json", caption="c")
    assert msg == "File not found: /nonexistent/backup.json"
