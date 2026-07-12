"""
Фоновий процес: опитує dish кожні POLL_INTERVAL_SEC секунд,
пише в БД, і якщо dish не відповідає MAX_CONSECUTIVE_FAILURES разів
поспіль — виконує автоматичний reboot dish (з захистом від reboot-loop
через MIN_REBOOT_INTERVAL_SEC).
"""
import logging
import time

from app import config, db
from app.starlink_client import StarlinkClient

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

    def poll_once(self):
        status = self.client.get_status()
        db.insert_metric(status.to_dict())

        if status.online:
            if self.consecutive_failures > 0:
                logger.info("Dish знову online після %d невдалих спроб", self.consecutive_failures)
            self.consecutive_failures = 0
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
        last_prune = 0
        while True:
            try:
                self.poll_once()
            except Exception as e:
                logger.exception("Неочікувана помилка в циклі опитування: %s", e)

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
