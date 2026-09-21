"""
Спільні людські назви (з підтримкою мультимовності, app/i18n.py) для
enum-станів і alert-прапорців Starlink dish/router. Винесено в
окремий модуль, щоб monitor.py (журнал подій) і telegram_bot.py
(відповіді на команди) використовували ті самі назви, без
дублювання словників.

Джерело значень - grpcurl describe на живому dish/router (див. докстрінг
app/starlink_client.py).

Реалізовано як ФУНКЦІЇ (не module-level dict-літерали) - watchdog-
процес живе тижнями без перезапуску; якщо мову інтерфейсу змінили
через /settings, наступний виклик МАЄ побачити нову мову негайно.
Module-level dict обчислився б ОДИН раз при імпорті модуля й
закешував старий переклад назавжди (реальна помилка, знайдена й
виправлена одразу після першої версії цього файлу - перед тим, як
вона потрапила в тести)."""

from app import i18n


def update_state_label(code: str) -> str:
    """Людська назва стану оновлення dish (enum SpaceX.API.Device.
    SoftwareUpdateState). Невідомий код повертається як є (той самий
    fallback, що раніше був у LABELS.get(code, code))."""
    labels = {
        "SOFTWARE_UPDATE_STATE_UNKNOWN": i18n.t("lbl_us_unknown"),
        "IDLE": i18n.t("lbl_us_idle"),
        "FETCHING": i18n.t("lbl_us_fetching"),
        "PRE_CHECK": i18n.t("lbl_us_pre_check"),
        "WRITING": i18n.t("lbl_us_writing"),
        "POST_CHECK": i18n.t("lbl_us_post_check"),
        "REBOOT_REQUIRED": i18n.t("lbl_us_reboot_required"),
        "DISABLED": i18n.t("lbl_us_disabled"),
        "FAULTED": i18n.t("lbl_us_faulted"),
    }
    return labels.get(code, code)


def alert_label(code: str) -> str:
    """Людська назва alert-прапорця dish (19 полів message DishAlerts,
    ті самі, що й у starlink_client.ALERT_FIELD_NAMES)."""
    labels = {
        "motors_stuck": i18n.t("lbl_alert_motors_stuck"),
        "thermal_shutdown": i18n.t("lbl_alert_thermal_shutdown"),
        "thermal_throttle": i18n.t("lbl_alert_thermal_throttle"),
        "unexpected_location": i18n.t("lbl_alert_unexpected_location"),
        "mast_not_near_vertical": i18n.t("lbl_alert_mast_not_near_vertical"),
        "slow_ethernet_speeds": i18n.t("lbl_alert_slow_ethernet_speeds"),
        "roaming": i18n.t("lbl_alert_roaming"),
        "install_pending": i18n.t("lbl_alert_install_pending"),
        "is_heating": i18n.t("lbl_alert_is_heating"),
        "power_supply_thermal_throttle": i18n.t("lbl_alert_power_supply_thermal_throttle"),
        "is_power_save_idle": i18n.t("lbl_alert_is_power_save_idle"),
        "dbf_telem_stale": i18n.t("lbl_alert_dbf_telem_stale"),
        "low_motor_current": i18n.t("lbl_alert_low_motor_current"),
        "lower_signal_than_predicted": i18n.t("lbl_alert_lower_signal_than_predicted"),
        "slow_ethernet_speeds_100": i18n.t("lbl_alert_slow_ethernet_speeds_100"),
        "obstruction_map_reset": i18n.t("lbl_alert_obstruction_map_reset"),
        "dish_water_detected": i18n.t("lbl_alert_dish_water_detected"),
        "router_water_detected": i18n.t("lbl_alert_router_water_detected"),
        "upsu_router_port_slow": i18n.t("lbl_alert_upsu_router_port_slow"),
        "no_ethernet_link": i18n.t("lbl_alert_no_ethernet_link"),
    }
    return labels.get(code, code)


def router_update_state_label(code: str) -> str:
    """Людська назва стану оновлення роутера (enum WifiSoftwareUpdateState)."""
    labels = {
        "NOT_RUN": i18n.t("lbl_rus_not_run"),
        "GETTING_TARGET_VERSION": i18n.t("lbl_rus_getting_target_version"),
        "DOWNLOADING_UPDATE_IMAGE": i18n.t("lbl_rus_downloading_update_image"),
        "FLASHING": i18n.t("lbl_rus_flashing"),
        "NO_UPDATE_REQUIRED": i18n.t("lbl_rus_no_update_required"),
        "REBOOT_PENDING": i18n.t("lbl_rus_reboot_pending"),
        "GETTING_TARGET_VERSION_FAILED": i18n.t("lbl_rus_getting_target_version_failed"),
        "GETTING_TARGET_VERSION_EXHAUSTED": i18n.t("lbl_rus_getting_target_version_exhausted"),
        "NO_VALID_ARTIFACT": i18n.t("lbl_rus_no_valid_artifact"),
        "ILLEGAL_ARTIFACT": i18n.t("lbl_rus_illegal_artifact"),
        "DOWNLOADING_UPDATE_IMAGE_FAILED": i18n.t("lbl_rus_downloading_update_image_failed"),
        "DOWNLOADING_UPDATE_IMAGE_EXHAUSTED": i18n.t("lbl_rus_downloading_update_image_exhausted"),
        "FLASHING_FAILED": i18n.t("lbl_rus_flashing_failed"),
    }
    return labels.get(code, code)


def router_alert_label(code: str) -> str:
    """Людська назва alert-прапорця роутера (21 поле message WifiAlerts,
    ті самі, що й у starlink_client.ROUTER_ALERT_FIELD_NAMES)."""
    labels = {
        "thermal_throttle": i18n.t("lbl_alert_thermal_throttle"),
        "install_pending": i18n.t("lbl_alert_install_pending"),
        "freshly_fused": i18n.t("lbl_ralert_freshly_fused"),
        "lan_eth_slow_link_10": i18n.t("lbl_ralert_lan_eth_slow_link_10"),
        "lan_eth_slow_link_100": i18n.t("lbl_ralert_lan_eth_slow_link_100"),
        "wan_eth_poor_connection": i18n.t("lbl_ralert_wan_eth_poor_connection"),
        "mesh_topology_changing_often": i18n.t("lbl_ralert_mesh_topology_changing_often"),
        "mesh_unreliable_backhaul": i18n.t("lbl_ralert_mesh_unreliable_backhaul"),
        "radius_missing_process": i18n.t("lbl_ralert_radius_missing_process"),
        "eth_switch_error": i18n.t("lbl_ralert_eth_switch_error"),
        "poe_on_dish_unreachable": i18n.t("lbl_ralert_poe_on_dish_unreachable"),
        "poe_fuse_blown": i18n.t("lbl_ralert_poe_fuse_blown"),
        "poe_router_overcurrent": i18n.t("lbl_ralert_poe_router_overcurrent"),
        "poe_off_current_nominal": i18n.t("lbl_ralert_poe_off_current_nominal"),
        "poe_vin_overvoltage": i18n.t("lbl_ralert_poe_vin_overvoltage"),
        "poe_vin_undervoltage": i18n.t("lbl_ralert_poe_vin_undervoltage"),
        "high_cable_ping_drop_rate": i18n.t("lbl_ralert_high_cable_ping_drop_rate"),
        "sandbox_disabled": i18n.t("lbl_ralert_sandbox_disabled"),
        "only_overflight_blocked": i18n.t("lbl_ralert_only_overflight_blocked"),
        "offline_networks_disabled": i18n.t("lbl_ralert_offline_networks_disabled"),
        "wired_mesh_not_using_wan_iface": i18n.t("lbl_ralert_wired_mesh_not_using_wan_iface"),
    }
    return labels.get(code, code)
