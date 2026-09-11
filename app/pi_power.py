"""
Спільна логіка reboot/poweroff Raspberry Pi - викликається з трьох
різних джерел (веб-дашборд, фізична кнопка, Telegram-команда), тому
винесена сюди, а не дублюється в кожному з app/webapp.py,
app/shutdown_button.py, app/telegram_bot.py.

Показ повідомлення на TFT-дисплеї (якщо увімкнений) - через БД-сигнал
(db.set_setting), не прямий виклик display.py: це ОКРЕМИЙ процес,
який ексклюзивно тримає SPI-запит, тому не можна намалювати щось на
екрані напряму з іншого процесу. display.py опитує цей setting у
своєму швидкому (типово 100мс) циклі кнопки, не чекаючи звичайного
5-секундного циклу оновлення статусу - тому виявляє сигнал майже
одразу. Затримка ПЕРЕД реальним systemctl-викликом (типово 2с) дає
достатньо часу, щоб display.py встиг це побачити й намалювати
повідомлення ДО того, як SIGTERM від systemctl reboot/poweroff
вб'є сам процес display.py.
"""
import logging
import subprocess
import time
from typing import Any, Callable, Optional

from app import config, db, telegram_notify

logger = logging.getLogger("pi_power")

# Ключ у settings-таблиці - display.py опитує це значення. Значення -
# "reboot"/"poweroff", видаляється після реального виконання команди
# (не залишається "завислим" на наступний запуск, коли Pi піднімається
# знову і db still має старий запис).
PENDING_ACTION_SETTING_KEY = "pi_power_action_pending"


def _run_system_command(cmd: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            return True, "виконано"
        return False, result.stderr.strip() or f"exit code {result.returncode}"
    except Exception as e:
        return False, str(e)


def execute_pi_power_action(
    cmd: list[str],
    action: str,
    event_kind: str,
    event_label: str,
    success_text: str,
    fail_verb: str,
    notify_fn: Optional[Callable[[str], Any]] = None,
) -> tuple[bool, str]:
    """action: "reboot" чи "poweroff" - використовується як значення
    pending-сигналу для display.py. cmd - реальна команда (напр.
    ["sudo", "systemctl", "reboot"]). notify_fn - за замовчуванням
    Telegram (lazy-визначається всередині, не як default-параметр -
    early-bound default обчислився б ОДИН раз при імпорті модуля,
    роблячи patch("app.telegram_notify.send_message") у тестах
    неефективним), але приймає інший callback (напр. з shutdown_
    button.py, де немає різниці, окрім джерела виклику)."""
    if notify_fn is None:
        notify_fn = telegram_notify.send_message
    try:
        db.set_setting(PENDING_ACTION_SETTING_KEY, action)
    except Exception as e:
        # Не критично - навіть якщо сигнал для екрана не записався,
        # сам reboot/poweroff МАЄ відбутись, це основна дія.
        logger.warning("Не вдалося записати pending-сигнал для дисплея: %s", e)

    if config.DISPLAY_ENABLED:
        time.sleep(config.DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC)

    ok, msg = _run_system_command(cmd)
    db.insert_event(event_kind, f"{event_label}: {msg}", success=ok)
    # Очищаємо pending-сигнал ЗАВЖДИ (успіх чи провал) - systemctl
    # reboot/poweroff НЕ миттєвий: subprocess.run() повертається одразу
    # після ІНІЦІЮВАННЯ команди (systemd починає graceful shutdown
    # процесів послідовно), лишаючи реальні кілька секунд ДО того, як
    # ця система вимкнеться. Без очищення тут сигнал лишався б у БД
    # назавжди після успіху (реальний баг, знайдений користувачем):
    # після реального перезавантаження display.py стартує заново,
    # бачить цей самий застарілий сигнал, малює повідомлення повторно
    # і завершується (return) - а Restart=on-failure НЕ перезапускає
    # процес після чистого завершення, тому сервіс лишається inactive
    # назавжди. Наступний reboot/poweroff тоді взагалі нічого не малює
    # (сервіс уже мертвий), а екран фізично зберігає останній
    # намальований кадр у власному framebuffer TFT-дисплея незалежно
    # від того, чи процес, що його малював, ще працює - тому здається,
    # що повідомлення "висить" безкінечно.
    try:
        db.set_setting(PENDING_ACTION_SETTING_KEY, "")
    except Exception:
        pass
    if ok:
        notify_fn(success_text)
    else:
        notify_fn(f"❌ Не вдалося {fail_verb} Raspberry Pi: {msg}")
    return ok, msg
