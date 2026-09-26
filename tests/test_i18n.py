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
    monitor.check_target_version_reached("тарілки", "v2.0", "dish_target_version", "dish_target_notified", "d1", sent.append)
    assert sent == ["✅ Latest dish update installed: version v2.0"]


def test_firmware_change_message_in_english(en):
    from app import monitor
    assert monitor._format_firmware_change_message("роутера", "r1", "r2") == "🔄 router firmware updated: r1 → r2"


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
