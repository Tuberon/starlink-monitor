"""Мінімалістична i18n-система для веб-інтерфейсу та Telegram-бота.
Одна мова інтерфейсу для всього проєкту (не per-user) - зберігається
в settings-таблиці (ui_language), той самий ключ визначає і мову
веб-сторінок, і мову Telegram-повідомлень (одна людина/сім'я керує
одним Pi, окрема мова для кожного каналу не потрібна).

t(key) шукає переклад для поточної мови; якщо ключ відсутній у
словнику - повертає сам ключ (одразу видно відсутній переклад,
не приховує помилку порожнім рядком). Використовується як Jinja2-
global (app.jinja_env.globals["t"] = t) для шаблонів і напряму в
telegram_bot.py. Для JS - словник серіалізується в JSON і
вбудовується в кожну сторінку (window.I18N), плюс маленький
t(key) JS-helper у common.js."""
from typing import Any, Callable, Iterable, Optional

from app import db

DEFAULT_LANG = "uk"
SUPPORTED_LANGS = ("uk", "en")

TRANSLATIONS: dict[str, dict[str, str]] = {
    # ---- index.html: шапка/мета ----
    "page_title_index": {"uk": "Dish Watch — Starlink Monitor", "en": "Dish Watch — Starlink Monitor"},
    "brand_subtitle": {"uk": "Starlink Mini · Автономний монітор", "en": "Starlink Mini · Autonomous monitor"},
    "connecting": {"uk": "З'єднання...", "en": "Connecting..."},
    "nav_stats_title": {"uk": "Статистика", "en": "Statistics"},
    "nav_settings_title": {"uk": "Налаштування", "en": "Settings"},
    "offline_banner": {"uk": "⚠️ Немає зв'язку з Pi — показано останній відомий стан", "en": "⚠️ No connection to Pi — showing last known state"},

    # ---- index.html: метрики dish ----
    "label_state": {"uk": "Стан", "en": "State"},
    "label_downlink": {"uk": "Downlink", "en": "Downlink"},
    "label_uplink": {"uk": "Uplink", "en": "Uplink"},
    "label_latency": {"uk": "Затримка", "en": "Latency"},
    "label_packet_loss": {"uk": "Втрати пакетів", "en": "Packet loss"},
    "label_obstruction": {"uk": "Обструкція", "en": "Obstruction"},
    "label_uptime_dish": {"uk": "Uptime dish", "en": "Dish uptime"},
    "label_dish_id": {"uk": "Dish ID", "en": "Dish ID"},
    "label_firmware_dish": {"uk": "Прошивка тарілки", "en": "Dish firmware"},
    "label_firmware_router": {"uk": "Прошивка роутера", "en": "Router firmware"},

    # ---- index.html: статус-рядки ----
    "row_dish_update": {"uk": "Оновлення ПЗ dish", "en": "Dish firmware update"},
    "flag_reboot_pending": {"uk": "очікує перезавантаження", "en": "reboot pending"},
    "flag_install_pending": {"uk": "очікує встановлення", "en": "install pending"},
    "row_dish_alerts": {"uk": "Попередження dish", "en": "Dish alerts"},
    "alerts_none": {"uk": "активних попереджень немає", "en": "no active alerts"},
    "row_router_update": {"uk": "Оновлення ПЗ роутера", "en": "Router firmware update"},
    "row_router_alerts": {"uk": "Попередження роутера", "en": "Router alerts"},

    # ---- index.html: керування Starlink ----
    "panel_starlink_control": {"uk": "Керування Starlink", "en": "Starlink control"},
    "btn_reboot_starlink": {"uk": "⟲ Перезавантажити Starlink", "en": "⟲ Reboot Starlink"},
    "hint_reboot_starlink": {"uk": "Ручний reboot dish через локальний gRPC API", "en": "Manual dish reboot via local gRPC API"},
    "btn_check_updates": {"uk": "↻ Перевірити стан оновлень", "en": "↻ Check update status"},
    "hint_check_updates": {"uk": "Оновлює дані dish і роутера негайно, без очікування циклу опитування", "en": "Refreshes dish and router data immediately, without waiting for the poll cycle"},
    "toggle_auto_reboot": {"uk": "Автоматичний reboot при готовому оновленні", "en": "Automatic reboot when update is ready"},

    # ---- index.html: клієнти WiFi ----
    "panel_wifi_clients": {"uk": "Клієнти WiFi", "en": "WiFi clients"},
    "th_device": {"uk": "Пристрій", "en": "Device"},
    "th_ip": {"uk": "IP", "en": "IP"},
    "th_band": {"uk": "Діапазон", "en": "Band"},
    "th_signal": {"uk": "Сигнал", "en": "Signal"},
    "th_uptime_net": {"uk": "У мережі", "en": "On network"},
    "loading": {"uk": "Завантаження...", "en": "Loading..."},

    # ---- index.html: секція Pi ----
    "label_uptime_pi": {"uk": "Uptime Pi", "en": "Pi uptime"},
    "label_temperature": {"uk": "Температура", "en": "Temperature"},
    "label_cpu": {"uk": "CPU", "en": "CPU"},
    "label_memory": {"uk": "Пам'ять", "en": "Memory"},
    "label_disk": {"uk": "Диск", "en": "Disk"},
    "panel_pi_control": {"uk": "Керування Raspberry Pi", "en": "Raspberry Pi control"},
    "btn_reboot_pi": {"uk": "⟲ Перезавантажити Pi", "en": "⟲ Reboot Pi"},
    "btn_shutdown_pi": {"uk": "⏻ Вимкнути Pi", "en": "⏻ Shut down Pi"},
    "hint_pi_control": {"uk": "Обидві дії reboot/shutdown розривають з'єднання з дашбордом", "en": "Both reboot/shutdown actions will disconnect the dashboard"},

    # ---- index.html: журнал подій ----
    "panel_event_log": {"uk": "Журнал подій", "en": "Event log"},
    "btn_clear_title": {"uk": "Очистити журнал подій", "en": "Clear event log"},
    "btn_clear": {"uk": "Очистити", "en": "Clear"},
    "link_all_stats": {"uk": "Уся статистика →", "en": "All statistics →"},

    # ---- index.html: футер ----
    "footer_license": {"uk": "© 2026 JunioR · Starlink Mini Monitor · Ліцензія MIT", "en": "© 2026 JunioR · Starlink Mini Monitor · MIT License"},

    # ---- common.js: fmtDateHeader/fmtAgo (дата-групування журналу, "N хв тому") ----
    "date_today": {"uk": "Сьогодні", "en": "Today"},
    "date_yesterday": {"uk": "Вчора", "en": "Yesterday"},
    "ago_unknown": {"uk": "невідомо", "en": "unknown"},
    "ago_just_now": {"uk": "щойно", "en": "just now"},
    "ago_min": {"uk": "хв тому", "en": "min ago"},
    "ago_hour": {"uk": "год тому", "en": "hr ago"},
    "ago_day": {"uk": "дн тому", "en": "days ago"},

    # ---- dashboard.js: UPDATE_STATE_LABELS (dish) ----
    "us_unknown": {"uk": "невідомо", "en": "unknown"},
    "us_idle": {"uk": "немає оновлень", "en": "no updates"},
    "us_fetching": {"uk": "завантаження", "en": "downloading"},
    "us_pre_check": {"uk": "перевірка перед встановленням", "en": "pre-install check"},
    "us_writing": {"uk": "встановлення", "en": "installing"},
    "us_post_check": {"uk": "перевірка після встановлення", "en": "post-install check"},
    "us_reboot_required": {"uk": "очікує перезавантаження", "en": "reboot required"},
    "us_disabled": {"uk": "вимкнено", "en": "disabled"},
    "us_faulted": {"uk": "помилка оновлення", "en": "update failed"},

    # ---- dashboard.js: ALERT_LABELS (dish) ----
    "alert_motors_stuck": {"uk": "двигуни заклинило", "en": "motors stuck"},
    "alert_thermal_shutdown": {"uk": "аварійне вимкнення через перегрів", "en": "thermal shutdown"},
    "alert_thermal_throttle": {"uk": "обмеження через перегрів", "en": "thermal throttling"},
    "alert_unexpected_location": {"uk": "неочікуване розташування", "en": "unexpected location"},
    "alert_mast_not_near_vertical": {"uk": "мачта не вертикальна", "en": "mast not vertical"},
    "alert_slow_ethernet_speeds": {"uk": "низька швидкість Ethernet", "en": "slow Ethernet speed"},
    "alert_roaming": {"uk": "роумінг", "en": "roaming"},
    "alert_install_pending": {"uk": "очікує встановлення", "en": "install pending"},
    "alert_is_heating": {"uk": "обігрів увімкнено", "en": "heater on"},
    "alert_power_supply_thermal_throttle": {"uk": "обмеження блока живлення через перегрів", "en": "power supply thermal throttling"},
    "alert_is_power_save_idle": {"uk": "режим енергозбереження", "en": "power-save idle"},
    "alert_dbf_telem_stale": {"uk": "застарілі дані телеметрії", "en": "stale telemetry data"},
    "alert_low_motor_current": {"uk": "низький струм двигунів", "en": "low motor current"},
    "alert_lower_signal_than_predicted": {"uk": "сигнал слабший за прогнозований", "en": "signal weaker than predicted"},
    "alert_slow_ethernet_speeds_100": {"uk": "швидкість Ethernet нижче 100 Мбіт/с", "en": "Ethernet speed below 100 Mbps"},
    "alert_obstruction_map_reset": {"uk": "карта перешкод скинута", "en": "obstruction map reset"},
    "alert_dish_water_detected": {"uk": "виявлено воду на dish", "en": "water detected on dish"},
    "alert_router_water_detected": {"uk": "виявлено воду на роутері", "en": "water detected on router"},
    "alert_upsu_router_port_slow": {"uk": "повільний порт роутера UPSU", "en": "slow UPSU router port"},
    "alert_no_ethernet_link": {"uk": "немає з'єднання Ethernet", "en": "no Ethernet link"},

    # ---- dashboard.js: ROUTER_UPDATE_STATE_LABELS ----
    "rus_not_run": {"uk": "немає оновлень", "en": "no updates"},
    "rus_getting_target_version": {"uk": "перевірка наявності оновлення", "en": "checking for update"},
    "rus_downloading_update_image": {"uk": "завантаження оновлення", "en": "downloading update"},
    "rus_flashing": {"uk": "встановлення оновлення", "en": "installing update"},
    "rus_no_update_required": {"uk": "оновлення не потрібне", "en": "update not required"},
    "rus_reboot_pending": {"uk": "очікує перезавантаження", "en": "reboot pending"},
    "rus_getting_target_version_exhausted": {"uk": "не вдалося перевірити оновлення", "en": "failed to check for update"},
    "rus_no_valid_artifact": {"uk": "відсутній коректний файл оновлення", "en": "no valid update file"},
    "rus_illegal_artifact": {"uk": "некоректний файл оновлення", "en": "invalid update file"},
    "rus_downloading_update_image_exhausted": {"uk": "не вдалося завантажити оновлення", "en": "failed to download update"},
    "rus_flashing_failed": {"uk": "помилка встановлення оновлення", "en": "update installation failed"},

    # ---- dashboard.js: ROUTER_ALERT_LABELS ----
    "ralert_freshly_fused": {"uk": "щойно активовано", "en": "freshly activated"},
    "ralert_lan_eth_slow_link_10": {"uk": "повільне LAN Ethernet (10 Мбіт/с)", "en": "slow LAN Ethernet (10 Mbps)"},
    "ralert_lan_eth_slow_link_100": {"uk": "повільне LAN Ethernet (100 Мбіт/с)", "en": "slow LAN Ethernet (100 Mbps)"},
    "ralert_wan_eth_poor_connection": {"uk": "погане WAN Ethernet з'єднання", "en": "poor WAN Ethernet connection"},
    "ralert_mesh_topology_changing_often": {"uk": "топологія mesh часто змінюється", "en": "mesh topology changing often"},
    "ralert_mesh_unreliable_backhaul": {"uk": "ненадійний mesh-канал", "en": "unreliable mesh backhaul"},
    "ralert_radius_missing_process": {"uk": "відсутній процес RADIUS", "en": "RADIUS process missing"},
    "ralert_eth_switch_error": {"uk": "помилка Ethernet-комутатора", "en": "Ethernet switch error"},
    "ralert_poe_on_dish_unreachable": {"uk": "PoE на dish недоступне", "en": "PoE on dish unreachable"},
    "ralert_poe_fuse_blown": {"uk": "перегорів запобіжник PoE", "en": "PoE fuse blown"},
    "ralert_poe_router_overcurrent": {"uk": "перевищення струму PoE роутера", "en": "PoE router overcurrent"},
    "ralert_poe_off_current_nominal": {"uk": "PoE вимкнено (номінальний струм)", "en": "PoE off (nominal current)"},
    "ralert_poe_vin_overvoltage": {"uk": "перевищення напруги живлення PoE", "en": "PoE input overvoltage"},
    "ralert_poe_vin_undervoltage": {"uk": "занижена напруга живлення PoE", "en": "PoE input undervoltage"},
    "ralert_high_cable_ping_drop_rate": {"uk": "високі втрати пакетів на кабелі", "en": "high cable ping drop rate"},
    "ralert_sandbox_disabled": {"uk": "sandbox вимкнено", "en": "sandbox disabled"},
    "ralert_only_overflight_blocked": {"uk": "заблоковано лише прольотний режим", "en": "overflight mode only blocked"},
    "ralert_offline_networks_disabled": {"uk": "офлайн-мережі вимкнено", "en": "offline networks disabled"},
    "ralert_wired_mesh_not_using_wan_iface": {"uk": "дротовий mesh не використовує WAN", "en": "wired mesh not using WAN"},

    # ---- dashboard.js: решта UI-текстів (кнопки, hint'и, статуси) ----
    "not_polled_yet": {"uk": "ще не опитано", "en": "not polled yet"},
    "no_response": {"uk": "немає відповіді", "en": "no response"},
    "confirm_reboot_starlink": {"uk": "Перезавантажити Starlink dish зараз? Зв'язок буде втрачено на ~1-2 хвилини.", "en": "Reboot Starlink dish now? Connection will be lost for ~1-2 minutes."},
    "sending_reboot_cmd": {"uk": "Надсилаю команду reboot...", "en": "Sending reboot command..."},
    "reboot_cmd_sent_ok": {"uk": "Команда reboot надіслана успішно", "en": "Reboot command sent successfully"},
    "error_prefix": {"uk": "Помилка", "en": "Error"},
    "network_error_sending": {"uk": "Помилка мережі при надсиланні команди", "en": "Network error while sending command"},
    "sending_pi_reboot_cmd": {"uk": "Надсилаю команду перезавантаження...", "en": "Sending reboot command..."},
    "cmd_sent_disconnected": {"uk": "Команду надіслано (з'єднання розірвано)", "en": "Command sent (connection lost)"},
    "sending_pi_shutdown_cmd": {"uk": "Надсилаю команду вимкнення...", "en": "Sending shutdown command..."},
    "polling_dish_router": {"uk": "Опитую dish і роутер...", "en": "Polling dish and router..."},
    "not_available_short": {"uk": "н/д", "en": "n/a"},
    "check_error": {"uk": "Помилка перевірки", "en": "Check failed"},
    "network_error_checking": {"uk": "Помилка мережі при перевірці", "en": "Network error while checking"},
    "enabled": {"uk": "увімкнено", "en": "enabled"},
    "disabled_word": {"uk": "вимкнено", "en": "disabled"},

    # ---- dashboard.js: одиниці/формати (uptime, ПЗ-версія, лічильники) ----
    "unit_days_short": {"uk": "д", "en": "d"},
    "unit_hours_short": {"uk": "г", "en": "h"},
    "unit_minutes_short": {"uk": "хв", "en": "min"},
    "unit_mbps": {"uk": "Мбіт/с", "en": "Mbps"},
    "unit_ms": {"uk": "мс", "en": "ms"},
    "fw_hw_line": {"uk": "ПЗ: {sw}  ·  Апаратна версія: {hw}", "en": "Firmware: {sw}  ·  Hardware version: {hw}"},
    "clients_connected": {"uk": "{n} підключено", "en": "{n} connected"},
    "pi_rebooting": {"uk": "Pi перезавантажується...", "en": "Pi is rebooting..."},
    "pi_shutting_down": {"uk": "Pi вимикається...", "en": "Pi is shutting down..."},
    "check_done": {"uk": "Готово. Dish: {dish}  ·  Роутер: {router}", "en": "Done. Dish: {dish}  ·  Router: {router}"},
    "unavailable_reason": {"uk": "недоступний ({reason})", "en": "unavailable ({reason})"},
    "no_events_yet": {"uk": "Подій ще немає", "en": "No events yet"},
    "confirm_reboot_pi": {"uk": "Перезавантажити Raspberry Pi зараз? Дашборд стане недоступний на 1-2 хвилини.", "en": "Reboot Raspberry Pi now? The dashboard will be unavailable for 1-2 minutes."},
    "confirm_shutdown_pi_1": {"uk": "Вимкнути Raspberry Pi зараз? Для повторного увімкнення знадобиться фізичний доступ до пристрою (від'єднати й підключити живлення).", "en": "Shut down Raspberry Pi now? Powering it back on will require physical access to the device (unplug and reconnect power)."},
    "confirm_shutdown_pi_2": {"uk": "Підтвердіть ще раз: дашборд стане повністю недоступний до ручного увімкнення Pi.", "en": "Confirm once more: the dashboard will become completely unavailable until Pi is manually powered on."},
    "log_cleared_locally": {"uk": "Журнал очищено на екрані", "en": "Log cleared on screen"},

    # ---- settings.html: шапка/мета ----
    "page_title_settings": {"uk": "Налаштування — Dish Watch", "en": "Settings — Dish Watch"},
    "back_to_dashboard": {"uk": "Назад до дашборду", "en": "Back to dashboard"},
    "settings_title": {"uk": "Налаштування", "en": "Settings"},
    "settings_subtitle": {"uk": "Dish Watch · Telegram, backup", "en": "Dish Watch · Telegram, backup"},

    # ---- settings.html: панель "Вигляд" ----
    "panel_appearance": {"uk": "Вигляд", "en": "Appearance"},
    "toggle_light_theme": {"uk": "Світла тема", "en": "Light theme"},
    "label_interface_language": {"uk": "Мова інтерфейсу", "en": "Interface language"},
    "lang_uk": {"uk": "Українська", "en": "Ukrainian"},
    "lang_en": {"uk": "English", "en": "English"},

    # ---- settings.html: панель Telegram ----
    "panel_telegram": {"uk": "Telegram-сповіщення", "en": "Telegram notifications"},
    "sub_telegram": {"uk": "reboot, оновлення, попередження", "en": "reboot, updates, alerts"},
    "toggle_enable_notifications": {"uk": "Увімкнути сповіщення", "en": "Enable notifications"},
    "label_bot_token": {"uk": "Bot token", "en": "Bot token"},
    "label_chat_ids": {"uk": "Chat ID (через кому, якщо кілька)", "en": "Chat ID (comma-separated if multiple)"},
    "btn_save": {"uk": "💾 Зберегти", "en": "💾 Save"},
    "btn_send_test": {"uk": "✉ Надіслати тестове", "en": "✉ Send test"},
    "hint_telegram_setup": {"uk": "Bot token отримайте в @BotFather, chat_id — у @userinfobot", "en": "Get a bot token from @BotFather, chat_id from @userinfobot"},
    "telegram_commands_hint": {"uk": "Команди в чаті з ботом: <b>/status</b> — стан оновлень, <b>/reboot</b> — перезавантажити Starlink Mini (з підтвердженням), <b>/id</b> — відомі тарілки, <b>/help</b> — довідка.", "en": "Commands in chat with the bot: <b>/status</b> — update status, <b>/reboot</b> — reboot Starlink Mini (with confirmation), <b>/id</b> — known dishes, <b>/help</b> — help."},

    # ---- settings.html: панель target-версій ----
    "panel_target_versions": {"uk": "Очікувані версії прошивок", "en": "Expected firmware versions"},
    "sub_target_versions": {"uk": "сповіщення, коли встановлено будь-яку з очікуваних версій", "en": "notification when any of the expected versions is installed"},
    "label_dish_target": {"uk": "Тарілка — очікувана версія (можна кілька через кому)", "en": "Dish — expected version (comma-separated for multiple)"},
    "label_router_target": {"uk": "Роутер — очікувана версія (можна кілька через кому)", "en": "Router — expected version (comma-separated for multiple)"},
    "hint_target_versions": {"uk": "Сповіщення прийде, коли встановлена версія збіжиться з БУДЬ-ЯКОЮ з очікуваних (корисно для різних апаратних ревізій) — версії з різних build-каналів (mr/cr тощо) не порівнюються одна з одною, лише в межах свого каналу; порожнє поле + \"Зберегти\" очищає", "en": "Notification triggers when the installed version matches ANY of the expected ones (useful for different hardware revisions) — versions from different build channels (mr/cr etc.) are not compared against each other, only within their own channel; empty field + \"Save\" clears it"},

    # ---- settings.html: панель backup/restore ----
    "panel_backup_restore": {"uk": "Backup/restore налаштувань", "en": "Settings backup/restore"},
    "sub_backup_restore": {"uk": "Telegram config, параметри моніторингу, auto-reboot", "en": "Telegram config, monitoring parameters, auto-reboot"},
    "btn_download_backup": {"uk": "⬇ Завантажити backup", "en": "⬇ Download backup"},
    "btn_restore_backup": {"uk": "⬆ Відновити з backup", "en": "⬆ Restore from backup"},
    "btn_send_backup_telegram": {"uk": "📨 Надіслати backup у Telegram", "en": "📨 Send backup to Telegram"},
    "hint_backup": {"uk": "Bot token, chat_id, auto-reboot, очікувані версії прошивок, історія відомих пристроїв і перевизначені параметри моніторингу одним файлом", "en": "Bot token, chat_id, auto-reboot, expected firmware versions, known devices history, and overridden monitoring parameters in one file"},

    # ---- settings.html: панель параметрів моніторингу ----
    "panel_monitoring_params": {"uk": "Параметри моніторингу", "en": "Monitoring parameters"},
    "sub_monitoring_params": {"uk": "app/config.py — застосовуються після перезапуску сервісів", "en": "app/config.py — applied after service restart"},
    "btn_save_params": {"uk": "💾 Зберегти параметри", "en": "💾 Save parameters"},
    "btn_save_restart": {"uk": "🔁 Зберегти й перезапустити сервіси", "en": "🔁 Save and restart services"},
    "hint_empty_is_default": {"uk": "Порожнє поле = значення за замовчуванням", "en": "Empty field = default value"},

    # ---- settings.js: статуси/повідомлення ----
    "saving_ellipsis": {"uk": "Зберігаю...", "en": "Saving..."},
    "saved": {"uk": "Збережено", "en": "Saved"},
    "save_error": {"uk": "Помилка збереження", "en": "Save error"},
    "network_error_saving": {"uk": "Помилка мережі при збереженні", "en": "Network error while saving"},
    "sending_test_message": {"uk": "Надсилаю тестове повідомлення...", "en": "Sending test message..."},
    "success_word": {"uk": "Успішно", "en": "Success"},
    "error_word": {"uk": "Помилка", "en": "Error"},
    "network_error_testing": {"uk": "Помилка мережі при тестуванні", "en": "Network error while testing"},
    "backup_load_error": {"uk": "Помилка завантаження backup", "en": "Backup download error"},
    "sending_backup_telegram": {"uk": "Надсилаю backup у Telegram...", "en": "Sending backup to Telegram..."},
    "network_error_sending_backup": {"uk": "Помилка мережі при відправці backup", "en": "Network error while sending backup"},
    "confirm_restore_settings": {"uk": "Відновити налаштування з цього файлу? Поточні Telegram-налаштування, перемикач auto-reboot і перевизначені параметри моніторингу будуть перезаписані.", "en": "Restore settings from this file? Current Telegram settings, auto-reboot toggle, and overridden monitoring parameters will be overwritten."},
    "restored": {"uk": "Відновлено", "en": "Restored"},
    "invalid_backup_file": {"uk": "Некоректний файл backup", "en": "Invalid backup file"},
    "category_other": {"uk": "інше", "en": "other"},
    "saved_apply_hint": {"uk": "Збережено. Щоб застосувати — перезапустіть сервіси (кнопка поруч) або вручну на Pi.", "en": "Saved. To apply — restart services (button nearby) or manually on Pi."},
    "confirm_save_restart": {"uk": "Зберегти параметри і перезапустити сервіси моніторингу та веб-інтерфейсу? Дашборд буде недоступний кілька секунд.", "en": "Save parameters and restart monitoring and web interface services? The dashboard will be unavailable for a few seconds."},
    "restarting_services": {"uk": "Перезапускаю сервіси...", "en": "Restarting services..."},
    "services_restarted": {"uk": "Сервіси перезапущено.", "en": "Services restarted."},
    "cmd_sent_restart_disconnect": {"uk": "Команду надіслано (з'єднання могло розірватись під час рестарту)", "en": "Command sent (connection may have dropped during restart)"},
    "backup_sent_ok": {"uk": "✅ Backup надіслано: {msg}", "en": "✅ Backup sent: {msg}"},
    "default_value_hint": {"uk": "за замовчуванням: {default}{active}", "en": "default: {default}{active}"},
    "active_now_suffix": {"uk": " (активне зараз)", "en": " (currently active)"},

    # ---- config_editor.py: категорії параметрів ----
    "cat_monitoring": {"uk": "Моніторинг та мережа", "en": "Monitoring and network"},
    "cat_reliability": {"uk": "Надійність", "en": "Reliability"},
    "cat_telegram": {"uk": "Telegram", "en": "Telegram"},
    "cat_gpio": {"uk": "GPIO та периферія", "en": "GPIO and peripherals"},

    # ---- config_editor.py: параметри - monitoring ----
    "param_dish_addr": {"uk": "Адреса тарілки (dish)", "en": "Dish address"},
    "param_dish_timeout": {"uk": "Timeout запиту до dish, сек", "en": "Dish request timeout, sec"},
    "param_router_addr": {"uk": "Адреса роутера", "en": "Router address"},
    "param_poll_interval": {"uk": "Інтервал опитування dish, сек", "en": "Dish poll interval, sec"},
    "param_router_poll_interval": {"uk": "Інтервал опитування router, сек", "en": "Router poll interval, sec"},
    "param_system_metrics_interval": {"uk": "Інтервал запису CPU/пам'яті/температури, сек", "en": "CPU/memory/temperature record interval, sec"},
    "param_dish_metrics_batch_interval": {"uk": "Інтервал batch-запису dish-метрик, сек", "en": "Dish metrics batch-write interval, sec"},
    "param_obstruction_warn": {"uk": "Поріг попередження про перешкоди (0-1)", "en": "Obstruction warning threshold (0-1)"},
    "param_history_days": {"uk": "Зберігати історію, днів", "en": "Keep history, days"},
    "param_webui_port": {"uk": "Порт веб-інтерфейсу", "en": "Web interface port"},

    # ---- config_editor.py: параметри - reliability ----
    "param_db_integrity_check_interval": {"uk": "Інтервал перевірки цілісності БД, сек", "en": "DB integrity check interval, sec"},
    "param_auto_backup_enabled": {"uk": "Автоматичний періодичний backup (0/1)", "en": "Automatic periodic backup (0/1)"},
    "param_auto_backup_interval": {"uk": "Інтервал автоматичного backup, сек", "en": "Automatic backup interval, sec"},
    "param_auto_backup_keep_count": {"uk": "Скільки останніх backup-ів зберігати", "en": "How many recent backups to keep"},
    "param_telegram_backup_enabled": {"uk": "Періодично надсилати backup у Telegram (0/1)", "en": "Periodically send backup to Telegram (0/1)"},
    "param_telegram_backup_interval_hours": {"uk": "Інтервал відправки backup у Telegram, годин", "en": "Telegram backup send interval, hours"},
    "param_max_failures": {"uk": "Невдалих опитувань перед watchdog-reboot", "en": "Failed polls before watchdog reboot"},
    "param_min_reboot_interval": {"uk": "Мін. інтервал між авто-ребутами, сек", "en": "Min. interval between auto-reboots, sec"},
    "param_auto_reboot_on_update": {"uk": "Авто-reboot при готовому оновленні", "en": "Auto-reboot when update is ready"},
    "param_max_logged_failures": {"uk": "Макс. послідовних невдач у журналі перед припиненням запису", "en": "Max. consecutive failures logged before stopping"},
    "param_reboot_spam_threshold": {"uk": "Група reboot-сповіщень: поріг кількості за вікно", "en": "Reboot notification grouping: count threshold per window"},
    "param_reboot_spam_window": {"uk": "Група reboot-сповіщень: вікно часу, сек", "en": "Reboot notification grouping: time window, sec"},

    # ---- config_editor.py: параметри - telegram ----
    "param_notifications_mute_after": {"uk": "Приглушити Telegram після недоступності dish, сек", "en": "Mute Telegram after dish unavailability, sec"},
    "param_notify_dish_recovery": {"uk": "Сповіщати 'Dish знову online' (0/1)", "en": "Notify 'Dish back online' (0/1)"},
    "param_notify_pi_startup": {"uk": "Сповіщати про запуск Pi після перезавантаження (0/1)", "en": "Notify about Pi startup after reboot (0/1)"},
    "param_telegram_send_timeout": {"uk": "Telegram-бот: timeout надсилання, сек", "en": "Telegram bot: send timeout, sec"},
    "param_telegram_poll_timeout": {"uk": "Telegram-бот: timeout long-polling, сек", "en": "Telegram bot: long-polling timeout, sec"},
    "param_telegram_confirm_ttl": {"uk": "Telegram-бот: TTL підтвердження команд, сек", "en": "Telegram bot: command confirmation TTL, sec"},
    "param_telegram_notify_timeout": {"uk": "Telegram-сповіщення: timeout запиту, сек", "en": "Telegram notifications: request timeout, sec"},
    "param_telegram_send_retries": {"uk": "Telegram: повторних спроб при мережевій помилці", "en": "Telegram: retries on network error"},
    "param_telegram_send_retry_delay": {"uk": "Telegram: затримка між повторами, сек", "en": "Telegram: delay between retries, sec"},
    "param_telegram_id_list_max": {"uk": "/id: макс. тарілок у списку без аргументу", "en": "/id: max dishes listed without argument"},

    # ---- config_editor.py: параметри - gpio ----
    "param_shutdown_button_pin": {"uk": "GPIO-пін кнопки виключення (0=вимк.)", "en": "GPIO pin for shutdown button (0=off)"},
    "param_shutdown_button_hold": {"uk": "Утримання кнопки перед вимкненням, сек", "en": "Button hold before shutdown, sec"},
    "param_shutdown_button_poll": {"uk": "Опитування GPIO кнопки виключення, сек", "en": "Shutdown button GPIO poll interval, sec"},
    "param_activity_led_pin": {"uk": "GPIO-пін LED активності SD-картки (0=вимк.)", "en": "GPIO pin for SD-card activity LED (0=off)"},
    "param_activity_led_blink_ms": {"uk": "Тривалість спалаху LED активності, мс", "en": "Activity LED blink duration, ms"},
    "param_display_enabled": {"uk": "Фізичний TFT-дисплей статусу (0/1)", "en": "Physical TFT status display (0/1)"},
    "param_display_button_poll": {"uk": "Опитування GPIO кнопки дисплея, сек", "en": "Display button GPIO poll interval, sec"},
    "param_display_spi_cs_pin": {"uk": "Дисплей: GPIO-пін CS (bit-banged, не апаратний CE0/CE1)", "en": "Display: CS GPIO pin (bit-banged, not hardware CE0/CE1)"},
    "param_display_dc_pin": {"uk": "Дисплей: GPIO-пін DC", "en": "Display: DC GPIO pin"},
    "param_display_rst_pin": {"uk": "Дисплей: GPIO-пін RES", "en": "Display: RES GPIO pin"},
    "param_display_bl_pin": {"uk": "Дисплей: GPIO-пін підсвітки (0=не керувати)", "en": "Display: backlight GPIO pin (0=don't control)"},
    "param_display_width": {"uk": "Дисплей: ширина, px", "en": "Display: width, px"},
    "param_display_height": {"uk": "Дисплей: висота, px", "en": "Display: height, px"},
    "param_display_rotation": {"uk": "Дисплей: поворот, ° (0/90/180/270)", "en": "Display: rotation, ° (0/90/180/270)"},
    "param_display_offset_left": {"uk": "Дисплей: зміщення X відносно GRAM, px", "en": "Display: X offset relative to GRAM, px"},
    "param_display_offset_top": {"uk": "Дисплей: зміщення Y відносно GRAM, px", "en": "Display: Y offset relative to GRAM, px"},
    "param_display_refresh": {"uk": "Дисплей: інтервал оновлення, сек", "en": "Display: refresh interval, sec"},
    "param_display_shutdown_delay": {"uk": "Дисплей: затримка перед reboot/poweroff, сек", "en": "Display: delay before reboot/poweroff, sec"},
    "param_display_spi_speed": {"uk": "Дисплей: швидкість SPI, Гц", "en": "Display: SPI speed, Hz"},
    "param_display_backlight_auto_off": {"uk": "Дисплей: автовимкнення підсвітки, сек (0=вимк.)", "en": "Display: backlight auto-off, sec (0=off)"},
    "param_display_update_flash": {"uk": "Дисплей: підсвітка при зміні статусу оновлення, сек (0=вимк.)", "en": "Display: backlight flash on update status change, sec (0=off)"},

    # ---- config_editor.py: повідомлення валідації типів ----
    "expected_type": {"uk": "очікується {type}", "en": "expected {type}"},
    "expected_0_or_1": {"uk": "очікується 0 або 1", "en": "expected 0 or 1"},

    # ---- stats.html ----
    "page_title_stats": {"uk": "Статистика — Dish Watch", "en": "Statistics — Dish Watch"},
    "stats_subtitle": {"uk": "Dish Watch · журнал подій", "en": "Dish Watch · event log"},

    # ---- labels.py: UPDATE_STATE_LABELS (детальніші за dashboard.js - для журналу подій/Telegram) ----
    "lbl_us_unknown": {"uk": "невідомо", "en": "unknown"},
    "lbl_us_idle": {"uk": "немає оновлень", "en": "no updates"},
    "lbl_us_fetching": {"uk": "завантаження оновлення", "en": "downloading update"},
    "lbl_us_pre_check": {"uk": "перевірка перед встановленням", "en": "pre-install check"},
    "lbl_us_writing": {"uk": "встановлення оновлення", "en": "installing update"},
    "lbl_us_post_check": {"uk": "перевірка після встановлення", "en": "post-install check"},
    "lbl_us_reboot_required": {"uk": "оновлення готове, очікує перезавантаження", "en": "update ready, reboot pending"},
    "lbl_us_disabled": {"uk": "оновлення вимкнено", "en": "updates disabled"},
    "lbl_us_faulted": {"uk": "помилка оновлення", "en": "update failed"},

    # ---- labels.py: ALERT_LABELS (dish) ----
    "lbl_alert_motors_stuck": {"uk": "двигуни заклинило", "en": "motors stuck"},
    "lbl_alert_thermal_shutdown": {"uk": "аварійне вимкнення через перегрів", "en": "thermal shutdown"},
    "lbl_alert_thermal_throttle": {"uk": "обмеження через перегрів", "en": "thermal throttling"},
    "lbl_alert_unexpected_location": {"uk": "неочікуване розташування", "en": "unexpected location"},
    "lbl_alert_mast_not_near_vertical": {"uk": "мачта не вертикальна", "en": "mast not vertical"},
    "lbl_alert_slow_ethernet_speeds": {"uk": "низька швидкість Ethernet", "en": "slow Ethernet speed"},
    "lbl_alert_roaming": {"uk": "роумінг", "en": "roaming"},
    "lbl_alert_install_pending": {"uk": "оновлення очікує встановлення", "en": "update install pending"},
    "lbl_alert_is_heating": {"uk": "обігрів увімкнено", "en": "heater on"},
    "lbl_alert_power_supply_thermal_throttle": {"uk": "обмеження блока живлення через перегрів", "en": "power supply thermal throttling"},
    "lbl_alert_is_power_save_idle": {"uk": "режим енергозбереження", "en": "power-save idle"},
    "lbl_alert_dbf_telem_stale": {"uk": "застарілі дані телеметрії", "en": "stale telemetry data"},
    "lbl_alert_low_motor_current": {"uk": "низький струм двигунів", "en": "low motor current"},
    "lbl_alert_lower_signal_than_predicted": {"uk": "сигнал слабший за прогнозований", "en": "signal weaker than predicted"},
    "lbl_alert_slow_ethernet_speeds_100": {"uk": "швидкість Ethernet нижче 100 Мбіт/с", "en": "Ethernet speed below 100 Mbps"},
    "lbl_alert_obstruction_map_reset": {"uk": "карта перешкод скинута", "en": "obstruction map reset"},
    "lbl_alert_dish_water_detected": {"uk": "виявлено воду на dish", "en": "water detected on dish"},
    "lbl_alert_router_water_detected": {"uk": "виявлено воду на роутері", "en": "water detected on router"},
    "lbl_alert_upsu_router_port_slow": {"uk": "повільний порт роутера UPSU", "en": "slow UPSU router port"},
    "lbl_alert_no_ethernet_link": {"uk": "немає з'єднання Ethernet", "en": "no Ethernet link"},

    # ---- labels.py: ROUTER_UPDATE_STATE_LABELS ----
    "lbl_rus_not_run": {"uk": "немає оновлень", "en": "no updates"},
    "lbl_rus_getting_target_version": {"uk": "перевірка наявності оновлення", "en": "checking for update"},
    "lbl_rus_downloading_update_image": {"uk": "завантаження оновлення", "en": "downloading update"},
    "lbl_rus_flashing": {"uk": "встановлення оновлення", "en": "installing update"},
    "lbl_rus_no_update_required": {"uk": "оновлення не потрібне", "en": "update not required"},
    "lbl_rus_reboot_pending": {"uk": "оновлення готове, очікує перезавантаження", "en": "update ready, reboot pending"},
    "lbl_rus_getting_target_version_failed": {"uk": "помилка перевірки оновлення", "en": "update check failed"},
    "lbl_rus_getting_target_version_exhausted": {"uk": "не вдалося перевірити оновлення", "en": "failed to check for update"},
    "lbl_rus_no_valid_artifact": {"uk": "відсутній коректний файл оновлення", "en": "no valid update file"},
    "lbl_rus_illegal_artifact": {"uk": "некоректний файл оновлення", "en": "invalid update file"},
    "lbl_rus_downloading_update_image_failed": {"uk": "помилка завантаження оновлення", "en": "update download failed"},
    "lbl_rus_downloading_update_image_exhausted": {"uk": "не вдалося завантажити оновлення", "en": "failed to download update"},
    "lbl_rus_flashing_failed": {"uk": "помилка встановлення оновлення", "en": "update installation failed"},

    # ---- labels.py: ROUTER_ALERT_LABELS ----
    "lbl_ralert_freshly_fused": {"uk": "щойно активовано (freshly fused)", "en": "freshly activated (freshly fused)"},
    "lbl_ralert_lan_eth_slow_link_10": {"uk": "повільне LAN Ethernet з'єднання (10 Мбіт/с)", "en": "slow LAN Ethernet connection (10 Mbps)"},
    "lbl_ralert_lan_eth_slow_link_100": {"uk": "повільне LAN Ethernet з'єднання (100 Мбіт/с)", "en": "slow LAN Ethernet connection (100 Mbps)"},
    "lbl_ralert_wan_eth_poor_connection": {"uk": "погане WAN Ethernet з'єднання", "en": "poor WAN Ethernet connection"},
    "lbl_ralert_mesh_topology_changing_often": {"uk": "топологія mesh-мережі часто змінюється", "en": "mesh network topology changing often"},
    "lbl_ralert_mesh_unreliable_backhaul": {"uk": "ненадійний mesh-канал", "en": "unreliable mesh backhaul"},
    "lbl_ralert_radius_missing_process": {"uk": "відсутній процес RADIUS", "en": "RADIUS process missing"},
    "lbl_ralert_eth_switch_error": {"uk": "помилка Ethernet-комутатора", "en": "Ethernet switch error"},
    "lbl_ralert_poe_on_dish_unreachable": {"uk": "PoE на dish недоступне", "en": "PoE on dish unreachable"},
    "lbl_ralert_poe_fuse_blown": {"uk": "перегорів запобіжник PoE", "en": "PoE fuse blown"},
    "lbl_ralert_poe_router_overcurrent": {"uk": "перевищення струму PoE роутера", "en": "PoE router overcurrent"},
    "lbl_ralert_poe_off_current_nominal": {"uk": "PoE вимкнено (номінальний струм)", "en": "PoE off (nominal current)"},
    "lbl_ralert_poe_vin_overvoltage": {"uk": "перевищення напруги живлення PoE", "en": "PoE input overvoltage"},
    "lbl_ralert_poe_vin_undervoltage": {"uk": "занижена напруга живлення PoE", "en": "PoE input undervoltage"},
    "lbl_ralert_high_cable_ping_drop_rate": {"uk": "високі втрати пакетів на кабелі", "en": "high cable ping drop rate"},
    "lbl_ralert_sandbox_disabled": {"uk": "sandbox вимкнено", "en": "sandbox disabled"},
    "lbl_ralert_only_overflight_blocked": {"uk": "заблоковано лише прольотний режим", "en": "overflight mode only blocked"},
    "lbl_ralert_offline_networks_disabled": {"uk": "офлайн-мережі вимкнено", "en": "offline networks disabled"},
    "lbl_ralert_wired_mesh_not_using_wan_iface": {"uk": "дротовий mesh не використовує WAN-інтерфейс", "en": "wired mesh not using WAN interface"},

    # ---- telegram_bot.py: повідомлення бота ----
    "tg_unauthorized_chat": {"uk": "\u26d4 Цей чат не авторизований для команд боту.", "en": "\u26d4 This chat is not authorized for bot commands."},
    "tg_unknown_command": {"uk": "Невідома команда. /help — список команд.", "en": "Unknown command. /help — command list."},
    "tg_not_authorized": {"uk": "Не авторизовано", "en": "Not authorized"},
    "tg_reboot_expired": {"uk": "\u231b Запит на reboot застарів. Надішліть /reboot ще раз.", "en": "\u231b Reboot request expired. Send /reboot again."},
    "tg_executing_reboot": {"uk": "\U0001f501 Виконую reboot Starlink Mini...", "en": "\U0001f501 Executing Starlink Mini reboot..."},
    "tg_reboot_success": {"uk": "\u2705 Reboot виконано успішно.", "en": "\u2705 Reboot completed successfully."},
    "tg_reboot_failed": {"uk": "\u274c Не вдалося виконати reboot: {msg}", "en": "\u274c Failed to reboot: {msg}"},
    "tg_cancelled": {"uk": "Скасовано.", "en": "Cancelled."},
    "tg_status_title": {"uk": "<b>Стан Starlink Mini</b>", "en": "<b>Starlink Mini status</b>"},
    "tg_dish_online_line": {"uk": "\U0001f4e1 <b>Тарілка</b>: online, ПЗ {sw}", "en": "\U0001f4e1 <b>Dish</b>: online, firmware {sw}"},
    "tg_update_line": {"uk": "   Оновлення: {label}", "en": "   Update: {label}"},
    "tg_alerts_count_line": {"uk": "   \u26a0\ufe0f Попереджень: {n}", "en": "   \u26a0\ufe0f Alerts: {n}"},
    "tg_dish_offline_line": {"uk": "\U0001f4e1 <b>Тарілка</b>: offline ({error})", "en": "\U0001f4e1 <b>Dish</b>: offline ({error})"},
    "tg_router_online_line": {"uk": "\U0001f4f6 <b>Роутер</b>: online, ПЗ {sw}", "en": "\U0001f4f6 <b>Router</b>: online, firmware {sw}"},
    "tg_router_offline_line": {"uk": "\U0001f4f6 <b>Роутер</b>: offline ({error})", "en": "\U0001f4f6 <b>Router</b>: offline ({error})"},
    "tg_check_updates_title": {"uk": "<b>Перевірка оновлень</b>", "en": "<b>Update check</b>"},
    "tg_dish_line_short": {"uk": "\U0001f4e1 <b>Тарілка</b>: ПЗ {sw}", "en": "\U0001f4e1 <b>Dish</b>: firmware {sw}"},
    "tg_router_line_short": {"uk": "\U0001f4f6 <b>Роутер</b>: ПЗ {sw}", "en": "\U0001f4f6 <b>Router</b>: firmware {sw}"},
    "tg_reboot_confirm_prompt": {"uk": "\u26a0\ufe0f Перезавантажити Starlink Mini зараз? Зв'язок буде втрачено на 1-2 хвилини.", "en": "\u26a0\ufe0f Reboot Starlink Mini now? Connection will be lost for 1-2 minutes."},
    "tg_yes_reboot": {"uk": "\u2705 Так, перезавантажити", "en": "\u2705 Yes, reboot"},
    "tg_cancel_btn": {"uk": "\u274c Скасувати", "en": "\u274c Cancel"},
    "tg_help_text": {"uk": "<b>Starlink Monitor — команди</b>\n\n/status — поточний стан оновлення ПЗ тарілки й роутера, активні попередження\n/checkupdates — примусово опитати dish/router зараз (не чекаючи наступного циклу), перевірити target-версії й зафіксувати відомі зміни прошивки\n/reboot — перезавантажити Starlink Mini (з підтвердженням)\n/id — список усіх колись підключених тарілок (ID, версії ПЗ)\n/id &lt;ID або частина ID&gt; — деталі конкретної тарілки: версії ПЗ dish/router і коли востаннє встановлювались оновлення\n/help — цей список", "en": "<b>Starlink Monitor — commands</b>\n\n/status — current dish/router firmware update status, active alerts\n/checkupdates — force-poll dish/router now (without waiting for the next cycle), check target versions and record known firmware changes\n/reboot — reboot Starlink Mini (with confirmation)\n/id — list of all dishes ever connected (ID, firmware versions)\n/id &lt;ID or part of ID&gt; — details of a specific dish: dish/router firmware versions and when updates were last installed\n/help — this list"},
    "tg_no_dishes_yet": {"uk": "Ще жодної тарілки не підключено.", "en": "No dish has connected yet."},
    "tg_known_dishes_title": {"uk": "<b>Відомі тарілки ({n})</b>", "en": "<b>Known dishes ({n})</b>"},
    "tg_last_seen_line": {"uk": "<code>{id}</code> — востаннє в мережі {ago}", "en": "<code>{id}</code> — last seen {ago}"},
    "tg_and_more_hint": {"uk": "…і ще {n}. Уточніть /id &lt;ID або частина ID&gt;.", "en": "…and {n} more. Specify /id &lt;ID or part of ID&gt;."},
    "tg_details_hint": {"uk": "Деталі: /id &lt;ID або частина ID&gt;", "en": "Details: /id &lt;ID or part of ID&gt;"},
    "tg_multiple_matches": {"uk": "Знайдено кілька збігів, уточніть ID:\n\n{ids}", "en": "Multiple matches found, please specify ID:\n\n{ids}"},
    "tg_dish_not_found": {"uk": "Тарілку з ID «{arg}» не знайдено серед відомих.", "en": "No known dish found with ID \"{arg}\"."},
    "tg_dish_title": {"uk": "<b>Тарілка</b> <code>{id}</code>", "en": "<b>Dish</b> <code>{id}</code>"},
    "tg_dish_hw_line": {"uk": "\U0001f4e1 <b>Dish</b>: {hw}", "en": "\U0001f4e1 <b>Dish</b>: {hw}"},
    "tg_sw_line": {"uk": "   ПЗ: {sw}", "en": "   Firmware: {sw}"},
    "tg_last_fw_update_line": {"uk": "   Останнє оновлення ПЗ: {ago}", "en": "   Last firmware update: {ago}"},
    "tg_router_hw_line": {"uk": "\U0001f4f6 <b>Router</b>: {hw}", "en": "\U0001f4f6 <b>Router</b>: {hw}"},
    "tg_first_seen_line": {"uk": "Вперше підключено: {ago}", "en": "First connected: {ago}"},
    "tg_last_seen_full_line": {"uk": "Востаннє в мережі: {ago}", "en": "Last seen: {ago}"},

    # ---- PWA manifest.json ----
    "manifest_name": {"uk": "Dish Watch — Starlink Monitor", "en": "Dish Watch — Starlink Monitor"},
    "manifest_short_name": {"uk": "Dish Watch", "en": "Dish Watch"},
    "manifest_description": {"uk": "Автономний монітор Starlink Mini на Raspberry Pi", "en": "Autonomous Starlink Mini monitor on Raspberry Pi"},
}


def get_language() -> str:
    """Поточна мова інтерфейсу - з БД, дефолт uk. Якщо збережене
    значення чомусь невідоме (напр. пошкоджений settings-рядок) -
    падає на дефолт, не кидає виняток."""
    lang = db.get_setting("ui_language") or DEFAULT_LANG
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


def _translate(lang: str, key: str, **kwargs: Any) -> str:
    entry = TRANSLATIONS.get(key)
    if entry is None:
        return key
    text = entry.get(lang) or entry.get(DEFAULT_LANG, key)
    return text.format(**kwargs) if kwargs else text


def t(key: str, **kwargs: Any) -> str:
    """Перекладає key для поточної мови. Відсутній ключ у словнику -
    повертає сам key (видно одразу під час розробки/QA, не ховає
    помилку порожнім рядком). kwargs - підстановка через .format()
    для рядків з плейсхолдерами (напр. t('greeting', name=x)).

    Кожен виклик читає мову з БД (нове SQLite-з'єднання) - для
    поодиноких перекладів це прийнятно, але де перекладів багато
    за одну операцію (рендер сторінки, список параметрів, label-
    мапи) - використовувати translator() нижче."""
    return _translate(get_language(), key, **kwargs)


def translator(lang: Optional[str] = None) -> Callable[..., str]:
    """Повертає функцію перекладу, прив'язану до мови, прочитаної з БД
    ОДИН раз. Для операцій з багатьма перекладами: рендер головної
    сторінки робив ~55 окремих SQLite-з'єднань (по одному на кожен
    {{ t('key') }}), /api/env-config - ~57 (виміряно). Свідомо НЕ
    кеш із TTL між запитами - мова змінюється в webapp-процесі, а
    читається й у monitor-процесі (Telegram-бот), тож кеш давав би
    застарілу мову; тут мова лише не перечитується ВСЕРЕДИНІ однієї
    операції."""
    bound = lang or get_language()
    return lambda key, **kwargs: _translate(bound, key, **kwargs)


def all_translations_for_current_lang(
    lang: Optional[str] = None, keys: Optional[Iterable[str]] = None,
) -> dict[str, str]:
    """Плаский {key: text} для поточної мови - серіалізується в JSON
    і вбудовується в кожну HTML-сторінку як window.I18N для JS-коду
    (JS не має доступу до Jinja2-функції t() напряму)."""
    lang = lang or get_language()
    wanted = TRANSLATIONS.keys() if keys is None else keys
    return {k: _translate(lang, k) for k in wanted if k in TRANSLATIONS}
