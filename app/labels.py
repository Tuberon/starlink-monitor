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
вона потрапила в тести). Мова читається ОДИН раз на виклик функції
(i18n.translator()), не на кожен із ~20 рядків словника."""

from app import i18n


def update_state_label(code: str) -> str:
    """Людська назва стану оновлення dish (enum SpaceX.API.Device.
    SoftwareUpdateState). Невідомий код повертається як є (той самий
    fallback, що раніше був у LABELS.get(code, code))."""
    tr = i18n.translator()
    labels = {
        "SOFTWARE_UPDATE_STATE_UNKNOWN": tr("lbl_us_unknown"),
        "IDLE": tr("lbl_us_idle"),
        "FETCHING": tr("lbl_us_fetching"),
        "PRE_CHECK": tr("lbl_us_pre_check"),
        "WRITING": tr("lbl_us_writing"),
        "POST_CHECK": tr("lbl_us_post_check"),
        "REBOOT_REQUIRED": tr("lbl_us_reboot_required"),
        "DISABLED": tr("lbl_us_disabled"),
        "FAULTED": tr("lbl_us_faulted"),
    }
    return labels.get(code, code)


def alert_label(code: str) -> str:
    """Людська назва alert-прапорця dish (19 полів message DishAlerts,
    ті самі, що й у starlink_client.ALERT_FIELD_NAMES)."""
    tr = i18n.translator()
    labels = {
        "motors_stuck": tr("lbl_alert_motors_stuck"),
        "thermal_shutdown": tr("lbl_alert_thermal_shutdown"),
        "thermal_throttle": tr("lbl_alert_thermal_throttle"),
        "unexpected_location": tr("lbl_alert_unexpected_location"),
        "mast_not_near_vertical": tr("lbl_alert_mast_not_near_vertical"),
        "slow_ethernet_speeds": tr("lbl_alert_slow_ethernet_speeds"),
        "roaming": tr("lbl_alert_roaming"),
        "install_pending": tr("lbl_alert_install_pending"),
        "is_heating": tr("lbl_alert_is_heating"),
        "power_supply_thermal_throttle": tr("lbl_alert_power_supply_thermal_throttle"),
        "is_power_save_idle": tr("lbl_alert_is_power_save_idle"),
        "dbf_telem_stale": tr("lbl_alert_dbf_telem_stale"),
        "low_motor_current": tr("lbl_alert_low_motor_current"),
        "lower_signal_than_predicted": tr("lbl_alert_lower_signal_than_predicted"),
        "slow_ethernet_speeds_100": tr("lbl_alert_slow_ethernet_speeds_100"),
        "obstruction_map_reset": tr("lbl_alert_obstruction_map_reset"),
        "dish_water_detected": tr("lbl_alert_dish_water_detected"),
        "router_water_detected": tr("lbl_alert_router_water_detected"),
        "upsu_router_port_slow": tr("lbl_alert_upsu_router_port_slow"),
        "no_ethernet_link": tr("lbl_alert_no_ethernet_link"),
    }
    return labels.get(code, code)


def router_update_state_label(code: str) -> str:
    """Людська назва стану оновлення роутера (enum WifiSoftwareUpdateState)."""
    tr = i18n.translator()
    labels = {
        "NOT_RUN": tr("lbl_rus_not_run"),
        "GETTING_TARGET_VERSION": tr("lbl_rus_getting_target_version"),
        "DOWNLOADING_UPDATE_IMAGE": tr("lbl_rus_downloading_update_image"),
        "FLASHING": tr("lbl_rus_flashing"),
        "NO_UPDATE_REQUIRED": tr("lbl_rus_no_update_required"),
        "REBOOT_PENDING": tr("lbl_rus_reboot_pending"),
        "GETTING_TARGET_VERSION_FAILED": tr("lbl_rus_getting_target_version_failed"),
        "GETTING_TARGET_VERSION_EXHAUSTED": tr("lbl_rus_getting_target_version_exhausted"),
        "NO_VALID_ARTIFACT": tr("lbl_rus_no_valid_artifact"),
        "ILLEGAL_ARTIFACT": tr("lbl_rus_illegal_artifact"),
        "DOWNLOADING_UPDATE_IMAGE_FAILED": tr("lbl_rus_downloading_update_image_failed"),
        "DOWNLOADING_UPDATE_IMAGE_EXHAUSTED": tr("lbl_rus_downloading_update_image_exhausted"),
        "FLASHING_FAILED": tr("lbl_rus_flashing_failed"),
    }
    return labels.get(code, code)


def router_alert_label(code: str) -> str:
    """Людська назва alert-прапорця роутера (21 поле message WifiAlerts,
    ті самі, що й у starlink_client.ROUTER_ALERT_FIELD_NAMES)."""
    tr = i18n.translator()
    labels = {
        "thermal_throttle": tr("lbl_alert_thermal_throttle"),
        "install_pending": tr("lbl_alert_install_pending"),
        "freshly_fused": tr("lbl_ralert_freshly_fused"),
        "lan_eth_slow_link_10": tr("lbl_ralert_lan_eth_slow_link_10"),
        "lan_eth_slow_link_100": tr("lbl_ralert_lan_eth_slow_link_100"),
        "wan_eth_poor_connection": tr("lbl_ralert_wan_eth_poor_connection"),
        "mesh_topology_changing_often": tr("lbl_ralert_mesh_topology_changing_often"),
        "mesh_unreliable_backhaul": tr("lbl_ralert_mesh_unreliable_backhaul"),
        "radius_missing_process": tr("lbl_ralert_radius_missing_process"),
        "eth_switch_error": tr("lbl_ralert_eth_switch_error"),
        "poe_on_dish_unreachable": tr("lbl_ralert_poe_on_dish_unreachable"),
        "poe_fuse_blown": tr("lbl_ralert_poe_fuse_blown"),
        "poe_router_overcurrent": tr("lbl_ralert_poe_router_overcurrent"),
        "poe_off_current_nominal": tr("lbl_ralert_poe_off_current_nominal"),
        "poe_vin_overvoltage": tr("lbl_ralert_poe_vin_overvoltage"),
        "poe_vin_undervoltage": tr("lbl_ralert_poe_vin_undervoltage"),
        "high_cable_ping_drop_rate": tr("lbl_ralert_high_cable_ping_drop_rate"),
        "sandbox_disabled": tr("lbl_ralert_sandbox_disabled"),
        "only_overflight_blocked": tr("lbl_ralert_only_overflight_blocked"),
        "offline_networks_disabled": tr("lbl_ralert_offline_networks_disabled"),
        "wired_mesh_not_using_wan_iface": tr("lbl_ralert_wired_mesh_not_using_wan_iface"),
    }
    return labels.get(code, code)
