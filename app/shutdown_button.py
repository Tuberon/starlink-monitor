"""
GPIO-кнопка виключення Pi (pull-up, LOW = натиснуто). Утримання довше
SHUTDOWN_BUTTON_HOLD_SEC -> systemctl poweroff + подія + Telegram.
Окремий сервіс, виходить одразу якщо SHUTDOWN_BUTTON_GPIO_PIN=0.
Використовує gpiod (character-device API, не застарілий RPi.GPIO).
"""
import logging
import subprocess
import time

from app import config, db, telegram_notify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("shutdown_button")

POLL_INTERVAL_SEC = 0.1  # як часто перевіряти стан піна під час очікування
GPIO_CHIP = "/dev/gpiochip0"


def _find_gpio_chip():
    """На різних версіях Raspberry Pi OS/ядра основний GPIO-чіп може
    бути gpiochip0 або інший номер (напр. після додавання HAT-плат,
    які теж реєструють свої chip'и). Перебираємо перші кілька."""
    import os
    for i in range(6):
        path = f"/dev/gpiochip{i}"
        if os.path.exists(path):
            return path
    return GPIO_CHIP


def _init_line_v2(gpiod, chip_path, pin):
    """gpiod >= 2.0: gpiod.request_lines() з LineSettings, значення
    читається через request.get_value(pin) (повертає Value.ACTIVE/INACTIVE,
    не 0/1 як у v1)."""
    from gpiod.line import Direction, Bias

    request = gpiod.request_lines(
        chip_path,
        consumer="starlink-shutdown-button",
        config={pin: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)},
    )

    def get_value():
        from gpiod.line import Value
        return 0 if request.get_value(pin) == Value.INACTIVE else 1

    def release():
        request.release()

    return get_value, release


def _init_line_v1(gpiod, chip_path, pin):
    """gpiod < 2.0 (застарілий API): chip.get_line() + line.request()."""
    chip = gpiod.Chip(chip_path)
    line = chip.get_line(pin)
    line.request(consumer="starlink-shutdown-button", type=gpiod.LINE_REQ_DIR_IN,
                 flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP)

    def get_value():
        return line.get_value()

    def release():
        line.release()

    return get_value, release


def watch_button():
    pin = config.SHUTDOWN_BUTTON_GPIO_PIN
    if not pin or pin <= 0:
        logger.info("SHUTDOWN_BUTTON_GPIO_PIN не налаштовано (0) - кнопка вимкнена, завершення")
        return

    try:
        import gpiod
    except ImportError:
        logger.error("Бібліотека gpiod не встановлена - кнопка виключення не працюватиме")
        return

    chip_path = _find_gpio_chip()
    logger.info("Слухаю кнопку виключення на GPIO%d (%s), утримання %.1fс", pin, chip_path, config.SHUTDOWN_BUTTON_HOLD_SEC)

    # gpiod v2.x видалив Chip.get_line() (звідси hasattr-перевірка) на
    # користь request_lines() - API повністю несумісний зі старим v1.x.
    # Raspberry Pi OS Bookworm+ ставить v2 через apt python3-libgpiod.
    is_v2 = not hasattr(gpiod.Chip, "get_line")

    try:
        if is_v2:
            get_value, release = _init_line_v2(gpiod, chip_path, pin)
        else:
            get_value, release = _init_line_v1(gpiod, chip_path, pin)
    except Exception as e:
        logger.error("Не вдалося ініціалізувати GPIO%d (gpiod %s API): %s",
                     pin, "v2" if is_v2 else "v1", e)
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
