"""Спільні людські назви (мультимовні, app/i18n.py) для enum-станів і alert-прапорців Starlink dish/router:
monitor.py (журнал подій) і telegram_bot.py (відповіді) не дублюють словники. Джерело значень — grpcurl
describe на живому пристрої (див. докстрінг app/starlink_client.py). Реалізовано ФУНКЦІЯМИ, не
module-level dict: watchdog живе тижнями, а зміна мови через /settings має діяти з наступного виклику
(module-level dict закешував би старий переклад при імпорті). Мова читається раз на виклик
(i18n.translator()).
"""

import re
from typing import Optional

from app import i18n

# Стани оновлення роутера, що ПОКАЗУЮТЬСЯ як "немає оновлень" (NOT_RUN) скрізь: дисплей, Telegram
# /status і /checkupdates, бейдж дашборда (static/dashboard.js тримає копію — JS не імпортує Python;
# розбіжність ловить test_router_hidden_states_in_sync_with_dashboard). Це тимчасові хмарні помилки
# SpaceX, роутер повторює сам. Чи писати такий стан у ЖУРНАЛ — окреме рішення:
# monitor.HIDDEN_ROUTER_UPDATE_STATES (лише DOWNLOADING_UPDATE_IMAGE_FAILED;
# GETTING_TARGET_VERSION_FAILED пишеться в журнал, але не шле Telegram — рішення користувача).
def short_error(text: Optional[str], limit: int = 200) -> str:
    """Коротка форма помилки для чату. gRPC-помилка - це багаторядкова
    `<_MultiThreadedRendezvous of RPC ... status = ... details = "..." ...>`
    (сотні символів налагоджувального сміття): лишаємо лише `details`."""
    if not text:
        return ""
    match = re.search(r'details = "(.*?)"\s*\n', text, re.S)
    line = match.group(1) if match else text.strip().splitlines()[0] if text.strip() else ""
    return line if len(line) <= limit else line[: limit - 1] + "…"


ROUTER_STATES_SHOWN_AS_NO_UPDATES = ("DOWNLOADING_UPDATE_IMAGE_FAILED", "GETTING_TARGET_VERSION_FAILED")


# Таблиці "код -> ключ перекладу" на рівні модуля. Раніше кожна з чотирьох функцій нижче при КОЖНОМУ
# виклику будувала повний словник із 9-21 перекладів, щоб знайти один код (і були майже-клонами
# одна одної); тепер переклад береться лише для потрібного коду, а перевірка повноти таблиць
# (кожен ключ існує в обох мовах, кожен alert-прапорець має мітку) - у tests/test_labels.py.
# Стан оновлення dish (enum SpaceX.API.Device.SoftwareUpdateState): код -> ключ перекладу.
_UPDATE_STATE_KEYS = {
    "SOFTWARE_UPDATE_STATE_UNKNOWN": "lbl_us_unknown",
    "IDLE": "lbl_us_idle",
    "FETCHING": "lbl_us_fetching",
    "PRE_CHECK": "lbl_us_pre_check",
    "WRITING": "lbl_us_writing",
    "POST_CHECK": "lbl_us_post_check",
    "REBOOT_REQUIRED": "lbl_us_reboot_required",
    "DISABLED": "lbl_us_disabled",
    "FAULTED": "lbl_us_faulted",
}

# alert-прапорці dish (19 полів message DishAlerts, ті самі, що в starlink_client.ALERT_FIELD_NAMES).
_ALERT_KEYS = {
    "motors_stuck": "lbl_alert_motors_stuck",
    "thermal_shutdown": "lbl_alert_thermal_shutdown",
    "thermal_throttle": "lbl_alert_thermal_throttle",
    "unexpected_location": "lbl_alert_unexpected_location",
    "mast_not_near_vertical": "lbl_alert_mast_not_near_vertical",
    "slow_ethernet_speeds": "lbl_alert_slow_ethernet_speeds",
    "roaming": "lbl_alert_roaming",
    "install_pending": "lbl_alert_install_pending",
    "is_heating": "lbl_alert_is_heating",
    "power_supply_thermal_throttle": "lbl_alert_power_supply_thermal_throttle",
    "is_power_save_idle": "lbl_alert_is_power_save_idle",
    "dbf_telem_stale": "lbl_alert_dbf_telem_stale",
    "low_motor_current": "lbl_alert_low_motor_current",
    "lower_signal_than_predicted": "lbl_alert_lower_signal_than_predicted",
    "slow_ethernet_speeds_100": "lbl_alert_slow_ethernet_speeds_100",
    "obstruction_map_reset": "lbl_alert_obstruction_map_reset",
    "dish_water_detected": "lbl_alert_dish_water_detected",
    "router_water_detected": "lbl_alert_router_water_detected",
    "upsu_router_port_slow": "lbl_alert_upsu_router_port_slow",
    "no_ethernet_link": "lbl_alert_no_ethernet_link",
}

# Стан оновлення роутера (enum WifiSoftwareUpdateState).
_ROUTER_UPDATE_STATE_KEYS = {
    "NOT_RUN": "lbl_rus_not_run",
    "GETTING_TARGET_VERSION": "lbl_rus_getting_target_version",
    "DOWNLOADING_UPDATE_IMAGE": "lbl_rus_downloading_update_image",
    "FLASHING": "lbl_rus_flashing",
    "NO_UPDATE_REQUIRED": "lbl_rus_no_update_required",
    "REBOOT_PENDING": "lbl_rus_reboot_pending",
    "GETTING_TARGET_VERSION_FAILED": "lbl_rus_getting_target_version_failed",
    "GETTING_TARGET_VERSION_EXHAUSTED": "lbl_rus_getting_target_version_exhausted",
    "NO_VALID_ARTIFACT": "lbl_rus_no_valid_artifact",
    "ILLEGAL_ARTIFACT": "lbl_rus_illegal_artifact",
    "DOWNLOADING_UPDATE_IMAGE_FAILED": "lbl_rus_downloading_update_image_failed",
    "DOWNLOADING_UPDATE_IMAGE_EXHAUSTED": "lbl_rus_downloading_update_image_exhausted",
    "FLASHING_FAILED": "lbl_rus_flashing_failed",
}

# alert-прапорці роутера (21 поле message WifiAlerts, ті самі, що в starlink_client.ROUTER_ALERT_FIELD_NAMES).
_ROUTER_ALERT_KEYS = {
    "thermal_throttle": "lbl_alert_thermal_throttle",
    "install_pending": "lbl_alert_install_pending",
    "freshly_fused": "lbl_ralert_freshly_fused",
    "lan_eth_slow_link_10": "lbl_ralert_lan_eth_slow_link_10",
    "lan_eth_slow_link_100": "lbl_ralert_lan_eth_slow_link_100",
    "wan_eth_poor_connection": "lbl_ralert_wan_eth_poor_connection",
    "mesh_topology_changing_often": "lbl_ralert_mesh_topology_changing_often",
    "mesh_unreliable_backhaul": "lbl_ralert_mesh_unreliable_backhaul",
    "radius_missing_process": "lbl_ralert_radius_missing_process",
    "eth_switch_error": "lbl_ralert_eth_switch_error",
    "poe_on_dish_unreachable": "lbl_ralert_poe_on_dish_unreachable",
    "poe_fuse_blown": "lbl_ralert_poe_fuse_blown",
    "poe_router_overcurrent": "lbl_ralert_poe_router_overcurrent",
    "poe_off_current_nominal": "lbl_ralert_poe_off_current_nominal",
    "poe_vin_overvoltage": "lbl_ralert_poe_vin_overvoltage",
    "poe_vin_undervoltage": "lbl_ralert_poe_vin_undervoltage",
    "high_cable_ping_drop_rate": "lbl_ralert_high_cable_ping_drop_rate",
    "sandbox_disabled": "lbl_ralert_sandbox_disabled",
    "only_overflight_blocked": "lbl_ralert_only_overflight_blocked",
    "offline_networks_disabled": "lbl_ralert_offline_networks_disabled",
    "wired_mesh_not_using_wan_iface": "lbl_ralert_wired_mesh_not_using_wan_iface",
}


def _label(table: dict[str, str], code: str) -> str:
    """Переклад за кодом; невідомий код повертається як є (той самий fallback,
    що раніше був у labels.get(code, code))."""
    key = table.get(code)
    return i18n.t(key) if key else code


def update_state_label(code: str) -> str:
    """Людська назва стану оновлення dish (enum SpaceX.API.Device.
    SoftwareUpdateState). Невідомий код повертається як є (той самий
    fallback, що раніше був у LABELS.get(code, code))."""
    return _label(_UPDATE_STATE_KEYS, code)


def alert_label(code: str) -> str:
    """Людська назва alert-прапорця dish (19 полів message DishAlerts,
    ті самі, що й у starlink_client.ALERT_FIELD_NAMES)."""
    return _label(_ALERT_KEYS, code)


def router_update_state_label(code: str) -> str:
    """Людська назва стану оновлення роутера (enum WifiSoftwareUpdateState)."""
    return _label(_ROUTER_UPDATE_STATE_KEYS, code)


def router_alert_label(code: str) -> str:
    """Людська назва alert-прапорця роутера (21 поле message WifiAlerts,
    ті самі, що й у starlink_client.ROUTER_ALERT_FIELD_NAMES)."""
    return _label(_ROUTER_ALERT_KEYS, code)
