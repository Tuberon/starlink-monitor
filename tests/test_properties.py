"""Властивісні тести (Hypothesis): чисті функції на довільному вході.

Знайшли помилку, яку приклади не бачили: `version_key("2026.07.20.²")` падав (`str.isdigit()` істинне для "²",
`int()` його не розбирає), і `POST /api/target-versions` віддавав 500. Решта властивостей - запобіжники того ж класу."""
from unittest.mock import patch

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app import config_editor, db, labels, log_redact, telegram_bot

PROFILE = settings(max_examples=250, deadline=None, database=None, suppress_health_check=list(HealthCheck))   # без бази прикладів: не створювати .hypothesis/ у теці проєкту

VERSIONS = st.one_of(
    st.text(max_size=40),
    st.from_regex(r"20[0-9]{2}\.[0-9]{2}\.[0-9]{2}(\.(mr|cr)[0-9]{3,6})?(\.[0-9]{1,3})?", fullmatch=True),
    st.text(alphabet="0123456789.mrc-_ x²①٣", max_size=24),
)


@pytest.mark.parametrize("version", ["2026.07.20.²", "1.②.3", "٣.٤", "x.¼", "²", "2026.07.19.mr82648"])
def test_version_key_survives_unicode_digit_lookalikes(version):
    """Раніше ValueError: isdigit() істинне й для надрядкових/обведених цифр, а int() їх не розбирає."""
    key = db.version_key(version)
    assert key < db.version_key("9999.99.99") or key >= db.version_key("9999.99.99")      # порівнюється без TypeError
    assert db.is_older_version(version, version) is False


def test_non_ascii_digits_are_text_not_numbers():
    assert db.version_key("1.٣")[1][1] == (1, "٣")
    assert db.version_key("1.3")[1][1] == (0, 3)


@PROFILE
@given(VERSIONS, VERSIONS)
def test_version_key_is_comparable_for_any_two_strings(a, b):
    _ = db.version_key(a) < db.version_key(b)


@PROFILE
@given(VERSIONS, VERSIONS)
def test_older_is_irreflexive_and_antisymmetric(a, b):
    assert db.is_older_version(a, a) is False
    assert not (db.is_older_version(a, b) and db.is_older_version(b, a))


@PROFILE
@given(st.text(max_size=80))
def test_version_helpers_never_raise_on_any_text(text):
    db.version_channel(text)
    db.parse_version_list(text)


@PROFILE
@given(st.text(max_size=60), st.text(alphabet="0123456789:ABCdef_-", min_size=8, max_size=30), st.text(max_size=30))
def test_redact_never_leaves_the_token_and_is_idempotent(before, token, after):
    out = log_redact.redact(f"{before}/bot{token}/sendMessage{after}", token)
    assert token not in out
    assert log_redact.redact(out, token) == out


@PROFILE
@given(st.text(max_size=40))
def test_labels_never_raise_and_never_return_non_text(code):
    for fn in (labels.update_state_label, labels.alert_label, labels.router_update_state_label, labels.router_alert_label):
        assert isinstance(fn(code), str)


@PROFILE
@given(st.one_of(st.none(), st.text(max_size=3000)), st.integers(min_value=1, max_value=400))
def test_short_error_respects_the_limit(text, limit):
    result = labels.short_error(text, limit)
    assert isinstance(result, str) and len(result) <= limit + 1


@PROFILE
@given(st.text(max_size=60))
def test_validators_never_raise_for_any_text_and_any_parameter(text):
    for param in config_editor.EDITABLE_PARAMS:
        config_editor._validate_value(param, text)


@PROFILE
@given(st.text(alphabet=st.characters(exclude_categories=["Cs"]), max_size=40))
def test_accepted_string_values_are_safe_for_environment_file(text):
    """systemd розбирає EnvironmentFile: лапки, `\\`, `$`, `%` і керуючі символи в прийнятому значенні змінили б розбір
    наступних рядків. Рядкові параметри (адреси) мають пройти валідатор лише без них."""
    for param in config_editor.EDITABLE_PARAMS:
        if param["type"] != "str":
            continue
        accepted, _ = config_editor._validate_value(param, text)
        if accepted and text.strip():
            assert not any(ch in text.strip() for ch in "\"'\\$%`\n\r"), repr(text)


_JSON = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(min_value=-10**12, max_value=10**12),
              st.floats(allow_nan=False, allow_infinity=False), st.text(max_size=20)),
    lambda inner: st.one_of(st.lists(inner, max_size=4), st.dictionaries(st.text(max_size=10), inner, max_size=5)),
    max_leaves=10)
_UPDATE_KEYS = st.sampled_from(["message", "edited_message", "callback_query", "chat", "id", "text", "data", "from", "update_id"])


@PROFILE
@given(st.dictionaries(_UPDATE_KEYS, _JSON, max_size=6))
def test_update_handlers_survive_arbitrary_json(update):
    """Боту може написати будь-хто: структура update до перевірки авторизації не має кидати винятків."""
    bot = telegram_bot.TelegramBot()
    with patch.object(bot, "_send"), patch.object(telegram_bot, "_api_call", return_value={"ok": True}):
        telegram_bot.TelegramBot._extract_chat_id(update)
        bot._handle_update("tok", {"1"}, update)


@PROFILE
@given(_JSON)
def test_poll_once_survives_an_arbitrary_getupdates_result_and_still_advances(result):
    """Елемент result не з об'єктів (чи result не список) кидав виняток ДО просування _last_update_id."""
    bot = telegram_bot.TelegramBot()
    with patch.object(telegram_bot, "_api_call", return_value={"ok": True, "result": result}), patch.object(bot._executor, "submit"):
        bot._poll_once("tok", {"1"})


def test_a_valid_update_after_a_malformed_one_is_still_processed_and_acknowledged():
    bot = telegram_bot.TelegramBot()
    good = {"update_id": 77, "message": {"chat": {"id": 1}, "text": "/help"}}
    with patch.object(telegram_bot, "_api_call", return_value={"ok": True, "result": [None, 5, "x", good]}), patch.object(bot._executor, "submit") as submit:
        bot._poll_once("tok", {"1"})
    assert bot._last_update_id == 77 and submit.call_count == 1
