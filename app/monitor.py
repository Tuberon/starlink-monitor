"""
Фоновий watchdog: опитує dish (POLL_INTERVAL_SEC) і router (~60с),
пише в БД, авто-reboot Mini при 3 умовах (watchdog failures,
update-ready dish, update-ready router) - див. docs/architecture.md.
Логує зміни стану/попереджень в events, дублює ключові події в
Telegram (не блокує цикл при помилках відправки).
"""
import json
import logging
import time

import psutil

from app import config, db, telegram_notify
from app.labels import ALERT_LABELS, ROUTER_ALERT_LABELS, ROUTER_UPDATE_STATE_LABELS, UPDATE_STATE_LABELS
from app.starlink_client import StarlinkClient
from app.system_metrics import get_system_metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("monitor")


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
        # Останній відомий dish_id - потрібен, щоб прив'язати опитування
        # роутера (окремий цикл, без власного dish_id у RouterInfo) до
        # того самого фізичного Mini в таблиці known_devices.
        self.last_known_dish_id = None

    def _notify(self, text: str):
        """Безпечна відправка Telegram-сповіщення - ніколи не кидає виняток
        назовні і не блокує основний цикл моніторингу."""
        try:
            ok, msg = telegram_notify.send_message(text)
            if not ok and msg not in ("Telegram сповіщення вимкнені", "Не вказано bot token", "Не вказано жодного chat_id"):
                logger.warning("Telegram сповіщення не надіслано: %s", msg)
        except Exception as e:
            logger.warning("Помилка відправки Telegram-сповіщення: %s", e)

    def poll_once(self):
        status = self.client.get_status()
        db.insert_metric(status.to_dict())

        if status.online:
            if self.consecutive_failures > 0:
                logger.info("Dish знову online після %d невдалих спроб", self.consecutive_failures)
                self._notify(f"✅ Dish знову online (після {self.consecutive_failures} невдалих спроб)")
            self.consecutive_failures = 0
            self._notify_first_dish_connection(status)
            if status.dish_id:
                self.last_known_dish_id = status.dish_id
            db.upsert_known_device_dish(status.dish_id, status.hardware_version, status.software_version)
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
            if state == "REBOOT_REQUIRED":
                self._notify(f"🔄 Оновлення ПЗ dish готове — очікує перезавантаження{detail}")
            elif state == "FAULTED":
                self._notify(f"⚠️ Помилка оновлення ПЗ dish: {label}")
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
                self._notify(f"⚠️ Нове попередження dish: {label}")
            for alert in sorted(resolved):
                label = ALERT_LABELS.get(alert, alert)
                db.insert_event(
                    "dish_alert_resolved",
                    f"Попередження знято: {label}",
                    success=True,
                )

        self.prev_alerts = current

    def _notify_first_dish_connection(self, status):
        """Надсилає в Telegram ID тарілки один раз - лише при першому
        підключенні кожної конкретної тарілки (за dish_id) до Pi. Усі
        колись бачені ID зберігаються в settings (JSON-список), тож
        переживають рестарт сервісу; при підключенні НОВОЇ тарілки
        (ID, якого ще не було в списку) сповіщення прийде знову, навіть
        якщо до цього вже підключались дві чи більше різних тарілок."""
        if not status.dish_id:
            return

        raw = db.get_setting("known_dish_ids", "[]")
        try:
            known_ids = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            known_ids = []

        if status.dish_id in known_ids:
            return

        known_ids.append(status.dish_id)
        db.set_setting("known_dish_ids", json.dumps(known_ids, ensure_ascii=False))
        db.insert_event("dish_connected", f"Підключено Starlink Mini, ID: {status.dish_id}", success=True)
        self._notify(f"📡 Підключено Starlink Mini (тарілка), ID: {status.dish_id}")

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
            if self.last_known_dish_id:
                db.upsert_known_device_router(self.last_known_dish_id, info.hardware_version, info.software_version)
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
            is_failure = "FAILED" in state or "ILLEGAL" in state
            db.insert_event(
                "router_update_state_change",
                f"Стан оновлення ПЗ роутера: {label}{detail}",
                success=(not is_failure),
            )
            if state == "REBOOT_PENDING":
                self._notify(f"🔄 Оновлення ПЗ роутера готове — очікує перезавантаження{detail}")
            elif is_failure:
                self._notify(f"⚠️ Помилка оновлення ПЗ роутера: {label}")
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
                self._notify(f"⚠️ Нове попередження роутера: {label}")
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
            self._notify(f"🔁 Starlink Mini автоматично перезавантажено (оновлення ПЗ роутера готове: {reason})")
        else:
            self._notify(f"❌ Не вдалося перезавантажити Starlink Mini (оновлення ПЗ роутера готове): {msg}")

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
            self._notify(f"🔁 Starlink Mini автоматично перезавантажено (оновлення ПЗ dish готове: {reason})")
        else:
            self._notify(f"❌ Не вдалося перезавантажити Starlink Mini (оновлення ПЗ dish готове): {msg}")

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

        failures = self.consecutive_failures
        logger.warning("Ініціюю автоматичний reboot dish після %d невдалих спроб", failures)
        db.insert_event(
            "watchdog_trigger",
            f"{failures} послідовних невдалих опитувань — ініціюю reboot",
            success=True,
        )
        ok, msg = self.client.reboot_dish()
        db.insert_event("dish_reboot", msg, success=ok)
        # last_reboot_ts оновлюється завжди, навіть при невдачі: якщо dish
        # ще перезавантажується з попередньої спроби, команда reboot теж
        # провалиться (grpcurl: connection refused) - без цього watchdog
        # намагався б "перезавантажити" вже перезавантажуваний dish щоцикл,
        # ігноруючи MIN_REBOOT_INTERVAL_SEC (справжній reboot-loop).
        self.last_reboot_ts = now
        if ok:
            self.consecutive_failures = 0
            self._notify(f"🔁 Starlink Mini автоматично перезавантажено (dish не відповідав {failures} спроб поспіль)")

    def run_forever(self):
        db.init_db()
        logger.info("Starlink watchdog запущено. Опитування кожні %d с.", config.POLL_INTERVAL_SEC)

        from app.telegram_bot import TelegramBot
        telegram_bot = TelegramBot()
        telegram_bot.start()

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
