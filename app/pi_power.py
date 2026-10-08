"""Спільна логіка reboot/poweroff Pi для трьох джерел (веб-дашборд, фізична кнопка, Telegram), щоб не
дублювати її в webapp.py, shutdown_button.py, telegram_bot.py. Повідомлення на TFT-дисплеї — через
БД-сигнал (db.set_setting), не прямим викликом: display.py — окремий процес, що ексклюзивно тримає SPI.
Він опитує сигнал у швидкому (типово 100 мс) циклі кнопки, тож бачить його майже одразу. Затримка перед
systemctl (типово 2 с) дає йому час намалювати повідомлення ДО того, як SIGTERM вб'є процес display.py.
"""
import logging
import subprocess
import time
from typing import Any, Callable, Optional

from app import config, db, i18n

logger = logging.getLogger("pi_power")

# Ключ у settings-таблиці - display.py опитує це значення. Значення -
# "reboot"/"poweroff", видаляється після реального виконання команди
# (не залишається "завислим" на наступний запуск, коли Pi піднімається
# знову і db still має старий запис).
PENDING_ACTION_SETTING_KEY = "pi_power_action_pending"


def run_system_command(cmd: list[str], timeout: int = 10) -> tuple[bool, str]:
    """Спільна для pi_power.py (reboot/poweroff Pi) і webapp.py
    (restart сервісів) - раніше була продубльована в обох файлах
    майже ідентично, з дрібними відмінностями (timeout, обрізання
    помилки, TimeoutExpired-обробка); об'єднано в одну, найповнішу
    версію. Публічна (без `_`) - реально використовується з іншого
    модуля, не лише внутрішньо."""
    try:
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "unknown error").strip()
            return False, err[:500]
        return True, i18n.t("api_done")
    except subprocess.TimeoutExpired:
        return False, "timeout"
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
        # Імпорт тут, а не на рівні модуля: pi_power імпортують процеси
        # display/shutdown_button, яким Telegram потрібен лише в момент
        # вимкнення Pi - модульний імпорт тягнув у них увесь HTTP-стек
        # (requests/urllib3, ~20 МБ RSS у вимірі) назавжди.
        from app import telegram_notify
        notify_fn = telegram_notify.send_message
    try:
        db.set_setting(PENDING_ACTION_SETTING_KEY, action)
    except Exception as e:
        # Не критично - навіть якщо сигнал для екрана не записався,
        # сам reboot/poweroff МАЄ відбутись, це основна дія.
        logger.warning("Не вдалося записати pending-сигнал для дисплея: %s", e)

    if config.DISPLAY_ENABLED:
        time.sleep(config.DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC)

    # Pending-сигнал очищується ПЕРЕД командою, не після: ця функція виконується в
    # starlink-webui.service, який systemctl reboot сам і зупиняє — SIGTERM міг прийти раніше за
    # очищення, сигнал лишався в БД, і після перезавантаження display.py малював застаріле повідомлення.
    # Сигнал уже виконав свою мету (display.py встиг показати повідомлення під час затримки вище);
    # провал команди теж лишає його очищеним — екран не має "застрягати".
    try:
        db.set_setting(PENDING_ACTION_SETTING_KEY, "")
    except Exception:
        pass

    ok, msg = run_system_command(cmd, timeout=15)
    db.insert_event(event_kind, f"{event_label}: {msg}", success=ok)
    if ok:
        notify_fn(success_text)
    else:
        notify_fn(i18n.t("tg_pi_action_failed", verb=fail_verb, msg=msg))
    return ok, msg

def shutdown_from_button(pin: int) -> None:
    """Виключення Pi після довгого утримання фізичної кнопки. Спільне для
    процесів shutdown_button і display (раніше жило в shutdown_button.py,
    який display імпортував як бібліотеку: точка входу сервісу не має бути
    бібліотекою - див. tests/test_architecture.py)."""
    logger.warning("Кнопка виключення утримана %.1fс на GPIO%d - виконую poweroff", config.SHUTDOWN_BUTTON_HOLD_SEC, pin)
    try:
        db.init_db()
    except Exception as e:
        logger.warning("Не вдалося ініціалізувати БД: %s", e)

    execute_pi_power_action(
        ["sudo", "systemctl", "poweroff"], "poweroff",
        "pi_shutdown", f"Виключення через фізичну кнопку (GPIO{pin})",
        i18n.t("tg_pi_shutdown_button", pin=pin), i18n.t("verb_shutdown"),
    )
