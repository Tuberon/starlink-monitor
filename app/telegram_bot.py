"""
Вхідні команди Telegram-бота для Starlink Monitor: перевірка стану
оновлень і ручний reboot Starlink Mini прямо з чату.

Працює через long polling (getUpdates), без webhook - Pi зазвичай не
має публічної адреси/сертифіката, тож long polling простіший і
надійніший варіант. Запускається окремим потоком з поточного процесу
starlink-monitor.service (не окремим сервісом), щоб не плодити ще один
systemd-юніт лише заради опитування Telegram API раз на кілька секунд.

Авторизація: команди виконує лише той chat_id, що вже доданий у
налаштування сповіщень (app/telegram_notify.get_telegram_config) -
той самий список отримувачів, яким бот і так надсилає сповіщення.
Захищає /reboot від виконання випадковим користувачем, який просто
написав боту.

Команди:
  /status  - поточний стан оновлення ПЗ dish і router + активні
             попередження (той самий формат, що API /api/status,
             /api/router-status)
  /reboot  - запит підтвердження (inline-кнопки Так/Ні), і лише після
             підтвердження - виконує reboot_dish()
  /help    - список команд
"""
import logging
import threading
import time

import requests

from app import db, telegram_notify
from app.labels import ROUTER_UPDATE_STATE_LABELS, UPDATE_STATE_LABELS
from app.starlink_client import StarlinkClient

logger = logging.getLogger("telegram_bot")

API_BASE = "https://api.telegram.org/bot{token}/{method}"
REQUEST_TIMEOUT_SEND = 10
POLL_TIMEOUT_SEC = 30
REQUEST_TIMEOUT_POLL = POLL_TIMEOUT_SEC + 5  # трохи більше за timeout long polling

# Скільки секунд діє запит підтвердження /reboot, перш ніж вважати його
# застарілим (захист від випадкового підтвердження старого запиту)
CONFIRM_TTL_SEC = 120


def _api_call(method: str, token: str, http_timeout: float, **params):
    try:
        resp = requests.post(
            API_BASE.format(token=token, method=method),
            json=params,
            timeout=http_timeout,
        )
        return resp.json()
    except requests.RequestException as e:
        logger.warning("Telegram API виклик %s провалився: %s", method, e)
        return None


class TelegramBot:
    def __init__(self):
        self.client = StarlinkClient()
        self._last_update_id = 0
        self._stop_event = threading.Event()
        self._thread = None
        # Очікуючі підтвердження /reboot: chat_id -> час запиту (для TTL)
        self._pending_reboot_confirm = {}

    def start(self):
        """Запускає polling у фоновому демон-потоці. Викликається один раз
        з monitor.run_forever()."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="telegram-bot")
        self._thread.start()
        logger.info("Telegram-бот: потік опитування команд запущено")

    def stop(self):
        self._stop_event.set()

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                token, allowed_chat_ids, enabled = telegram_notify.get_telegram_config()
                if not enabled or not token:
                    # Бот вимкнений/не налаштований - не опитуємо API даремно,
                    # перевіряємо періодично, чи налаштування з'явились.
                    time.sleep(10)
                    continue
                self._poll_once(token, set(allowed_chat_ids))
            except Exception:
                logger.exception("Неочікувана помилка в циклі Telegram-бота")
                time.sleep(5)

    def _poll_once(self, token: str, allowed_chat_ids: set):
        data = _api_call(
            "getUpdates",
            token,
            REQUEST_TIMEOUT_POLL,
            offset=self._last_update_id + 1,
            timeout=POLL_TIMEOUT_SEC,
        )
        if not data or not data.get("ok"):
            time.sleep(3)
            return

        for update in data.get("result", []):
            self._last_update_id = max(self._last_update_id, update.get("update_id", 0))
            try:
                self._handle_update(token, allowed_chat_ids, update)
            except Exception:
                logger.exception("Помилка обробки Telegram update: %s", update)

    def _handle_update(self, token: str, allowed_chat_ids: set, update: dict):
        if "callback_query" in update:
            self._handle_callback(token, allowed_chat_ids, update["callback_query"])
            return

        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if not text:
            return

        if chat_id not in allowed_chat_ids:
            logger.info("Ігноровано команду від неавторизованого chat_id=%s", chat_id)
            self._send(token, chat_id, "\u26d4 Цей чат не авторизований для команд боту.")
            return

        command = text.split()[0].lower().split("@")[0]  # прибрати /cmd@botname
        if command == "/status":
            self._cmd_status(token, chat_id)
        elif command == "/reboot":
            self._cmd_reboot_request(token, chat_id)
        elif command == "/id":
            arg = text[len(command):].strip()
            self._cmd_id(token, chat_id, arg)
        elif command in ("/help", "/start"):
            self._cmd_help(token, chat_id)
        else:
            self._send(token, chat_id, "Невідома команда. /help — список команд.")

    def _handle_callback(self, token: str, allowed_chat_ids: set, callback: dict):
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        data = callback.get("data", "")
        callback_id = callback.get("id")

        if chat_id not in allowed_chat_ids:
            _api_call("answerCallbackQuery", token, REQUEST_TIMEOUT_SEND, callback_query_id=callback_id,
                      text="Не авторизовано")
            return

        if data == "reboot_confirm":
            requested_at = self._pending_reboot_confirm.pop(chat_id, None)
            _api_call("answerCallbackQuery", token, REQUEST_TIMEOUT_SEND, callback_query_id=callback_id)
            if requested_at is None or (time.time() - requested_at) > CONFIRM_TTL_SEC:
                self._send(token, chat_id, "\u231b Запит на reboot застарів. Надішліть /reboot ще раз.")
                return
            self._send(token, chat_id, "\U0001f501 Виконую reboot Starlink Mini...")
            ok, msg = self.client.reboot_dish()
            db.insert_event("dish_reboot", f"Ручний reboot через Telegram: {msg}", success=ok)
            if ok:
                self._send(token, chat_id, "\u2705 Reboot виконано успішно.")
            else:
                self._send(token, chat_id, f"\u274c Не вдалося виконати reboot: {msg}")
        elif data == "reboot_cancel":
            self._pending_reboot_confirm.pop(chat_id, None)
            _api_call("answerCallbackQuery", token, REQUEST_TIMEOUT_SEND, callback_query_id=callback_id)
            self._send(token, chat_id, "Скасовано.")

    def _cmd_status(self, token: str, chat_id: str):
        dish = self.client.get_status()
        router = self.client.get_router_info()

        lines = ["<b>Стан Starlink Mini</b>", ""]

        if dish.online:
            dish_label = UPDATE_STATE_LABELS.get(dish.update_state, dish.update_state or "н/д")
            lines.append(f"\U0001f4e1 <b>Тарілка</b>: online, ПЗ {dish.software_version or '?'}")
            lines.append(
                f"   Оновлення: {dish_label}"
                + (f" ({dish.update_progress_pct:.0f}%)" if dish.update_progress_pct else "")
            )
            if dish.active_alerts:
                lines.append(f"   \u26a0\ufe0f Попереджень: {len(dish.active_alerts)}")
        else:
            lines.append(f"\U0001f4e1 <b>Тарілка</b>: offline ({dish.error or 'немає відповіді'})")

        lines.append("")

        if router.online:
            router_label = ROUTER_UPDATE_STATE_LABELS.get(router.update_state, router.update_state or "н/д")
            lines.append(f"\U0001f4f6 <b>Роутер</b>: online, ПЗ {router.software_version or '?'}")
            lines.append(
                f"   Оновлення: {router_label}"
                + (f" ({router.update_progress_pct:.0f}%)" if router.update_progress_pct else "")
            )
            if router.active_alerts:
                lines.append(f"   \u26a0\ufe0f Попереджень: {len(router.active_alerts)}")
        else:
            lines.append(f"\U0001f4f6 <b>Роутер</b>: offline ({router.error or 'немає відповіді'})")

        self._send(token, chat_id, "\n".join(lines))

    def _cmd_reboot_request(self, token: str, chat_id: str):
        self._pending_reboot_confirm[chat_id] = time.time()
        phrase = telegram_notify._random_signature_phrase()
        text = "\u26a0\ufe0f Перезавантажити Starlink Mini зараз? Зв'язок буде втрачено на 1-2 хвилини."
        if phrase:
            text = f"{text}\n\n{phrase}"
        _api_call(
            "sendMessage",
            token,
            REQUEST_TIMEOUT_SEND,
            chat_id=chat_id,
            text=text,
            reply_markup={
                "inline_keyboard": [[
                    {"text": "\u2705 Так, перезавантажити", "callback_data": "reboot_confirm"},
                    {"text": "\u274c Скасувати", "callback_data": "reboot_cancel"},
                ]]
            },
        )

    def _cmd_help(self, token: str, chat_id: str):
        text = (
            "<b>Starlink Monitor — команди</b>\n\n"
            "/status — поточний стан оновлення ПЗ тарілки й роутера, активні попередження\n"
            "/reboot — перезавантажити Starlink Mini (з підтвердженням)\n"
            "/id — список усіх колись підключених тарілок (ID, версії ПЗ)\n"
            "/id &lt;ID або частина ID&gt; — деталі конкретної тарілки: версії ПЗ dish/router "
            "і коли востаннє встановлювались оновлення\n"
            "/help — цей список"
        )
        self._send(token, chat_id, text)

    def _cmd_id(self, token: str, chat_id: str, arg: str):
        if not arg:
            devices = db.get_all_known_devices()
            if not devices:
                self._send(token, chat_id, "Ще жодної тарілки не підключено.")
                return
            lines = [f"<b>Відомі тарілки ({len(devices)})</b>", ""]
            for d in devices:
                last_seen = self._fmt_ago(d["last_seen_ts"])
                lines.append(f"<code>{d['dish_id']}</code> — востаннє в мережі {last_seen}")
            lines.append("")
            lines.append("Деталі: /id &lt;ID або частина ID&gt;")
            self._send(token, chat_id, "\n".join(lines))
            return

        device = db.get_known_device(arg)
        if device is None:
            # Пошук за частковим збігом - зручно, щоб не вводити довгий ID повністю
            matches = [d for d in db.get_all_known_devices() if arg.lower() in d["dish_id"].lower()]
            if len(matches) == 1:
                device = matches[0]
            elif len(matches) > 1:
                ids = "\n".join(f"<code>{d['dish_id']}</code>" for d in matches)
                self._send(token, chat_id, f"Знайдено кілька збігів, уточніть ID:\n\n{ids}")
                return

        if device is None:
            self._send(token, chat_id, f"Тарілку з ID «{arg}» не знайдено серед відомих.")
            return

        lines = [f"<b>Тарілка</b> <code>{device['dish_id']}</code>", ""]

        lines.append(f"\U0001f4e1 <b>Dish</b>: {device['dish_hardware_version'] or 'н/д'}")
        lines.append(f"   ПЗ: {device['dish_software_version'] or 'н/д'}")
        if device["dish_software_updated_ts"]:
            lines.append(f"   Останнє оновлення ПЗ: {self._fmt_ago(device['dish_software_updated_ts'])}")

        lines.append("")
        lines.append(f"\U0001f4f6 <b>Router</b>: {device['router_hardware_version'] or 'н/д'}")
        lines.append(f"   ПЗ: {device['router_software_version'] or 'н/д'}")
        if device["router_software_updated_ts"]:
            lines.append(f"   Останнє оновлення ПЗ: {self._fmt_ago(device['router_software_updated_ts'])}")

        lines.append("")
        lines.append(f"Вперше підключено: {self._fmt_ago(device['first_seen_ts'])}")
        lines.append(f"Востаннє в мережі: {self._fmt_ago(device['last_seen_ts'])}")

        self._send(token, chat_id, "\n".join(lines))

    @staticmethod
    def _fmt_ago(ts: float) -> str:
        if not ts:
            return "невідомо"
        delta = time.time() - ts
        if delta < 60:
            return "щойно"
        if delta < 3600:
            return f"{int(delta // 60)} хв тому"
        if delta < 86400:
            return f"{int(delta // 3600)} год тому"
        days = int(delta // 86400)
        return f"{days} дн тому"

    def _send(self, token: str, chat_id: str, text: str):
        phrase = telegram_notify._random_signature_phrase()
        full_text = f"{text}\n\n{phrase}" if phrase else text
        _api_call("sendMessage", token, REQUEST_TIMEOUT_SEND, chat_id=chat_id, text=full_text, parse_mode="HTML")
