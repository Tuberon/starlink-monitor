"""Flask веб-інтерфейс: дашборд статусу Starlink, історія, журнал подій, ручний reboot."""
import logging
import subprocess
import time

from flask import Flask, jsonify, render_template, request

from app import config, config_editor, db, telegram_notify
from app.starlink_client import StarlinkClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webapp")

app = Flask(__name__, template_folder="../templates", static_folder="../static")
# Кешування статики (dashboard.js, logo.png) на стороні браузера - зменшує
# кількість запитів при кожному відкритті/перезавантаженні дашборду,
# помітно на слабкому WiFi-каналі Pi Zero 2 W. API-ендпоінти (динамічні
# дані) цього не стосуються - кешується лише /static/*.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600
client = StarlinkClient()


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


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
        "shutdown_button_enabled": config.SHUTDOWN_BUTTON_GPIO_PIN > 0,
        "shutdown_button_pin": config.SHUTDOWN_BUTTON_GPIO_PIN,
        "shutdown_button_hold_sec": config.SHUTDOWN_BUTTON_HOLD_SEC,
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


@app.route("/api/signature-phrases")
def api_get_signature_phrases():
    return jsonify({
        "text": telegram_notify.get_signature_phrases_text(),
        "enabled": telegram_notify.get_signature_phrases_enabled(),
    })


@app.route("/api/signature-phrases", methods=["POST"])
def api_set_signature_phrases():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    ok, msg = telegram_notify.set_signature_phrases_text(text)
    db.insert_event("signature_phrases_updated", f"Фрази підпису оновлено: {msg}", success=ok)
    return jsonify({"success": ok, "message": msg})


@app.route("/api/signature-phrases-enabled", methods=["POST"])
def api_set_signature_phrases_enabled():
    payload = request.get_json(silent=True) or {}
    enabled = bool(payload.get("enabled"))
    telegram_notify.set_signature_phrases_enabled(enabled)
    db.insert_event(
        "signature_phrases_toggled",
        f"Додавання фраз підпису: {'увімкнено' if enabled else 'вимкнено'}",
        success=True,
    )
    return jsonify({"success": True, "enabled": enabled})


BACKUP_FORMAT_VERSION = 1


@app.route("/api/settings-backup")
def api_settings_backup():
    """Повертає всі налаштування (Telegram config, фрази підпису,
    auto-reboot) одним JSON-файлом для завантаження. Bot token
    включається у відкритому вигляді - файл backup потрібно берегти
    як secret (не публікувати, не комітити в git)."""
    token, chat_ids, enabled = telegram_notify.get_telegram_config()
    backup = {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": time.time(),
        "telegram_bot_token": token,
        "telegram_chat_ids": chat_ids,
        "telegram_enabled": enabled,
        "auto_reboot_enabled": db.get_auto_reboot_enabled(),
        "signature_phrases": telegram_notify.get_signature_phrases_text(),
        "signature_phrases_enabled": telegram_notify.get_signature_phrases_enabled(),
    }
    return jsonify(backup)


@app.route("/api/settings-restore", methods=["POST"])
def api_settings_restore():
    """Відновлює налаштування з JSON, отриманого через /api/settings-backup.
    Приймає лише відомі поля - невідомі/сторонні ключі ігноруються."""
    payload = request.get_json(silent=True) or {}
    if "format_version" not in payload:
        return jsonify({"success": False, "message": "Некоректний формат файлу backup"})

    restored = []
    try:
        if "telegram_bot_token" in payload or "telegram_chat_ids" in payload or "telegram_enabled" in payload:
            telegram_notify.set_telegram_config(
                token=payload.get("telegram_bot_token"),
                chat_ids=payload.get("telegram_chat_ids"),
                enabled=payload.get("telegram_enabled"),
            )
            restored.append("telegram config")

        if "auto_reboot_enabled" in payload:
            db.set_auto_reboot_enabled(bool(payload["auto_reboot_enabled"]))
            restored.append("auto-reboot")

        if "signature_phrases" in payload:
            ok, msg = telegram_notify.set_signature_phrases_text(payload["signature_phrases"])
            if ok:
                restored.append("фрази підпису")

        if "signature_phrases_enabled" in payload:
            telegram_notify.set_signature_phrases_enabled(bool(payload["signature_phrases_enabled"]))
            restored.append("перемикач фраз")

        db.insert_event("settings_restored", f"Відновлено з backup: {', '.join(restored) or 'нічого'}", success=True)
        return jsonify({"success": True, "message": f"Відновлено: {', '.join(restored) or 'нічого'}"})
    except Exception as e:
        db.insert_event("settings_restored", f"Помилка відновлення backup: {e}", success=False)
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/env-config")
def api_get_env_config():
    return jsonify({"params": config_editor.read_current_values()})


@app.route("/api/env-config", methods=["POST"])
def api_set_env_config():
    payload = request.get_json(silent=True) or {}
    values = payload.get("values", {})
    ok, msg = config_editor.save_values(values)
    db.insert_event("env_config_updated", f"Параметри config.py оновлено: {msg}", success=ok)
    return jsonify({"success": ok, "message": msg})


@app.route("/api/env-config-restart", methods=["POST"])
def api_restart_after_env_change():
    """Перезапускає starlink-monitor.service і starlink-webui.service, щоб
    застосувати щойно змінені env-параметри (читаються один раз при старті).
    webui.service перезапускає й самого себе - відповідь клієнту може не
    дійти, це очікувано."""
    ok1, msg1 = _run_system_command(["sudo", "systemctl", "restart", "starlink-monitor.service"])
    db.insert_event("service_restart", f"starlink-monitor.service: {msg1}", success=ok1)
    ok2, msg2 = _run_system_command(["sudo", "systemctl", "restart", "starlink-webui.service"])
    return jsonify({"success": ok1 and ok2, "message": f"monitor: {msg1}; webui: {msg2}"})


def _run_system_command(cmd: list) -> tuple:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "unknown error").strip()
            return False, err[:500]
        return True, "виконано"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as e:
        return False, str(e)


@app.route("/api/system-reboot", methods=["POST"])
def api_system_reboot():
    ok, msg = _run_system_command(["sudo", "systemctl", "reboot"])
    db.insert_event("pi_reboot", f"Ручне перезавантаження Raspberry Pi через веб-інтерфейс: {msg}", success=ok)
    if ok:
        telegram_notify.send_message("🔁 Raspberry Pi перезавантажується вручну через веб-інтерфейс")
    else:
        telegram_notify.send_message(f"❌ Не вдалося перезавантажити Raspberry Pi: {msg}")
    return jsonify({"success": ok, "message": msg})


@app.route("/api/system-shutdown", methods=["POST"])
def api_system_shutdown():
    ok, msg = _run_system_command(["sudo", "systemctl", "poweroff"])
    db.insert_event("pi_shutdown", f"Ручне виключення Raspberry Pi через веб-інтерфейс: {msg}", success=ok)
    if ok:
        telegram_notify.send_message("⏻ Raspberry Pi вимикається вручну через веб-інтерфейс")
    else:
        telegram_notify.send_message(f"❌ Не вдалося вимкнути Raspberry Pi: {msg}")
    return jsonify({"success": ok, "message": msg})


def main():
    db.init_db()
    app.run(host=config.WEBUI_HOST, port=config.WEBUI_PORT)


if __name__ == "__main__":
    main()
