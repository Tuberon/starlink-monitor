"""
Спільна gpiod v1/v2 сумісна логіка читання цифрового GPIO-входу
(pull-up, LOW = натиснуто) - винесено з shutdown_button.py, бо той
самий патерн тепер потрібен і в display.py (кнопка підсвітки).
"""
import os


def find_gpio_chip():
    """На різних версіях Raspberry Pi OS/ядра основний GPIO-чіп може
    бути gpiochip0 або інший номер (напр. після додавання HAT-плат,
    які теж реєструють свої chip'и). Перебираємо перші кілька."""
    for i in range(6):
        path = f"/dev/gpiochip{i}"
        if os.path.exists(path):
            return path
    return "/dev/gpiochip0"


def _init_line_v2(gpiod, chip_path, pin, consumer):
    """gpiod >= 2.0: gpiod.request_lines() з LineSettings, значення
    читається через request.get_value(pin) (повертає Value.ACTIVE/INACTIVE,
    не 0/1 як у v1)."""
    from gpiod.line import Direction, Bias

    request = gpiod.request_lines(
        chip_path,
        consumer=consumer,
        config={pin: gpiod.LineSettings(direction=Direction.INPUT, bias=Bias.PULL_UP)},
    )

    def get_value():
        from gpiod.line import Value
        return 0 if request.get_value(pin) == Value.INACTIVE else 1

    def release():
        request.release()

    return get_value, release


def _init_line_v1(gpiod, chip_path, pin, consumer):
    """gpiod < 2.0 (застарілий API): chip.get_line() + line.request()."""
    chip = gpiod.Chip(chip_path)
    line = chip.get_line(pin)
    line.request(consumer=consumer, type=gpiod.LINE_REQ_DIR_IN,
                 flags=gpiod.LINE_REQ_FLAG_BIAS_PULL_UP)

    def get_value():
        return line.get_value()

    def release():
        line.release()

    return get_value, release


def open_input_line(pin: int, consumer: str):
    """Відкриває GPIO-пін як цифровий вхід з pull-up (сумісно з gpiod
    v1 і v2). Повертає (get_value, release) або кидає виняток, якщо
    gpiod не встановлено чи ініціалізація провалилась - виклик має
    сам обробити except."""
    import gpiod

    chip_path = find_gpio_chip()
    # gpiod v2.x видалив Chip.get_line() (звідси hasattr-перевірка) на
    # користь request_lines() - API повністю несумісний зі старим v1.x.
    # Raspberry Pi OS Bookworm+ ставить v2 через apt python3-libgpiod.
    is_v2 = not hasattr(gpiod.Chip, "get_line")
    if is_v2:
        return _init_line_v2(gpiod, chip_path, pin, consumer)
    return _init_line_v1(gpiod, chip_path, pin, consumer)


class ButtonPressTracker:
    """Відстежує натискання/утримання кнопки (pull-up, LOW=натиснуто) -
    чиста, стейтфул структура без залежності від реального GPIO, легко
    тестується подачею довільної послідовності значень через poll().

    poll(value) повертає:
      - "long_press"  - утримано довше hold_sec (спрацьовує РІВНО ОДИН
                         раз, у момент досягнення порогу, поки кнопка
                         й далі утримується)
      - "short_press" - відпущено ДО досягнення порогу утримання
      - None          - нічого не сталось (ідле, чи утримання ще
                         триває нижче порогу, чи відпущення ПІСЛЯ вже
                         спрацьованого long_press - не подвійне
                         спрацювання)

    Використовується і в shutdown_button.py (лише long_press), і в
    display.py (short_press -> підсвітка, long_press -> shutdown) -
    та сама фізична кнопка, дві різні дії залежно від тривалості.
    """
    def __init__(self, hold_sec: float):
        self.hold_sec = hold_sec
        self._pressed_since = None
        self._triggered = False

    def poll(self, value, now: float = None):
        import time as _time
        now = now if now is not None else _time.time()
        is_pressed = (value == 0)

        if is_pressed:
            if self._pressed_since is None:
                self._pressed_since = now
            elif not self._triggered and (now - self._pressed_since) >= self.hold_sec:
                self._triggered = True
                return "long_press"
        else:
            was_pressed = self._pressed_since is not None
            was_triggered = self._triggered
            self._pressed_since = None
            self._triggered = False
            if was_pressed and not was_triggered:
                return "short_press"
        return None
