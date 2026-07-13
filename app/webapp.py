"""Flask веб-інтерфейс: дашборд статусу Starlink, історія, журнал подій, ручний reboot."""
import logging

from flask import Flask, jsonify, render_template, request

from app import config, db, telegram_notify
from app.starlink_client import StarlinkClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webapp")

app = Flask(__name__, template_folder="../templates", static_folder="../static")
client = StarlinkClient()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    latest = db.get_latest_metric()
    uptime_pct = db.uptime_stats_24h()
    return jsonify({"latest": latest, "uptime_24h_pct": uptime_pct})


@app.route("/api/history")
def api_history():
    limit = min(int(request.args.get("limit", 500)), 5000)
    return jsonify(db.get_recent_metrics(limit))


@app.route("/api/events")
def api_events():
    limit = min(int(request.args.get("limit", 30)), 500)
    return jsonify(db.get_recent_events(limit))


@app.route("/api/system-status")
def api_system_status():
    latest = db.get_latest_system_metric()
    return jsonify({"latest": latest})


@app.route("/api/router-status")
def api_router_status():
    return jsonify({"latest": db.get_router_status()})


@app.route("/api/reboot-dish", methods=["POST"])
def api_reboot_dish():
    ok, msg = client.reboot_dish()
    db.insert_event("dish_reboot", f"Ручний reboot через веб-інтерфейс: {msg}", success=ok)
    if ok:
        telegram_notify.send_message("🔁 Starlink Mini перезавантажено вручну через веб-інтерфейс")
    else:
        telegram_notify.send_message(f"❌ Не вдалося перезавантажити Starlink Mini вручну: {msg}")
    return jsonify({"success": ok, "message": msg})


@app.route("/api/check-updates", methods=["POST"])
def api_check_updates():
    """
    Ручна перевірка стану оновлень. ВАЖЛИВО: локальний gRPC API dish/router
    не має команди "примусово перевірити оновлення в хмарі SpaceX" — це
    підтверджено прямими викликами (software_update повертає
    "FailedPrecondition: Sideload update stream not open" на dish і
    "Unimplemented" на роутері - цей запит призначений для sideload
    завантаження файлу прошивки вручну, не для перевірки в хмарі).
    Кнопка "Перевірити оновлення" в офіційному застосунку працює через
    хмарний бекенд SpaceX, недоступний з локальної мережі.

    Натомість цей ендпоінт негайно опитує dish і router (замість очікування
    наступного фонового циклу опитування) і одразу показує актуальний
    поточний стан оновлення - це те, що реально доступно через локальний API.
    """
    dish_status = client.get_status()
    db.insert_metric(dish_status.to_dict())

    router_info = client.get_router_info()
    db.set_router_status(router_info.to_dict())

    db.insert_event(
        "manual_update_check",
        f"Ручна перевірка: dish={dish_status.update_state or 'н/д'}, "
        f"router={router_info.update_state or 'н/д'}",
        success=dish_status.online or router_info.online,
    )

    return jsonify({
        "success": True,
        "dish": {
            "online": dish_status.online,
            "update_state": dish_status.update_state,
            "update_progress_pct": dish_status.update_progress_pct,
        },
        "router": {
            "online": router_info.online,
            "update_state": router_info.update_state,
            "update_progress_pct": router_info.update_progress_pct,
        },
    })


@app.route("/api/config")
def api_config():
    """Не чутливі налаштування — для відображення на дашборді."""
    return jsonify({
        "poll_interval_sec": config.POLL_INTERVAL_SEC,
        "max_consecutive_failures": config.MAX_CONSECUTIVE_FAILURES,
        "min_reboot_interval_sec": config.MIN_REBOOT_INTERVAL_SEC,
        "auto_reboot_on_update_ready": db.get_auto_reboot_enabled(),
    })


@app.route("/api/auto-reboot", methods=["POST"])
def api_set_auto_reboot():
    """Вмикає/вимикає автоматичний reboot dish/router при готовому
    оновленні. Зберігається в БД (не в env-файлі), тож застосовується
    одразу, без перезапуску сервісу."""
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled"))
    db.set_auto_reboot_enabled(enabled)
    db.insert_event(
        "auto_reboot_toggled",
        f"Автоматичний reboot при готовому оновленні: {'увімкнено' if enabled else 'вимкнено'}",
        success=True,
    )
    return jsonify({"success": True, "enabled": enabled})


@app.route("/api/telegram-config")
def api_get_telegram_config():
    """Токен повертається лише замаскованим (щоб не показувати secret
    у відкритому вигляді в мережі/консолі браузера), крапка входу для
    перевірки, чи він взагалі заданий."""
    token, chat_ids, enabled = telegram_notify.get_telegram_config()
    masked_token = ""
    if token:
        masked_token = f"{token[:6]}...{token[-4:]}" if len(token) > 10 else "***"
    return jsonify({
        "token_set": bool(token),
        "token_masked": masked_token,
        "chat_ids": chat_ids,
        "enabled": enabled,
    })


@app.route("/api/telegram-config", methods=["POST"])
def api_set_telegram_config():
    payload = request.get_json(silent=True) or {}
    token = payload.get("token")
    chat_ids_raw = payload.get("chat_ids")
    enabled = payload.get("enabled")

    chat_ids = None
    if chat_ids_raw is not None:
        if isinstance(chat_ids_raw, str):
            chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
        elif isinstance(chat_ids_raw, list):
            chat_ids = chat_ids_raw

    telegram_notify.set_telegram_config(
        token=token if token else None,
        chat_ids=chat_ids,
        enabled=enabled if enabled is not None else None,
    )
    db.insert_event("telegram_config_updated", "Налаштування Telegram-сповіщень оновлено", success=True)
    return jsonify({"success": True})


@app.route("/api/telegram-test", methods=["POST"])
def api_telegram_test():
    ok, msg = telegram_notify.test_connection()
    if ok:
        send_ok, send_msg = telegram_notify.send_message(
            "✅ Тестове повідомлення від Starlink Monitor. Сповіщення налаштовано правильно."
        )
        return jsonify({"success": ok and send_ok, "message": f"{msg}; {send_msg}"})
    return jsonify({"success": False, "message": msg})


def main():
    db.init_db()
    app.run(host=config.WEBUI_HOST, port=config.WEBUI_PORT)


if __name__ == "__main__":
    main()
