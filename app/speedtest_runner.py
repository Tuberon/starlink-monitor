"""
Періодичний реальний speedtest (не телеметрія dish, а фактичний тест
пропускної здатності до інтернету через speedtest.net). Телеметрія dish
(downlink_mbps/uplink_mbps в app/starlink_client.py) показує "заявлений"
канал з точки зору самого dish, не реальну користувацьку швидкість крізь
увесь маршрут (WiFi -> router -> dish -> супутник -> інтернет). Цей
модуль дає незалежне вимірювання для порівняння "заявлена vs реальна".

Працює як окремий потік (не окремий systemd-сервіс) - запускається з
monitor.py поруч з Telegram-ботом, лише якщо SPEEDTEST_ENABLED=1.
Один прогін speedtest займає 10-30+ секунд і навантажує WiFi-радіомодуль
(конкурує за радіо-час з локальним опитуванням dish/router), тому:
- вимкнено за замовчуванням
- працює в окремому потоці, щоб не затримувати watchdog-цикл
- інтервал типово 1800с (двічі/год), не частіше
"""
import logging
import threading
import time

from app import config, db

logger = logging.getLogger("speedtest_runner")


def run_once() -> dict:
    """Один прогін speedtest. Ніколи не кидає виняток назовні - помилка
    кладеться в поле error, success=False."""
    result = {"ts": time.time(), "success": False}
    try:
        import speedtest
    except ImportError:
        result["error"] = "пакет speedtest-cli не встановлено"
        logger.error(result["error"])
        return result

    try:
        st = speedtest.Speedtest()
        st.get_best_server()
        download_bps = st.download()
        upload_bps = st.upload()
        server = st.results.server or {}

        result.update({
            "download_mbps": round(download_bps / 1_000_000, 2),
            "upload_mbps": round(upload_bps / 1_000_000, 2),
            "ping_ms": round(st.results.ping, 1) if st.results.ping else None,
            "server_name": f"{server.get('name', '')}, {server.get('country', '')}".strip(", "),
            "success": True,
        })
        logger.info(
            "Speedtest: %.1f Мбіт/с ⬇ / %.1f Мбіт/с ⬆ / %.0fмс ping (сервер: %s)",
            result["download_mbps"], result["upload_mbps"], result["ping_ms"] or 0,
            result["server_name"],
        )
    except Exception as e:
        result["error"] = str(e)
        logger.warning("Speedtest не вдався: %s", e)

    return result


def run_forever(stop_event: threading.Event = None):
    """Цикл: раз на SPEEDTEST_INTERVAL_SEC запускає run_once() і зберігає
    результат у БД. stop_event дозволяє коректно зупинити потік ззовні
    (той самий патерн, що telegram_bot._stop_event)."""
    if not config.SPEEDTEST_ENABLED:
        logger.info("SPEEDTEST_ENABLED=0 - періодичний speedtest вимкнено")
        return

    logger.info("Speedtest увімкнено, інтервал %d с (%.1f разів/год)",
                config.SPEEDTEST_INTERVAL_SEC, 3600 / config.SPEEDTEST_INTERVAL_SEC)

    while True:
        if stop_event and stop_event.is_set():
            return
        result = run_once()
        try:
            db.insert_speedtest_result(result)
        except Exception as e:
            logger.warning("Не вдалося зберегти результат speedtest у БД: %s", e)

        # Перевіряємо stop_event періодично під час сну, щоб зупинка
        # сервісу не чекала повний SPEEDTEST_INTERVAL_SEC (до 30 хв).
        slept = 0
        while slept < config.SPEEDTEST_INTERVAL_SEC:
            if stop_event and stop_event.is_set():
                return
            time.sleep(min(5, config.SPEEDTEST_INTERVAL_SEC - slept))
            slept += 5
