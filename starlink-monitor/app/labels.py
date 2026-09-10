"""
Спільні людські назви (українською) для enum-станів і alert-прапорців
Starlink dish/router. Винесено в окремий модуль, щоб monitor.py (журнал
подій) і telegram_bot.py (відповіді на команди) використовували ті самі
назви, без дублювання словників.

Джерело значень - grpcurl describe на живому dish/router (див. докстрінг
app/starlink_client.py).
"""

# Людські назви станів оновлення dish (enum SpaceX.API.Device.SoftwareUpdateState)
UPDATE_STATE_LABELS = {
    "SOFTWARE_UPDATE_STATE_UNKNOWN": "невідомо",
    "IDLE": "немає оновлень",
    "FETCHING": "завантаження оновлення",
    "PRE_CHECK": "перевірка перед встановленням",
    "WRITING": "встановлення оновлення",
    "POST_CHECK": "перевірка після встановлення",
    "REBOOT_REQUIRED": "оновлення готове, очікує перезавантаження",
    "DISABLED": "оновлення вимкнено",
    "FAULTED": "помилка оновлення",
}

# Людські назви alert-прапорців dish (19 полів message DishAlerts,
# ті самі, що й у starlink_client.ALERT_FIELD_NAMES)
ALERT_LABELS = {
    "motors_stuck": "двигуни заклинило",
    "thermal_shutdown": "аварійне вимкнення через перегрів",
    "thermal_throttle": "обмеження через перегрів",
    "unexpected_location": "неочікуване розташування",
    "mast_not_near_vertical": "мачта не вертикальна",
    "slow_ethernet_speeds": "низька швидкість Ethernet",
    "roaming": "роумінг",
    "install_pending": "оновлення очікує встановлення",
    "is_heating": "обігрів увімкнено",
    "power_supply_thermal_throttle": "обмеження блока живлення через перегрів",
    "is_power_save_idle": "режим енергозбереження",
    "dbf_telem_stale": "застарілі дані телеметрії",
    "low_motor_current": "низький струм двигунів",
    "lower_signal_than_predicted": "сигнал слабший за прогнозований",
    "slow_ethernet_speeds_100": "швидкість Ethernet нижче 100 Мбіт/с",
    "obstruction_map_reset": "карта перешкод скинута",
    "dish_water_detected": "виявлено воду на dish",
    "router_water_detected": "виявлено воду на роутері",
    "upsu_router_port_slow": "повільний порт роутера UPSU",
    "no_ethernet_link": "немає з'єднання Ethernet",
}

# Людські назви станів оновлення роутера (enum WifiSoftwareUpdateState)
ROUTER_UPDATE_STATE_LABELS = {
    "NOT_RUN": "немає оновлень",
    "GETTING_TARGET_VERSION": "перевірка наявності оновлення",
    "DOWNLOADING_UPDATE_IMAGE": "завантаження оновлення",
    "FLASHING": "встановлення оновлення",
    "NO_UPDATE_REQUIRED": "оновлення не потрібне",
    "REBOOT_PENDING": "оновлення готове, очікує перезавантаження",
    "GETTING_TARGET_VERSION_FAILED": "помилка перевірки оновлення",
    "GETTING_TARGET_VERSION_EXHAUSTED": "не вдалося перевірити оновлення",
    "NO_VALID_ARTIFACT": "відсутній коректний файл оновлення",
    "ILLEGAL_ARTIFACT": "некоректний файл оновлення",
    "DOWNLOADING_UPDATE_IMAGE_FAILED": "помилка завантаження оновлення",
    "DOWNLOADING_UPDATE_IMAGE_EXHAUSTED": "не вдалося завантажити оновлення",
    "FLASHING_FAILED": "помилка встановлення оновлення",
}

# Людські назви alert-прапорців роутера (21 поле message WifiAlerts,
# ті самі, що й у starlink_client.ROUTER_ALERT_FIELD_NAMES)
ROUTER_ALERT_LABELS = {
    "thermal_throttle": "обмеження через перегрів",
    "install_pending": "оновлення очікує встановлення",
    "freshly_fused": "щойно активовано (freshly fused)",
    "lan_eth_slow_link_10": "повільне LAN Ethernet з'єднання (10 Мбіт/с)",
    "lan_eth_slow_link_100": "повільне LAN Ethernet з'єднання (100 Мбіт/с)",
    "wan_eth_poor_connection": "погане WAN Ethernet з'єднання",
    "mesh_topology_changing_often": "топологія mesh-мережі часто змінюється",
    "mesh_unreliable_backhaul": "ненадійний mesh-канал",
    "radius_missing_process": "відсутній процес RADIUS",
    "eth_switch_error": "помилка Ethernet-комутатора",
    "poe_on_dish_unreachable": "PoE на dish недоступне",
    "poe_fuse_blown": "перегорів запобіжник PoE",
    "poe_router_overcurrent": "перевищення струму PoE роутера",
    "poe_off_current_nominal": "PoE вимкнено (номінальний струм)",
    "poe_vin_overvoltage": "перевищення напруги живлення PoE",
    "poe_vin_undervoltage": "занижена напруга живлення PoE",
    "high_cable_ping_drop_rate": "високі втрати пакетів на кабелі",
    "sandbox_disabled": "sandbox вимкнено",
    "only_overflight_blocked": "заблоковано лише прольотний режим",
    "offline_networks_disabled": "офлайн-мережі вимкнено",
    "wired_mesh_not_using_wan_iface": "дротовий mesh не використовує WAN-інтерфейс",
}
