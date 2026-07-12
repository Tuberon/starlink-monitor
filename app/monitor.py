"""
Фоновий процес: опитує dish кожні POLL_INTERVAL_SEC секунд і роутерний
компонент Starlink Mini раз на ~60с, пише в БД, і виконує автоматичний
reboot ВСЬОГО Mini (через reboot_dish() на адресу dish - dish і router
фізично один пристрій, тож reboot dish перезавантажує обидва) у ТРЬОХ
випадках (з єдиним захистом від reboot-loop через MIN_REBOOT_INTERVAL_SEC):

1. Watchdog: dish не відповідає MAX_CONSECUTIVE_FAILURES разів поспіль.
2. Update-ready (dish): dish повідомляє, що оновлення ПЗ вже завантажене
   й готове до встановлення (software_update_state == REBOOT_REQUIRED,
   або alerts.install_pending) - просто чекає на reboot.
3. Update-ready (router): роутерний компонент повідомляє те саме своєю
   окремою схемою (WifiSoftwareUpdateState == REBOOT_PENDING, або
   WifiAlerts.install_pending) - у роутера окремий цикл оновлення від dish.

Також відстежує зміни стану оновлення ПЗ (dish і router окремо) та
активних попереджень і пише кожну зміну окремою подією в журнал (events) -
щоб у веб-інтерфейсі було видно повну історію проходження оновлення
та появу/зникнення попереджень, а не лише момент, коли watchdog
ініціює reboot.
"""
import logging
import time

import psutil

from app import config, db
from app.starlink_client import StarlinkClient
from app.system_metrics import get_system_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("monitor")

# Людські назви станів оновлення для журналу подій (той самий enum
# SpaceX.API.Device.SoftwareUpdateState, назви - з starlink_client.py)
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

# Людські назви alert-прапорців для журналу подій (ті самі 19 полів
# message DishAlerts, що й у starlink_client.ALERT_FIELD_NAMES)
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


class Watchdog:
    def __init__(self):
        self.client = StarlinkClient()
        self.consecutive_failures = 0
        self.last_reboot_ts = 0.0
        # Попередні значення для детекції змін стану оновлення/попереджень.
        # None означає "ще не бачили жодного online-статусу" - перший
        # реальний статус теж логуємо, якщо він не порожній/не IDLE-без-алертів.
        self.prev_update_state = None
        self.prev_alerts = None
        self.prev_router_update_state = None
        self.prev_router_alerts = None

    def poll_once(self):
        status = self.client.get_status()
        db.insert_metric(status.to_dict())

        if status.online:
            if self.consecutive_failures > 0:
                logger.info("Dish знову online після %d невдалих спроб", self.consecutive_failures)
            self.consecutive_failures = 0
            self._log_update_state_change(status)
            self._log_alerts_change(status)
            self._maybe_reboot_for_update(status)
        else:
            self.consecutive_failures += 1
            logger.warning(
                "Dish недоступний (%d/%d): %s",
                self.consecutive_failures,
                config.MAX_CONSECUTIVE_FAILURES,
                status.error,
            )
            self._maybe_reboot()

        if status.online and status.obstruction_fraction > config.OBSTRUCTION_WARN_FRACTION:
            db.insert_event(
                "obstruction_warning",
                f"Фракція обструкції {status.obstruction_fraction:.2%} перевищує поріг "
                f"{config.OBSTRUCTION_WARN_FRACTION:.2%}",
                success=True,
            )

        return status

    def _log_update_state_change(self, status):
        """Пише подію в журнал кожного разу, коли змінюється стан оновлення ПЗ dish."""
        state = status.update_state or "SOFTWARE_UPDATE_STATE_UNKNOWN"
        if state == self.prev_update_state:
            return

        label = UPDATE_STATE_LABELS.get(state, state)
        detail = ""
        if state in ("FETCHING", "PRE_CHECK", "WRITING", "POST_CHECK") and status.update_progress_pct:
            detail = f" ({status.update_progress_pct:.0f}%)"

        # Перший запис після старту сервісу (prev_update_state is None) логуємо
        # лише якщо стан не "нейтральний" (IDLE) - інакше журнал засмічується
        # одноразовим повідомленням "IDLE" при кожному рестарті сервісу.
        if self.prev_update_state is not None or state != "IDLE":
            db.insert_event(
                "update_state_change",
                f"Стан оновлення ПЗ: {label}{detail}",
                success=(state not in ("FAULTED",)),
            )
        self.prev_update_state = state

    def _log_alerts_change(self, status):
        """Пише окрему подію для кожного попередження, яке з'явилось або зникло."""
        current = set(status.active_alerts or [])
        previous = self.prev_alerts

        # Перший виклик (previous is None): не генеруємо подій "з'явилось",
        # бо це вже поточний стан на момент старту сервісу, а не нова зміна.
        if previous is not None:
            appeared = current - previous
            resolved = previous - current
            for alert in sorted(appeared):
                label = ALERT_LABELS.get(alert, alert)
                db.insert_event(
                    "dish_alert",
                    f"Нове попередження dish: {label}",
                    success=False,
                )
            for alert in sorted(resolved):
                label = ALERT_LABELS.get(alert, alert)
                db.insert_event(
                    "dish_alert_resolved",
                    f"Попередження знято: {label}",
                    success=True,
                )

        self.prev_alerts = current

    def poll_system_metrics(self):
        try:
            metrics = get_system_metrics()
            db.insert_system_metric(metrics)
        except Exception as e:
            logger.warning("Не вдалося зібрати системні метрики: %s", e)

    def poll_router(self):
        """Опитує окремий роутерний компонент Starlink Mini (інша адреса,
        ніж dish). Версія прошивки роутера змінюється рідко, тому зберігаємо
        лише останній відомий стан (без історії/графіків)."""
        try:
            info = self.client.get_router_info()
            db.set_router_status(info.to_dict())
            if not info.online:
                logger.debug("Роутер недоступний: %s", info.error)
                return
            self._log_router_update_state_change(info)
            self._log_router_alerts_change(info)
            self._maybe_reboot_for_router_update(info)
        except Exception as e:
            logger.warning("Не вдалося опитати роутер: %s", e)

    def _log_router_update_state_change(self, info):
        """Пише подію в журнал кожного разу, коли змінюється стан оновлення ПЗ роутера."""
        state = info.update_state or "NOT_RUN"
        if state == self.prev_router_update_state:
            return

        label = ROUTER_UPDATE_STATE_LABELS.get(state, state)
        detail = ""
        if state in ("DOWNLOADING_UPDATE_IMAGE", "FLASHING") and info.update_progress_pct:
            detail = f" ({info.update_progress_pct:.0f}%)"

        if self.prev_router_update_state is not None or state != "NOT_RUN":
            db.insert_event(
                "router_update_state_change",
                f"Стан оновлення ПЗ роутера: {label}{detail}",
                success=("FAILED" not in state and "ILLEGAL" not in state),
            )
        self.prev_router_update_state = state

    def _log_router_alerts_change(self, info):
        """Пише окрему подію для кожного попередження роутера, яке з'явилось або зникло."""
        current = set(info.active_alerts or [])
        previous = self.prev_router_alerts

        if previous is not None:
            appeared = current - previous
            resolved = previous - current
            for alert in sorted(appeared):
                label = ROUTER_ALERT_LABELS.get(alert, alert)
                db.insert_event(
                    "router_alert",
                    f"Нове попередження роутера: {label}",
                    success=False,
                )
            for alert in sorted(resolved):
                label = ROUTER_ALERT_LABELS.get(alert, alert)
                db.insert_event(
                    "router_alert_resolved",
                    f"Попередження роутера знято: {label}",
                    success=True,
                )

        self.prev_router_alerts = current

    def _maybe_reboot_for_router_update(self, info):
        """Автоматичний reboot усього Starlink Mini, коли роутерний компонент
        повідомляє про готове до встановлення оновлення (REBOOT_PENDING або
        install_pending). Reboot виконується через dish_addr - dish і router
        фізично один пристрій, тож це перезавантажує обидва компоненти."""
        if not db.get_auto_reboot_enabled():
            return

        update_ready = info.update_state == "REBOOT_PENDING" or info.update_install_pending
        if not update_ready:
            return

        now = time.time()
        if now - self.last_reboot_ts < config.MIN_REBOOT_INTERVAL_SEC:
            logger.info(
                "Оновлення роутера готове до встановлення, але пропускаю авто-reboot: "
                "останній reboot був %.0f с тому (мін. інтервал %d с)",
                now - self.last_reboot_ts,
                config.MIN_REBOOT_INTERVAL_SEC,
            )
            return

        reason = info.update_state if info.update_state == "REBOOT_PENDING" else "install_pending"
        logger.warning("Оновлення ПЗ роутера готове до встановлення (%s) — ініціюю reboot Starlink Mini", reason)
        db.insert_event(
            "watchdog_trigger",
            f"Оновлення ПЗ роутера готове до встановлення ({reason}) — ініціюю reboot",
            success=True,
        )
        ok, msg = self.client.reboot_dish()
        db.insert_event("dish_reboot", msg, success=ok)
        if ok:
            self.last_reboot_ts = now

    def _maybe_reboot_for_update(self, status):
        if not db.get_auto_reboot_enabled():
            return

        update_ready = status.update_state == "REBOOT_REQUIRED" or status.update_install_pending
        if not update_ready:
            return

        now = time.time()
        if now - self.last_reboot_ts < config.MIN_REBOOT_INTERVAL_SEC:
            logger.info(
                "Оновлення готове до встановлення, але пропускаю авто-reboot: "
                "останній reboot був %.0f с тому (мін. інтервал %d с)",
                now - self.last_reboot_ts,
                config.MIN_REBOOT_INTERVAL_SEC,
            )
            return

        reason = status.update_state if status.update_state == "REBOOT_REQUIRED" else "install_pending"
        logger.warning("Оновлення ПЗ dish готове до встановлення (%s) — ініціюю reboot", reason)
        db.insert_event(
            "watchdog_trigger",
            f"Оновлення ПЗ готове до встановлення ({reason}) — ініціюю reboot",
            success=True,
        )
        ok, msg = self.client.reboot_dish()
        db.insert_event("dish_reboot", msg, success=ok)
        if ok:
            self.last_reboot_ts = now

    def _maybe_reboot(self):
        if self.consecutive_failures < config.MAX_CONSECUTIVE_FAILURES:
            return

        now = time.time()
        if now - self.last_reboot_ts < config.MIN_REBOOT_INTERVAL_SEC:
            logger.info(
                "Пропускаю авто-reboot: останній reboot був %.0f с тому (мін. інтервал %d с)",
                now - self.last_reboot_ts,
                config.MIN_REBOOT_INTERVAL_SEC,
            )
            return

        logger.warning("Ініціюю автоматичний reboot dish після %d невдалих спроб", self.consecutive_failures)
        db.insert_event(
            "watchdog_trigger",
            f"{self.consecutive_failures} послідовних невдалих опитувань — ініціюю reboot",
            success=True,
        )
        ok, msg = self.client.reboot_dish()
        db.insert_event("dish_reboot", msg, success=ok)
        if ok:
            self.last_reboot_ts = now
            self.consecutive_failures = 0

    def run_forever(self):
        db.init_db()
        logger.info("Starlink watchdog запущено. Опитування кожні %d с.", config.POLL_INTERVAL_SEC)
        # "Прогрів" psutil.cpu_percent: перший виклик без базового заміру
        # завжди повертає 0.0, тому робимо його тут і відкидаємо результат.
        psutil.cpu_percent(interval=None)
        last_prune = 0
        last_router_poll = 0  # 0 гарантує негайне перше опитування роутера
        while True:
            try:
                self.poll_once()
            except Exception as e:
                logger.exception("Неочікувана помилка в циклі опитування: %s", e)

            self.poll_system_metrics()

            # Роутерний компонент опитуємо рідше (раз на ~60с), бо його
            # версія прошивки змінюється нечасто, і зайве навантаження
            # на WiFi-канал непотрібне при опитуванні dish кожні 10с.
            if time.time() - last_router_poll > 60:
                self.poll_router()
                last_router_poll = time.time()

            if time.time() - last_prune > 3600:
                try:
                    db.prune_old()
                except Exception:
                    logger.exception("Помилка очищення старих записів")
                last_prune = time.time()

            time.sleep(config.POLL_INTERVAL_SEC)


def main():
    Watchdog().run_forever()


if __name__ == "__main__":
    main()
