"""
Вхідні команди Telegram-бота: long polling (без webhook), працює в
потоці starlink-monitor.service. Авторизація за chat_id зі списку
отримувачів сповіщень (app/telegram_notify).

Команди: /status, /checkupdates, /reboot (з підтвердженням),
/id [dish_id], /help.
"""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import requests

from app import config, db, i18n, monitor, telegram_notify
from app import labels
from app.starlink_client import StarlinkClient

logger = logging.getLogger("telegram_bot")

API_BASE = "https://api.telegram.org/bot{token}/{method}"

# Скільки секунд діє запит підтвердження /reboot, перш ніж вважати його
# застарілим (захист від випадкового підтвердження старого запиту)
def _api_call(method: str, token: str, http_timeout: float, **params: Any) -> Optional[dict[str, Any]]:
    """Викликає Telegram Bot API, повертає розпарсений JSON або None
    при мережевій помилці. ВАЖЛИВО: Telegram API повертає ВАЛІДНИЙ
    JSON навіть для відхилених запитів (напр. `{"ok": false,
    "description": "message is too long"}` при перевищенні ліміту
    4096 символів, чи невалідному HTML-форматуванні) - без явної
    перевірки `ok`-поля такі помилки мовчки ігнорувались, виглядаючи
    як "команда взагалі не відповідає" (реальний баг, знайдений на
    запиті користувача: /id без аргументів міг перевищити ліміт при
    великій кількості відомих тарілок)."""
    try:
        resp = telegram_notify._request_with_eth0_fallback(
            "post",
            API_BASE.format(token=token, method=method),
            json=params,
            timeout=http_timeout,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.warning(
                "Telegram API %s відхилив запит: %s (chat_id=%s)",
                method, data.get("description", "невідома причина"), params.get("chat_id", "?"),
            )
        return data
    except requests.RequestException as e:
        logger.warning("Telegram API виклик %s провалився: %s", method, e)
        return None


def _shown_router_state(state: str) -> str:
    """Прихований стан (monitor.HIDDEN_ROUTER_UPDATE_STATES) показується як
    "немає оновлень" - так само, як на дашборді й TFT-дисплеї."""
    return "NOT_RUN" if state in monitor.HIDDEN_ROUTER_UPDATE_STATES else state


class TelegramBot:
    def __init__(self) -> None:
        self.client = StarlinkClient()
        self._last_update_id = 0
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Очікуючі підтвердження /reboot: chat_id -> час запиту (для TTL)
        self._pending_reboot_confirm: dict[str, float] = {}
        # Обробка кожного update (зокрема /status, що робить блокуючі
        # gRPC-виклики до dish/router з таймаутом до ~15с) виконується
        # в окремому потоці з цього пулу - інакше повільна/недоступна
        # мережа Starlink затримує весь polling-цикл getUpdates, і нові
        # команди чекають, поки не завершиться поточна.
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="tg-cmd")

    def start(self) -> None:
        """Запускає polling у фоновому демон-потоці. Викликається один раз
        з monitor.run_forever()."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="telegram-bot")
        self._thread.start()
        logger.info("Telegram-бот: потік опитування команд запущено")

    def stop(self) -> None:
        self._stop_event.set()
        self._executor.shutdown(wait=False)

    def _run_loop(self) -> None:
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

    def _poll_once(self, token: str, allowed_chat_ids: set[str]) -> None:
        data = _api_call(
            "getUpdates",
            token,
            (config.TELEGRAM_POLL_TIMEOUT_SEC + 5),
            offset=self._last_update_id + 1,
            timeout=config.TELEGRAM_POLL_TIMEOUT_SEC,
        )
        if not data or not data.get("ok"):
            time.sleep(3)
            return

        for update in data.get("result", []):
            self._last_update_id = max(self._last_update_id, update.get("update_id", 0))

        # Групуємо за chat_id: updates того самого чату обробляються
        # послідовно в одному потоці (гарантує порядок, напр. команда
        # /reboot і подальший клік підтвердження від того самого
        # користувача) - без цього ThreadPoolExecutor міг би виконати їх
        # у довільному порядку завершення, і клік "підтвердити" міг би
        # обробитись РАНІШЕ за встановлення pending-стану командою /reboot.
        # Різні чати й далі обробляються паралельно (одна повільна команда
        # від одного користувача не блокує інших).
        groups: dict[str, list[dict[str, Any]]] = {}
        for update in data.get("result", []):
            chat_id = self._extract_chat_id(update)
            groups.setdefault(chat_id, []).append(update)

        for chat_id, updates in groups.items():
            self._executor.submit(self._handle_updates_sequential, token, allowed_chat_ids, updates)

    @staticmethod
    def _extract_chat_id(update: dict) -> str:
        if "callback_query" in update:
            return str(update["callback_query"].get("message", {}).get("chat", {}).get("id", ""))
        message = update.get("message") or update.get("edited_message") or {}
        return str(message.get("chat", {}).get("id", ""))

    def _handle_updates_sequential(self, token: str, allowed_chat_ids: set[str], updates: list[dict[str, Any]]) -> None:
        for update in updates:
            try:
                self._handle_update(token, allowed_chat_ids, update)
            except Exception:
                logger.exception("Помилка обробки Telegram update: %s", update)

    def _handle_update(self, token: str, allowed_chat_ids: set[str], update: dict[str, Any]) -> None:
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
            self._send(token, chat_id, i18n.t("tg_unauthorized_chat"))
            return

        command = text.split()[0].lower().split("@")[0]  # прибрати /cmd@botname
        if command == "/status":
            self._cmd_status(token, chat_id)
        elif command == "/checkupdates":
            self._cmd_check_updates(token, chat_id)
        elif command == "/reboot":
            self._cmd_reboot_request(token, chat_id)
        elif command == "/id":
            arg = text[len(command):].strip()
            self._cmd_id(token, chat_id, arg)
        elif command in ("/help", "/start"):
            self._cmd_help(token, chat_id)
        else:
            self._send(token, chat_id, i18n.t("tg_unknown_command"))

    def _handle_callback(self, token: str, allowed_chat_ids: set[str], callback: dict[str, Any]) -> None:
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        data = callback.get("data", "")
        callback_id = callback.get("id")

        if chat_id not in allowed_chat_ids:
            _api_call("answerCallbackQuery", token, config.TELEGRAM_SEND_TIMEOUT_SEC, callback_query_id=callback_id,
                      text=i18n.t("tg_not_authorized"))
            return

        if data == "reboot_confirm":
            requested_at = self._pending_reboot_confirm.pop(chat_id, None)
            _api_call("answerCallbackQuery", token, config.TELEGRAM_SEND_TIMEOUT_SEC, callback_query_id=callback_id)
            if requested_at is None or (time.time() - requested_at) > config.TELEGRAM_CONFIRM_TTL_SEC:
                self._send(token, chat_id, i18n.t("tg_reboot_expired"))
                return
            self._send(token, chat_id, i18n.t("tg_executing_reboot"))
            ok, msg = self.client.reboot_dish()
            db.insert_event("dish_reboot", f"Ручний reboot через Telegram: {msg}", success=ok)
            if ok:
                self._send(token, chat_id, i18n.t("tg_reboot_success"))
            else:
                self._send(token, chat_id, i18n.t("tg_reboot_failed", msg=msg))
        elif data == "reboot_cancel":
            self._pending_reboot_confirm.pop(chat_id, None)
            _api_call("answerCallbackQuery", token, config.TELEGRAM_SEND_TIMEOUT_SEC, callback_query_id=callback_id)
            self._send(token, chat_id, i18n.t("tg_cancelled"))

    def _cmd_status(self, token: str, chat_id: str) -> None:
        dish = self.client.get_status()
        router = self.client.get_router_info()

        lines = [i18n.t("tg_status_title"), ""]

        if dish.online:
            dish_label = labels.update_state_label(dish.update_state) if dish.update_state else i18n.t('not_available_short')
            lines.append(i18n.t("tg_dish_online_line", sw=dish.software_version or "?"))
            lines.append(
                i18n.t("tg_update_line", label=dish_label)
                + (f" ({dish.update_progress_pct:.0f}%)" if dish.update_progress_pct else "")
            )
            if dish.active_alerts:
                lines.append(i18n.t("tg_alerts_count_line", n=len(dish.active_alerts)))
        else:
            lines.append(i18n.t("tg_dish_offline_line", error=dish.error or i18n.t("no_response")))

        lines.append("")

        if router.online:
            router_label = labels.router_update_state_label(_shown_router_state(router.update_state)) if router.update_state else i18n.t('not_available_short')
            lines.append(i18n.t("tg_router_online_line", sw=router.software_version or "?"))
            lines.append(
                i18n.t("tg_update_line", label=router_label)
                + (f" ({router.update_progress_pct:.0f}%)" if router.update_progress_pct else "")
            )
            if router.active_alerts:
                lines.append(i18n.t("tg_alerts_count_line", n=len(router.active_alerts)))
        else:
            lines.append(i18n.t("tg_router_offline_line", error=router.error or i18n.t("no_response")))

        self._send(token, chat_id, "\n".join(lines))

    def _cmd_check_updates(self, token: str, chat_id: str) -> None:
        """Ручна перевірка стану оновлень - той самий monitor.check_
        updates_now(), що веб-кнопка "Перевірити оновлення" (webapp.py
        /api/check-updates) - не дублює логіку, лише інше форматування
        відповіді (HTML-текст у Telegram замість JSON)."""
        def notify(text: str) -> None:
            telegram_notify.send_message(text)

        dish, router = monitor.check_updates_now(self.client, notify)

        lines = [i18n.t("tg_check_updates_title"), ""]

        if dish.online:
            dish_label = labels.update_state_label(dish.update_state) if dish.update_state else i18n.t('not_available_short')
            lines.append(i18n.t("tg_dish_line_short", sw=dish.software_version or "?"))
            lines.append(
                i18n.t("tg_update_line", label=dish_label)
                + (f" ({dish.update_progress_pct:.0f}%)" if dish.update_progress_pct else "")
            )
        else:
            lines.append(i18n.t("tg_dish_offline_line", error=dish.error or i18n.t("no_response")))

        lines.append("")

        if router.online:
            router_label = labels.router_update_state_label(_shown_router_state(router.update_state)) if router.update_state else i18n.t('not_available_short')
            lines.append(i18n.t("tg_router_line_short", sw=router.software_version or "?"))
            lines.append(
                i18n.t("tg_update_line", label=router_label)
                + (f" ({router.update_progress_pct:.0f}%)" if router.update_progress_pct else "")
            )
        else:
            lines.append(i18n.t("tg_router_offline_line", error=router.error or i18n.t("no_response")))

        self._send(token, chat_id, "\n".join(lines))

    def _cmd_reboot_request(self, token: str, chat_id: str) -> None:
        # Прибираємо застарілі pending-записи (TTL уже минув) з УСІХ
        # chat_id, не лише поточного - без цього словник рахував би
        # ЛИШЕ через _handle_callback() (реальне натискання inline-
        # кнопки); якщо користувач просто ігнорує повідомлення (не
        # тисне ні "підтвердити", ні "скасувати"), запис лишався б
        # у пам'яті процесу назавжди - повільний, але реальний leak
        # у довготривалому (місяці без рестарту) watchdog-процесі.
        now = time.time()
        expired = [cid for cid, ts in self._pending_reboot_confirm.items()
                   if now - ts > config.TELEGRAM_CONFIRM_TTL_SEC]
        for cid in expired:
            del self._pending_reboot_confirm[cid]

        self._pending_reboot_confirm[chat_id] = time.time()
        text = i18n.t("tg_reboot_confirm_prompt")
        _api_call(
            "sendMessage",
            token,
            config.TELEGRAM_SEND_TIMEOUT_SEC,
            chat_id=chat_id,
            text=text,
            reply_markup={
                "inline_keyboard": [[
                    {"text": i18n.t("tg_yes_reboot"), "callback_data": "reboot_confirm"},
                    {"text": i18n.t("tg_cancel_btn"), "callback_data": "reboot_cancel"},
                ]]
            },
        )

    def _cmd_help(self, token: str, chat_id: str) -> None:
        self._send(token, chat_id, i18n.t("tg_help_text"))

    def _cmd_id(self, token: str, chat_id: str, arg: str) -> None:
        if not arg:
            self._reply_id_list(token, chat_id)
        else:
            self._reply_id_detail(token, chat_id, arg)

    def _reply_id_list(self, token: str, chat_id: str) -> None:
        """Список УСІХ відомих тарілок - викликається при /id без аргументу."""
        devices = db.get_all_known_devices()
        if not devices:
            self._send(token, chat_id, i18n.t("tg_no_dishes_yet"))
            return
        # Telegram обмежує повідомлення 4096 символами - без цього
        # захисту довгий список (десятки відомих тарілок за час
        # роботи) міг би бути ВІДХИЛЕНИЙ Telegram API цілком, і
        # відповідь мовчки не приходила б (реальний баг, знайдений
        # на запиті користувача - виправлено разом із явною
        # перевіркою "ok"-поля в _api_call() вище). get_all_known_
        # devices() уже сортує за last_seen_ts DESC - найновіші
        # (найактуальніші) показуються першими.
        shown = devices[:config.TELEGRAM_ID_LIST_MAX_ITEMS]
        lines = [i18n.t("tg_known_dishes_title", n=len(devices)), ""]
        for d in shown:
            last_seen = self._fmt_ago(d["last_seen_ts"])
            lines.append(i18n.t("tg_last_seen_line", id=d['dish_id'], ago=last_seen))
        if len(devices) > len(shown):
            lines.append("")
            lines.append(i18n.t("tg_and_more_hint", n=len(devices) - len(shown)))
        lines.append("")
        lines.append(i18n.t("tg_details_hint"))
        self._send(token, chat_id, "\n".join(lines))

    def _reply_id_detail(self, token: str, chat_id: str, arg: str) -> None:
        """Деталі КОНКРЕТНОЇ тарілки за точним ID чи частковим збігом -
        викликається при /id <arg>."""
        device = db.get_known_device(arg)
        if device is None:
            # Пошук за частковим збігом - зручно, щоб не вводити довгий ID повністю
            matches = [d for d in db.get_all_known_devices() if arg.lower() in d["dish_id"].lower()]
            if len(matches) == 1:
                device = matches[0]
            elif len(matches) > 1:
                ids = "\n".join(f"<code>{d['dish_id']}</code>" for d in matches)
                self._send(token, chat_id, i18n.t("tg_multiple_matches", ids=ids))
                return

        if device is None:
            self._send(token, chat_id, i18n.t("tg_dish_not_found", arg=arg))
            return

        na = i18n.t("not_available_short")
        lines = [i18n.t("tg_dish_title", id=device['dish_id']), ""]

        lines.append(i18n.t("tg_dish_hw_line", hw=device['dish_hardware_version'] or na))
        lines.append(i18n.t("tg_sw_line", sw=device['dish_software_version'] or na))
        if device["dish_software_updated_ts"]:
            lines.append(i18n.t("tg_last_fw_update_line", ago=self._fmt_ago(device['dish_software_updated_ts'])))

        lines.append("")
        lines.append(i18n.t("tg_router_hw_line", hw=device['router_hardware_version'] or na))
        lines.append(i18n.t("tg_sw_line", sw=device['router_software_version'] or na))
        if device["router_software_updated_ts"]:
            lines.append(i18n.t("tg_last_fw_update_line", ago=self._fmt_ago(device['router_software_updated_ts'])))

        lines.append("")
        lines.append(i18n.t("tg_first_seen_line", ago=self._fmt_ago(device['first_seen_ts'])))
        lines.append(i18n.t("tg_last_seen_full_line", ago=self._fmt_ago(device['last_seen_ts'])))

        self._send(token, chat_id, "\n".join(lines))

    @staticmethod
    def _fmt_ago(ts: float) -> str:
        if not ts:
            return i18n.t("ago_unknown")
        delta = time.time() - ts
        if delta < 60:
            return i18n.t("ago_just_now")
        if delta < 3600:
            return f"{int(delta // 60)} {i18n.t('ago_min')}"
        if delta < 86400:
            return f"{int(delta // 3600)} {i18n.t('ago_hour')}"
        days = int(delta // 86400)
        return f"{days} {i18n.t('ago_day')}"

    def _send(self, token: str, chat_id: str, text: str) -> None:
        _api_call("sendMessage", token, config.TELEGRAM_SEND_TIMEOUT_SEC, chat_id=chat_id, text=text, parse_mode="HTML")
