"""Мітки станів оновлення й alert-прапорців (app/labels.py): таблиці "код -> ключ перекладу".

Раніше чотири функції були майже-клонами одна одної й при кожному виклику будували повний
словник із 9-21 перекладів заради одного коду; тепер це таблиці на рівні модуля + одна
функція пошуку. Тут - повнота таблиць і поведінка пошуку.
"""
from unittest.mock import patch

import pytest

from app import db, i18n, labels, starlink_client

TABLES = {
    "update_state_label": labels._UPDATE_STATE_KEYS,
    "alert_label": labels._ALERT_KEYS,
    "router_update_state_label": labels._ROUTER_UPDATE_STATE_KEYS,
    "router_alert_label": labels._ROUTER_ALERT_KEYS,
}


@pytest.mark.parametrize("name", list(TABLES))
def test_every_table_key_exists_and_is_translated_in_both_languages(name):
    for code, key in TABLES[name].items():
        entry = i18n.TRANSLATIONS.get(key)
        assert entry and entry["uk"].strip() and entry["en"].strip(), (name, code, key)


def test_every_dish_alert_field_has_a_label():
    assert set(starlink_client.ALERT_FIELD_NAMES) <= set(labels._ALERT_KEYS)


def test_every_router_alert_field_has_a_label():
    assert set(starlink_client.ROUTER_ALERT_FIELD_NAMES) <= set(labels._ROUTER_ALERT_KEYS)


@pytest.mark.parametrize("name", list(TABLES))
def test_unknown_code_is_returned_unchanged(db_path, name):
    assert getattr(labels, name)("SOME_FUTURE_CODE_FROM_NEW_FIRMWARE") == "SOME_FUTURE_CODE_FROM_NEW_FIRMWARE"
    assert getattr(labels, name)("") == ""


@pytest.mark.parametrize("name", list(TABLES))
def test_labels_follow_the_ui_language(db_path, name):
    code, key = next(iter(TABLES[name].items()))
    for lang in ("uk", "en"):
        db.set_setting("ui_language", lang)
        assert getattr(labels, name)(code) == i18n.TRANSLATIONS[key][lang]


def test_lookup_translates_only_the_requested_code(db_path):
    """Раніше кожен виклик перекладав усі 9-21 записів словника."""
    with patch("app.labels.i18n.t", wraps=i18n.t) as translate:
        labels.alert_label("roaming")
        labels.alert_label("UNKNOWN_X")
    assert translate.call_count == 1                 # лише для відомого коду; невідомий - без перекладу


def test_dish_and_router_share_translations_for_common_alerts(db_path):
    """thermal_throttle/install_pending - одні й ті самі слова для dish і роутера (спільні ключі)."""
    for code in ("thermal_throttle", "install_pending"):
        assert labels.alert_label(code) == labels.router_alert_label(code)
