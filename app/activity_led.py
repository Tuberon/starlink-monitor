"""GPIO-світлодіод, що коротко блимає при кожному реальному записі в SQLite (метрики, events,
router-status, backup, VACUUM) — індикація активності SD, як activity-LED диска. Опційно, вимкнено за
замовчуванням (ACTIVITY_LED_PIN=0). Не блокує потік: blink() вмикає LED і запускає threading.Timer, що
вимикає його через ACTIVITY_LED_BLINK_MS (виклик із db.py на кожен commit не має додавати затримку).
gpiod як у gpio_utils.py, але open_output_line() (LED — вихід).
"""
import logging
import threading
from typing import Callable, Optional

from app import gpio_utils

logger = logging.getLogger("activity_led")


class ActivityLed:
    """Інкапсулює GPIO-стан одного LED. init() викликається раз при
    старті watchdog-циклу; якщо пін=0 чи ініціалізація провалилась
    (немає gpiod, пін зайнятий іншим процесом тощо) - blink() тихо
    нічого не робить, не ламаючи основний цикл запису в БД."""

    def __init__(self, pin: int, blink_ms: int) -> None:
        self.pin = pin
        self.blink_sec = blink_ms / 1000.0
        self._set_value: Optional[Callable[[int], None]] = None
        self._release: Optional[Callable[[], None]] = None
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def init(self) -> bool:
        if not self.pin or self.pin <= 0:
            logger.info("ACTIVITY_LED_PIN не налаштовано (0) - LED активності вимкнено")
            return False
        try:
            self._set_value, self._release = gpio_utils.open_output_line(
                self.pin, "starlink-activity-led"
            )
            self._set_value(0)
            logger.info("LED активності увімкнено на GPIO%d (спалах %dмс)", self.pin, int(self.blink_sec * 1000))
            return True
        except Exception as e:
            logger.warning("Не вдалося ініціалізувати LED активності на GPIO%d: %s", self.pin, e)
            self._set_value = None
            self._release = None
            return False

    def blink(self) -> None:
        """Викликається з db.py при кожному commit - МАЄ бути швидкою
        й ніколи не кидати виняток (запис у БД не повинен провалитись
        через проблему з LED). Скасовує попередній таймер при
        частих послідовних записах (напр. кілька insert підряд) -
        інакше LED мигав би нервово замість рівного світіння під час
        серії записів."""
        if self._set_value is None:
            return
        with self._lock:
            try:
                if self._timer is not None:
                    self._timer.cancel()
                self._set_value(1)
                self._timer = threading.Timer(self.blink_sec, self._turn_off)
                self._timer.daemon = True
                self._timer.start()
            except Exception as e:
                logger.debug("Помилка блимання LED активності: %s", e)

    def _turn_off(self) -> None:
        try:
            if self._set_value is not None:
                self._set_value(0)
        except Exception as e:
            logger.debug("Помилка вимкнення LED активності: %s", e)

    def close(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._release is not None:
            try:
                self._release()
            except Exception as e:
                # Той самий принцип, що в display.py/shutdown_button.py:
                # не перекривати оригінальну причину завершення, лише
                # debug-слід для рідкісного edge-case.
                logger.debug("Не вдалося звільнити GPIO LED активності при завершенні: %s", e)
