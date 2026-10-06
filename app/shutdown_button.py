"""GPIO-кнопка виключення Pi (pull-up, LOW = натиснуто): утримання довше SHUTDOWN_BUTTON_HOLD_SEC ->
systemctl poweroff + подія + Telegram. Окремий сервіс; одразу виходить при SHUTDOWN_BUTTON_GPIO_PIN=0 і
при DISPLAY_ENABLED=1 (тоді кнопку обробляє display.py: коротке — підсвітка, довге — вимкнення Pi; два
процеси не можуть тримати один вхід). Використовує gpiod (character-device API, не RPi.GPIO);
v1/v2-сумісність і детекція short/long — у app/gpio_utils.py.
"""
import logging
import time

from app import config, gpio_utils, log_redact, pi_power

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log_redact.install()   # токен бота ніколи не потрапляє в журнал (див. app/log_redact.py)
logger = logging.getLogger("shutdown_button")


def watch_button() -> None:
    pin = config.SHUTDOWN_BUTTON_GPIO_PIN
    if not pin or pin <= 0:
        logger.info("SHUTDOWN_BUTTON_GPIO_PIN не налаштовано (0) - кнопка вимкнена, завершення")
        return

    if config.DISPLAY_ENABLED:
        logger.info(
            "DISPLAY_ENABLED=1 - кнопка GPIO%d обробляється всередині display.py "
            "(коротке=підсвітка, довге=вимкнення) - цей сервіс не активний, "
            "щоб не конкурувати за той самий GPIO-пін", pin
        )
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

    tracker = gpio_utils.ButtonPressTracker(config.SHUTDOWN_BUTTON_HOLD_SEC)

    try:
        while True:
            try:
                value = get_value()
            except Exception as e:
                logger.warning("Помилка читання GPIO%d: %s", pin, e)
                time.sleep(1)
                continue

            if tracker.poll(value) == "long_press":
                pi_power.shutdown_from_button(pin)

            time.sleep(config.SHUTDOWN_BUTTON_POLL_INTERVAL_SEC)
    finally:
        try:
            release()
        except Exception as e:
            # Той самий принцип, що в display.py: не перекривати
            # оригінальну причину завершення, лише debug-слід для
            # рідкісного edge-case.
            logger.debug("Не вдалося звільнити GPIO кнопки виключення при завершенні: %s", e)


def main() -> None:
    watch_button()


if __name__ == "__main__":
    main()
