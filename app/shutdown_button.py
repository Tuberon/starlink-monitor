"""
GPIO-кнопка виключення Pi (pull-up, LOW = натиснуто). Утримання довше
SHUTDOWN_BUTTON_HOLD_SEC -> systemctl poweroff + подія + Telegram.
Окремий сервіс, виходить одразу якщо SHUTDOWN_BUTTON_GPIO_PIN=0.
Використовує gpiod (character-device API, не застарілий RPi.GPIO) -
детальна v1/v2-сумісна логіка в app/gpio_utils.py (спільна з display.py).
"""
import logging
import subprocess
import time

from app import config, db, gpio_utils, telegram_notify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("shutdown_button")

POLL_INTERVAL_SEC = 0.1  # як часто перевіряти стан піна під час очікування


def watch_button():
    pin = config.SHUTDOWN_BUTTON_GPIO_PIN
    if not pin or pin <= 0:
        logger.info("SHUTDOWN_BUTTON_GPIO_PIN не налаштовано (0) - кнопка вимкнена, завершення")
        return

    try:
        import gpiod  # noqa: F401 - лише перевірка наявності бібліотеки
    except ImportError:
        logger.error("Бібліотека gpiod не встановлена - кнопка виключення не працюватиме")
        return

    logger.info("Слухаю кнопку виключення на GPIO%d, утримання %.1fс", pin, config.SHUTDOWN_BUTTON_HOLD_SEC)

    try:
        get_value, release = gpio_utils.open_input_line(pin, "starlink-shutdown-button")
    except Exception as e:
        logger.error("Не вдалося ініціалізувати GPIO%d: %s", pin, e)
        return

    pressed_since = None
    triggered = False

    try:
        while True:
            try:
                value = get_value()
            except Exception as e:
                logger.warning("Помилка читання GPIO%d: %s", pin, e)
                time.sleep(1)
                continue

            is_pressed = (value == 0)  # pull-up: натиснуто = LOW

            if is_pressed:
                if pressed_since is None:
                    pressed_since = time.time()
                elif not triggered and (time.time() - pressed_since) >= config.SHUTDOWN_BUTTON_HOLD_SEC:
                    triggered = True
                    _trigger_shutdown(pin)
            else:
                pressed_since = None
                triggered = False

            time.sleep(POLL_INTERVAL_SEC)
    finally:
        try:
            release()
        except Exception:
            pass


def _trigger_shutdown(pin: int):
    logger.warning("Кнопка виключення утримана %.1fс на GPIO%d - виконую poweroff", config.SHUTDOWN_BUTTON_HOLD_SEC, pin)
    try:
        db.init_db()
        db.insert_event("pi_shutdown", f"Виключення через фізичну кнопку (GPIO{pin})", success=True)
    except Exception as e:
        logger.warning("Не вдалося записати подію в БД: %s", e)

    try:
        telegram_notify.send_message(f"⏻ Raspberry Pi вимикається через фізичну кнопку (GPIO{pin})")
    except Exception as e:
        logger.warning("Не вдалося надіслати Telegram-сповіщення: %s", e)

    try:
        subprocess.run(["sudo", "systemctl", "poweroff"], timeout=10)
    except Exception as e:
        logger.error("Не вдалося виконати poweroff: %s", e)


def main():
    watch_button()


if __name__ == "__main__":
    main()
