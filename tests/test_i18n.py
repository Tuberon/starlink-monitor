"""Цілісність перекладів і мова автоматичних Telegram-сповіщень."""
import glob
import os
import re
import sqlite3
import time
from pathlib import Path
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
        src = Path(f).read_text(encoding="utf-8")
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
    from app import services
    sent = []
    db.set_setting("dish_target_version", "v2.0")
    services.check_target_version_reached("dish", "v2.0", "dish_target_version", "dish_target_notified", "d1", sent.append)
    assert sent == ["✅ Latest dish update installed: version v2.0"]


def test_firmware_change_message_in_english(en):
    from app import services
    assert services.format_firmware_change_message("router", "r1", "r2") == "🔄 router firmware updated: r1 → r2"


def test_pi_reboot_failure_via_web_in_english(client, en):
    from types import SimpleNamespace
    sent = []
    with patch("app.telegram_notify.send_message", side_effect=lambda t: sent.append(t) or (True, "ok")), \
         patch("app.pi_power.subprocess.run", return_value=SimpleNamespace(returncode=1, stderr="denied", stdout="")), \
         patch("time.sleep"):
        client.post("/api/system-reboot")
    assert any(m.startswith("❌ Failed to reboot Raspberry Pi:") for m in sent), sent


def test_shutdown_button_texts_in_english(en):
    from app import pi_power
    calls = []
    with patch("app.pi_power.execute_pi_power_action", side_effect=lambda *a, **kw: calls.append(a)):
        pi_power.shutdown_from_button(27)
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


# ---- Telegram HTML: динамічний текст екранується, розмітка перекладу - ні ----

_HOSTILE = "<_MultiThreadedRendezvous of RPC & <b>x</b> \"q\">"


def test_tg_keys_escape_string_arguments_but_keep_translation_markup(db_path):
    text = i18n.t("tg_dish_offline_line", error=_HOSTILE)
    assert "&lt;_MultiThreadedRendezvous" in text and "&amp;" in text and "&lt;b&gt;x&lt;/b&gt;" in text
    assert text.startswith("📡 <b>Тарілка</b>")                     # власна розмітка перекладу вціліла


def test_safehtml_and_non_string_arguments_are_not_escaped_or_mangled(db_path):
    ok = i18n.SafeHtml("<code>a&amp;b</code>")
    assert "<code>a&amp;b</code>" in i18n.t("tg_multiple_matches", ids=ok)
    assert i18n.t("tg_alerts_count_line", n=3).endswith("3") or "3" in i18n.t("tg_alerts_count_line", n=3)
    assert "&lt;no space&gt;" in i18n.t("tg_emergency_backup_failed", error=OSError("<no space>"))   # виняток теж екранується


def test_non_telegram_keys_are_not_escaped(db_path):
    """Ключі поза tg_* ідуть у веб/JSON, де HTML-сутності показались б буквально."""
    assert i18n.t("api_sent_partially", errors="<x>") == "надіслано частково, помилки: <x>"


def test_every_telegram_message_is_valid_html_even_with_hostile_values(db_path):
    """Для КОЖНОГО ключа tg_* (і hint-команд) у обох мовах, з ворожими
    значеннями в усіх плейсхолдерах: результат - валідний Telegram-HTML."""
    import re
    from conftest import assert_valid_telegram_html
    keys = [k for k in i18n.TRANSLATIONS if k.startswith("tg_") or k == "telegram_commands_hint"]
    assert len(keys) > 60
    for lang in ("uk", "en"):
        tr = i18n.translator(lang)
        for key in keys:
            template = i18n.TRANSLATIONS[key][lang]
            values = {name: _HOSTILE for name in re.findall(r"\{(\w+)\}", template)}
            assert_valid_telegram_html(tr(key, **values))


def test_hostile_arguments_would_break_html_without_escaping():
    """Контроль самого перевіряльника: без екранування він справді падає."""
    from conftest import assert_valid_telegram_html
    with pytest.raises(AssertionError):
        assert_valid_telegram_html("📡 <b>Тарілка</b>: offline (" + _HOSTILE + ")")


# ---- коротка форма помилки для чату ----

_GRPC_ERROR = (
    '<_MultiThreadedRendezvous of RPC that terminated with:\n\tstatus = StatusCode.UNAVAILABLE\n'
    '\tdetails = "failed to connect to all addresses; last error: UNKNOWN: ipv4:192.168.100.1:9200: '
    'Failed to connect to remote host: Connection refused"\n'
    '\tdebug_error_string = "UNKNOWN:Error received from peer  {grpc_status:14}"\n>'
)


def test_short_error_extracts_grpc_details_and_handles_odd_input():
    from app import labels
    assert labels.short_error(_GRPC_ERROR).startswith("failed to connect to all addresses")
    assert "debug_error_string" not in labels.short_error(_GRPC_ERROR)
    assert labels.short_error("grpcurl не знайдено в PATH") == "grpcurl не знайдено в PATH"
    assert labels.short_error("перший рядок\nдругий рядок") == "перший рядок"
    assert labels.short_error(None) == "" and labels.short_error("") == "" and labels.short_error("  \n ") == ""
    assert len(labels.short_error("x" * 500)) == 200 and labels.short_error("x" * 500).endswith("…")


# ---- фронтенд: жодного вбудованого українського тексту поза t() ----

def _frontend_code_lines():
    """(файл, номер, рядок) коду JS і шаблонів БЕЗ коментарів, Jinja-виразів і console.*"""
    import glob
    import re
    from pathlib import Path
    root = Path(__file__).parent.parent
    for path in sorted(glob.glob(str(root / "static" / "*.js")) + glob.glob(str(root / "templates" / "*.html"))):
        text = Path(path).read_text(encoding="utf-8")
        text = re.sub(r"/\*.*?\*/|\{#.*?#\}|<!--.*?-->", "", text, flags=re.S)
        for number, line in enumerate(text.splitlines(), 1):
            line = re.sub(r"(^|\s)//.*$", "", line)
            line = re.sub(r"\{\{.*?\}\}|\{%.*?%\}", "", line)
            if "console." in line:
                continue
            yield Path(path).name, number, line


def test_frontend_has_no_hardcoded_cyrillic_text():
    """Англійський інтерфейс не має містити українських слів. Раніше таблиця
    клієнтів роутера, "активних попереджень немає", "Backup завантажено",
    одиниці Мбіт/с, дБм, ГГц були вбудовані в JS і шаблон, хоча ключі в
    i18n.py вже існували - просто не викликались."""
    import re
    offenders = [f"{name}:{n}: {line.strip()[:70]}" for name, n, line in _frontend_code_lines()
                 if re.search(r"[А-Яа-яІіЇїЄєҐґ]", line)]
    assert offenders == [], "вбудований український текст у фронтенді (використайте t('ключ')): " + "; ".join(offenders[:6])
