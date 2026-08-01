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
import socket
import threading
import time
from typing import Any, Optional

from app import config, db

logger = logging.getLogger("speedtest_runner")

# Прив'язка до wlan0 (WiFi Starlink) - без цього speedtest покладається
# на дефолтний маршрут ОС, який ЗАЗВИЧАЙ веде через Starlink (нижчий
# route-metric), але не гарантовано: якщо тест випадково спрацює саме
# під час тимчасової WAN-failover-корекції маршруту (wan_failover_
# check.sh), результат вимірював би домашню eth0-мережу під виглядом
# "швидкості Starlink" - оманливо для самої мети цього модуля
# (порівняння заявленої dish-швидкості з РЕАЛЬНОЮ Starlink-швидкістю).
_WLAN0_IFACE = b"wlan0"


class _WLAN0BoundSocket(socket.socket):
    """SO_BINDTODEVICE відразу після створення кожного сокета. Пакет
    speedtest-cli не надає API для injecting кастомного сокета/сесії
    (на відміну від requests у telegram_notify.py) - єдиний надійний
    спосіб форсувати конкретний інтерфейс для сторонньої бібліотеки,
    яку не контролюємо напряму, це тимчасовий monkey-patch socket.socket
    (speedtest.py створює сокети і напряму, і через socket.create_
    connection(), яка теж зрештою викликає socket.socket() - обидва
    шляхи покриваються). Вимагає CAP_NET_RAW (той самий, що вже
    надано starlink-monitor.service для eth0-прив'язки Telegram) -
    без нього просто не прив'язується, без падіння."""
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        try:
            self.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, _WLAN0_IFACE)
        except (PermissionError, OSError) as e:
            logger.debug("SO_BINDTODEVICE(wlan0) не вдався для speedtest: %s", e)


def run_once() -> dict[str, Any]:
    """Один прогін speedtest. Ніколи не кидає виняток назовні - помилка
    кладеться в поле error, success=False."""
    result: dict[str, Any] = {"ts": time.time(), "success": False}
    try:
        import speedtest
    except ImportError:
        result["error"] = "пакет speedtest-cli не встановлено"
        logger.error(result["error"])
        return result

    # Тимчасово підміняємо socket.socket ГЛОБАЛЬНО на час виклику
    # speedtest-бібліотеки - обов'язково відновлюємо в finally: цей
    # самий процес (monitor.py) паралельно запускає telegram_notify.py,
    # яка НАВПАКИ прив'язується до eth0 - без відновлення "протекла" б
    # підміна зламала протилежну прив'язку для решти процесу.
    original_socket = socket.socket
    socket.socket = _WLAN0BoundSocket  # type: ignore[misc]
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
    finally:
        socket.socket = original_socket  # type: ignore[misc]

    return result


def run_forever(stop_event: Optional[threading.Event] = None) -> None:
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
