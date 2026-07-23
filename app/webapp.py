"""Flask веб-інтерфейс: дашборд статусу Starlink, історія, журнал подій, ручний reboot."""
import logging
import os
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

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


def _static_v(filename: str) -> str:
    """URL статичного файлу з query-параметром версії (mtime файлу) -
    cache-busting: щойно файл змінюється (нова версія коду встановлена
    через update.sh), URL міняється разом з ним, і браузер завантажує
    свіжу версію негайно, ігноруючи старий закешований файл (замість
    очікування спливання SEND_FILE_MAX_AGE_DEFAULT=3600с чи покладання
    на ручний hard refresh користувача)."""
    path = os.path.join(_STATIC_DIR, filename)
    try:
        v = int(os.path.getmtime(path))
    except OSError:
        v = 0
    return f"/static/{filename}?v={v}"


app.jinja_env.globals["static_v"] = _static_v


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/settings")
def settings_page():
    return render_template("settings.html")


@app.route("/stats")
def stats_page():
    return render_template("stats.html")


@app.route("/healthz")
def healthz():
    """Легка перевірка стану для зовнішнього моніторингу (UptimeRobot тощо):
    (1) БД доступна для читання/запису, (2) watchdog реально опитує dish
    (останній запис метрики не старіший за 3 цикли опитування - якщо
    starlink-monitor.service завис чи впав, нові метрики перестають
    з'являтись, хоча сам webui.service може лишатись живим і відповідати
    на цей же запит). Не пише нічого в журнал подій - не засмічує його
    при частому зовнішньому опитуванні (напр. раз на хвилину)."""
    checks = {}
    ok = True

    try:
        with db.get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        checks["db"] = "ok"
    except Exception as e:
        checks["db"] = f"error: {e}"
        ok = False

    try:
        latest = db.get_latest_metric()
        if latest is None:
            checks["watchdog"] = "no data yet"
        else:
            age_sec = time.time() - latest["ts"]
            max_age_sec = config.POLL_INTERVAL_SEC * 3
            checks["watchdog"] = f"ok ({age_sec:.0f}s since last poll)"
            if age_sec > max_age_sec:
                checks["watchdog"] = f"stale ({age_sec:.0f}s since last poll, expected <{max_age_sec}s)"
                ok = False
    except Exception as e:
        checks["watchdog"] = f"error: {e}"
        ok = False

    status_code = 200 if ok else 503
    return jsonify({"status": "ok" if ok else "degraded", "checks": checks}), status_code


@app.route("/api/status")
def api_status():
    latest = db.get_latest_metric()
    uptime_pct = db.uptime_stats_24h()
    return jsonify({"latest": latest, "uptime_24h_pct": uptime_pct})


@app.route("/api/history")
def api_history():
    limit = min(int(request.args.get("limit", 500)), 5000)
    return jsonify(db.get_recent_metrics(limit))


@app.route("/api/speedtest-history")
def api_speedtest_history():
    limit = min(int(request.args.get("limit", 50)), 500)
    return jsonify({
        "results": db.get_recent_speedtest_results(limit),
        "latest": db.get_latest_speedtest_result(),
        "enabled": config.SPEEDTEST_ENABLED,
    })


@app.route("/api/speedtest-run", methods=["POST"])
def api_speedtest_run():
    """Ручний одноразовий speedtest на вимогу користувача - виконується
    синхронно (10-30с), бо це усвідомлена дія користувача, який готовий
    почекати на результат, а не фоновий цикл, що не повинен блокувати
    щось інше."""
    from app import speedtest_runner
    result = speedtest_runner.run_once()
    db.insert_speedtest_result(result)
    return jsonify(result)


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


BACKUP_FORMAT_VERSION = 2


@app.route("/api/settings-backup")
def api_settings_backup():
    """Повертає всі налаштування (Telegram config, фрази підпису,
    auto-reboot, перевизначені параметри app/config.py) одним JSON-файлом
    для завантаження. Bot token включається у відкритому вигляді - файл
    backup потрібно берегти як secret (не публікувати, не комітити в git).
    env_params містить лише РЕАЛЬНО перевизначені параметри (overridden),
    не всі значення за замовчуванням - інакше відновлення на іншому
    пристрої/версії коду затерло б нові дефолти застарілими значеннями."""
    token, chat_ids, enabled = telegram_notify.get_telegram_config()
    env_params = {
        p["key"]: p["current"]
        for p in config_editor.read_current_values()
        if p["overridden"]
    }
    backup = {
        "format_version": BACKUP_FORMAT_VERSION,
        "created_at": time.time(),
        "telegram_bot_token": token,
        "telegram_chat_ids": chat_ids,
        "telegram_enabled": enabled,
        "auto_reboot_enabled": db.get_auto_reboot_enabled(),
        "signature_phrases": telegram_notify.get_signature_phrases_text(),
        "signature_phrases_enabled": telegram_notify.get_signature_phrases_enabled(),
        "env_params": env_params,
    }
    return jsonify(backup)


@app.route("/api/settings-restore", methods=["POST"])
def api_settings_restore():
    """Відновлює налаштування з JSON, отриманого через /api/settings-backup.
    Приймає лише відомі поля - невідомі/сторонні ключі ігноруються.
    env_params (параметри app/config.py) записуються в env-файл так само,
    як через панель "Параметри моніторингу" - застосовуються лише після
    перезапуску сервісів (окрема кнопка на дашборді, тут не робимо цього
    автоматично, бо restore може виконуватись без наміру одразу рестартити)."""
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

        if payload.get("env_params"):
            ok, msg = config_editor.save_values(payload["env_params"])
            if ok:
                restored.append("параметри моніторингу (потрібен перезапуск сервісів)")
            else:
                restored.append(f"параметри моніторингу - помилка: {msg}")

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


def _execute_pi_power_action(cmd: list, event_kind: str, event_label: str,
                              success_text: str, fail_verb: str) -> tuple:
    """Спільна логіка для system-reboot/system-shutdown: виконати команду,
    записати подію в журнал, надіслати Telegram-сповіщення про результат."""
    ok, msg = _run_system_command(cmd)
    db.insert_event(event_kind, f"Ручне {event_label} Raspberry Pi через веб-інтерфейс: {msg}", success=ok)
    if ok:
        telegram_notify.send_message(success_text)
    else:
        telegram_notify.send_message(f"❌ Не вдалося {fail_verb} Raspberry Pi: {msg}")
    return ok, msg


@app.route("/api/system-reboot", methods=["POST"])
def api_system_reboot():
    ok, msg = _execute_pi_power_action(
        ["sudo", "systemctl", "reboot"], "pi_reboot", "перезавантаження",
        "🔁 Raspberry Pi перезавантажується вручну через веб-інтерфейс", "перезавантажити",
    )
    return jsonify({"success": ok, "message": msg})


@app.route("/api/system-shutdown", methods=["POST"])
def api_system_shutdown():
    ok, msg = _execute_pi_power_action(
        ["sudo", "systemctl", "poweroff"], "pi_shutdown", "виключення",
        "⏻ Raspberry Pi вимикається вручну через веб-інтерфейс", "вимкнути",
    )
    return jsonify({"success": ok, "message": msg})


def main():
    db.init_db()
    app.run(host=config.WEBUI_HOST, port=config.WEBUI_PORT)


if __name__ == "__main__":
    main()
