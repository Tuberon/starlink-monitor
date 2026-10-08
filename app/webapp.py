"""Flask веб-інтерфейс: дашборд статусу Starlink, журнал подій, ручний reboot."""
import logging
import math
import os
import re
import time
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request
from flask.json.provider import DefaultJSONProvider
from flask.typing import ResponseReturnValue

from app import config, config_editor, db, i18n, log_redact, pi_power, services, telegram_notify
from app.starlink_client import StarlinkClient

logging.basicConfig(level=logging.INFO)
log_redact.install()   # токен бота ніколи не потрапляє в журнал (див. app/log_redact.py)


class _QuietSuccessfulGets(logging.Filter):
    """Werkzeug пише рядок на КОЖЕН запит, а дашборд опитує сервер ~3500 разів/год на вкладку (~83 000
    рядків ≈ 8 МБ/добу на SD). Відкидаємо лише успішні GET (опитування, статика); POST-дії (з IP
    джерела: єдиний слід дій без автентифікації) і помилки 4xx/5xx лишаються.
    """
    _SUCCESSFUL_GET = re.compile(r'"GET [^"]*" [23]\d\d ')

    def filter(self, record: logging.LogRecord) -> bool:
        return not self._SUCCESSFUL_GET.search(record.getMessage())


logging.getLogger("werkzeug").addFilter(_QuietSuccessfulGets())
logger = logging.getLogger("webapp")

app = Flask(__name__, template_folder="../templates", static_folder="../static")
# Обмеження тіла запиту: Flask за замовчуванням його не має, а /api/settings-restore
# читає JSON цілком у пам'ять (Pi Zero - 415 МБ). Реальний бекап станції з 233
# пристроями - ~42 КБ; 5 МБ - запас понад 100 разів. Перевищення -> HTTP 413.
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
# Кешування статики (dashboard.js, logo.png) на стороні браузера - зменшує
# кількість запитів при кожному відкритті/перезавантаженні дашборду,
# помітно на слабкому WiFi-каналі Pi Zero 2 W. API-ендпоінти (динамічні
# дані) цього не стосуються - кешується лише /static/*.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 3600
client = StarlinkClient()

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")


def _static_v(filename: str) -> str:
    """URL статичного файлу з ?v=<mtime> (cache-busting): після update.sh файл змінюється, URL змінюється
    разом із ним, і браузер одразу бере свіжу версію, а не чекає SEND_FILE_MAX_AGE_DEFAULT=3600 с чи
    ручного hard refresh.
    """
    path = os.path.join(_STATIC_DIR, filename)
    try:
        v = int(os.path.getmtime(path))
    except OSError:
        v = 0
    return f"/static/{filename}?v={v}"


app.jinja_env.globals["static_v"] = _static_v


# window.I18N вбудовується в КОЖНУ сторінку: раніше всі 366 ключів (~58 КБ із ~66 КБ сторінки), хоча JS
# використовує ~121. Набір визначається при старті скануванням t('...') у static/*.js (виклики лише з
# літералами: гарантує test_js_i18n_calls_are_static_literals, інакше динамічний ключ тихо випав би).
_JS_T_CALL_RE = re.compile(r"(?<![\w.])t\(\s*['\"](\w+)['\"]")


def _collect_js_i18n_keys(static_dir: str) -> frozenset[str]:
    keys: set[str] = set()
    for name in os.listdir(static_dir):
        if name.endswith(".js"):
            with open(os.path.join(static_dir, name), encoding="utf-8") as f:
                keys.update(_JS_T_CALL_RE.findall(f.read()))
    return frozenset(keys)


_JS_I18N_KEYS = _collect_js_i18n_keys(app.static_folder or "static")

# UTF-8 замість \uXXXX для кирилиці: 2 байти на літеру замість 6 -
# і в window.I18N, і в JSON-відповідях API. Безпека вбудовування в
# <script> не залежить від цього: tojson окремо екранує < > & '.


def _scrub_non_finite(value: Any) -> Any:
    """float inf/-inf/NaN -> None рекурсивно (див. db._finite)."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _scrub_non_finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_scrub_non_finite(v) for v in value]
    return value


class _FiniteJSONProvider(DefaultJSONProvider):
    """Flask за замовчуванням віддає inf як `Infinity`, а NaN як `NaN` - це
    НЕвалідний JSON, `JSON.parse` у браузері падає, і дашборд переставав
    оновлювати статус. Будь-яке нескінченне значення (навіть уже збережене в
    БД до виправлення в db._finite) віддається як null."""

    def dumps(self, obj: Any, **kwargs: Any) -> str:
        return super().dumps(_scrub_non_finite(obj), **kwargs)


@app.before_request
def _reject_cross_site_posts() -> Any:
    """Захист від CSRF. Автентифікації немає (рішення власника: довірена LAN), але сторінка з будь-якого
    сайту у браузері комп'ютера LAN могла б надіслати "просту" міжсайтову форму (text/plain, без
    прелайту) на /api/system-shutdown чи /api/system-reboot й вимкнути станцію разом з інтернетом
    (виміряно: усі POST виконувались). Браузер сам виставляє Origin і Sec-Fetch-Site (скрипт сторінки їх
    не підробить); curl/скрипти без заголовків працюють як раніше. Від DNS-rebinding не захищає (запит
    тоді "same-origin" з чужого імені).
    """
    if request.method != "POST":
        return None
    fetch_site = request.headers.get("Sec-Fetch-Site")
    origin = request.headers.get("Origin")
    cross_site = fetch_site is not None and fetch_site not in ("same-origin", "none")
    if origin is not None and not cross_site:
        cross_site = urlparse(origin).netloc != request.host            # "null" (opaque origin) -> порожній netloc -> блок
    if cross_site:
        return jsonify({"success": False, "error": i18n.t("cross_site_blocked")}), 403
    return None


_json_provider = _FiniteJSONProvider(app)
_json_provider.ensure_ascii = False
app.json = _json_provider


@app.context_processor
def _inject_i18n() -> dict[str, Any]:
    """t()/ui_lang/i18n_json для шаблонів - усі три з ОДНОГО читання
    мови з БД на рендер. Раніше глобальний i18n.t читав мову на кожен
    {{ t('key') }} (~55 SQLite-з'єднань на рендер головної сторінки,
    виміряно); тепер - одне."""
    lang = i18n.get_language()
    return {
        "t": i18n.translator(lang),
        "ui_lang": lang,
        "i18n_json": i18n.all_translations_for_current_lang(lang, _JS_I18N_KEYS),
    }


def _json_body() -> dict[str, Any]:
    """Тіло запиту як JSON-об'єкт. Відсутнє/некоректне тіло або валідний
    JSON, що НЕ є об'єктом ([1,2], "str", 123) -> {}. Раніше обробники
    брали get_json() і далі payload.get(...) - на не-об'єктному JSON це
    давало 500 з трейсбеком (знайдено фазингом ендпоінтів)."""
    data = request.get_json(silent=True)
    return _scrub(data) if isinstance(data, dict) else {}


def _scrub(value: Any) -> Any:
    """Рядки з тіла JSON очищуються від одиноких сурогатів (`"\\ud800"` валідний JSON, але не кодується в UTF-8: sqlite3
    кидав UnicodeEncodeError, обробник віддавав 500 замість 4xx)."""
    if isinstance(value, str):
        return db._clean_text(value)
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {db._clean_text(k) if isinstance(k, str) else k: _scrub(v) for k, v in value.items()}
    return value


def _bad_request(field: str) -> ResponseReturnValue:
    return jsonify({"success": False, "message": i18n.t("invalid_field", field=field)}), 400


@app.route("/")
def index() -> ResponseReturnValue:
    return render_template("index.html")


@app.route("/settings")
def settings_page() -> ResponseReturnValue:
    return render_template("settings.html")


@app.route("/stats")
def stats_page() -> ResponseReturnValue:
    return render_template("stats.html")


@app.route("/manifest.json")
def manifest() -> ResponseReturnValue:
    """PWA-маніфест генерується динамічно (не статичний файл у
    static/), щоб name/description реально показувались поточною
    мовою інтерфейсу при встановленні на головний екран телефону -
    статичний JSON не міг би реагувати на зміну мови через
    /api/set-language без окремого механізму."""
    tr = i18n.translator()
    return jsonify({
        "name": tr("manifest_name"),
        "short_name": tr("manifest_short_name"),
        "description": tr("manifest_description"),
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#0b1220",
        "theme_color": "#0b1220",
        "orientation": "any",
        "icons": [
            {"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
        ],
    })


@app.route("/api/set-language", methods=["POST"])
def api_set_language() -> ResponseReturnValue:
    """Одна мова для всього інтерфейсу (веб + Telegram) - не per-user,
    одна людина/сім'я керує одним Pi. Валідність значення перевіряється
    тут (не покладаємось на frontend), щоб db.get_setting("ui_language")
    ніколи не міг повернути щось поза SUPPORTED_LANGS."""
    lang = _json_body().get("lang", "")
    if lang not in i18n.SUPPORTED_LANGS:
        return jsonify({"success": False, "message": i18n.t("api_unsupported_language", lang=lang)}), 400
    db.set_setting("ui_language", lang)
    return jsonify({"success": True, "message": "ok"})


@app.route("/healthz")
def healthz() -> ResponseReturnValue:
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
            # +DISH_METRICS_BATCH_INTERVAL_SEC - при буферизації dish-
            # метрик (SD-card-wear reduction) останній запис у БД може
            # відставати від реального часу опитування на ДО цього
            # інтервалу, чекаючи наступного batch-flush. Без цього
            # додавання сама буферизація хибно спрацьовувала б як
            # "watchdog завис", хоча він реально працює нормально.
            max_age_sec = config.POLL_INTERVAL_SEC * 3 + config.DISH_METRICS_BATCH_INTERVAL_SEC
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
def api_status() -> ResponseReturnValue:
    latest = db.get_latest_metric()
    # uptime_24h_pct прибрано: поле не читали ні дашборд, ні Telegram, а
    # COUNT по metrics за 24 год займав ~70% часу цього запиту (кожні
    # 1.5 с). До того ж щонічне вимкнення Starlink робило цифру хибною.
    return jsonify({"latest": latest})


@app.route("/api/events")
def api_events() -> ResponseReturnValue:
    # Межі 1..500 з обох боків: раніше min(int(...), 500) пропускав
    # від'ємне значення, а LIMIT -1 у SQLite означає "без обмеження" -
    # ?limit=-1 віддавав увесь журнал; нечислове значення давало 500.
    try:
        limit = int(request.args.get("limit", 30))
    except ValueError:
        limit = 30
    return jsonify(db.get_recent_events(max(1, min(limit, 500))))


@app.route("/api/system-status")
def api_system_status() -> ResponseReturnValue:
    latest = db.get_latest_system_metric()
    return jsonify({"latest": latest})


@app.route("/api/router-status")
def api_router_status() -> ResponseReturnValue:
    return jsonify({"latest": db.get_router_status()})


@app.route("/api/reboot-dish", methods=["POST"])
def api_reboot_dish() -> ResponseReturnValue:
    ok, msg = client.reboot_dish()
    db.insert_event("dish_reboot", f"Ручний reboot через веб-інтерфейс: {msg}", success=ok)
    if ok:
        telegram_notify.send_message(i18n.t("tg_manual_dish_reboot"))
    else:
        telegram_notify.send_message(i18n.t("tg_manual_dish_reboot_failed", msg=msg))
    return jsonify({"success": ok, "message": msg})


@app.route("/api/check-updates", methods=["POST"])
def api_check_updates() -> ResponseReturnValue:
    """Ручна перевірка стану оновлень - див. services.check_updates_now()
    для повної логіки (спільна з /checkupdates у telegram_bot.py)."""
    def notify(text: str) -> None:
        telegram_notify.send_message(text)

    dish_status, router_info = services.check_updates_now(client, notify)

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
def api_config() -> ResponseReturnValue:
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
def api_set_auto_reboot() -> ResponseReturnValue:
    """Вмикає/вимикає автоматичний reboot dish/router при готовому
    оновленні. Зберігається в БД (не в env-файлі), тож застосовується
    одразу, без перезапуску сервісу."""
    payload = _json_body()
    enabled = bool(payload.get("enabled"))
    db.set_auto_reboot_enabled(enabled)
    db.insert_event(
        "auto_reboot_toggled",
        f"Автоматичний reboot при готовому оновленні: {'увімкнено' if enabled else 'вимкнено'}",
        success=True,
    )
    return jsonify({"success": True, "enabled": enabled})


@app.route("/api/telegram-config")
def api_get_telegram_config() -> ResponseReturnValue:
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
def api_set_telegram_config() -> ResponseReturnValue:
    payload = _json_body()
    token = payload.get("token")
    chat_ids_raw = payload.get("chat_ids")
    enabled = payload.get("enabled")
    if token is not None and not isinstance(token, str):
        return _bad_request("token")

    chat_ids = None
    if chat_ids_raw is not None:
        if isinstance(chat_ids_raw, str):
            chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
        elif isinstance(chat_ids_raw, list):
            # chat_id у Telegram числовий - клієнт може надіслати числа
            chat_ids = [str(c).strip() for c in chat_ids_raw if str(c).strip()]
        else:
            return _bad_request("chat_ids")

    telegram_notify.set_telegram_config(
        token=token if token else None,
        chat_ids=chat_ids,
        enabled=enabled if enabled is not None else None,
    )
    db.insert_event("telegram_config_updated", "Налаштування Telegram-сповіщень оновлено", success=True)
    return jsonify({"success": True})


def _find_older_candidates(
    candidates: list[str], baseline_candidates: list[str], current_installed: Optional[str] = None,
) -> list[tuple[str, str]]:
    """Channel-aware перевірка "старіші за вже відому": кожен кандидат порівнюється ЛИШЕ з baseline ТОГО
    САМОГО build-каналу (mr/cr, db.version_channel()), не глобально — різні апаратні ревізії отримують
    оновлення з незалежних каналів з різними датами (cr81950 від 07.06 хибно відхилявся як старіший за
    mr82648 від 07.19). Кандидат БЕЗ явного каналу (проста дата "2020.01.01") — універсальний формат:
    порівнюється з усіма baseline (глобальна поведінка); ізоляція за каналом — лише для кандидата з
    явним каналом. current_installed: якщо dish/router реально відкотився (SpaceX-side) нижче раніше
    введеного target, кандидат, що ТОЧНО збігається з встановленою версією, дозволяється завжди —
    узгодження з реальністю не є випадковим відкатом.
    """
    older = []
    for c in candidates:
        if current_installed and c == current_installed:
            continue
        c_channel = db.version_channel(c)
        if c_channel is None:
            relevant_baseline = baseline_candidates
        else:
            relevant_baseline = [b for b in baseline_candidates if db.version_channel(b) == c_channel]
        if not relevant_baseline:
            continue
        channel_baseline = max(relevant_baseline, key=db.version_key)
        if db.is_older_version(c, channel_baseline):
            older.append((c, channel_baseline))
    return older


@app.route("/api/target-versions")
def api_get_target_versions() -> ResponseReturnValue:
    """Поточна встановлена версія (dish_current/router_current) - для
    pre-fill інпута на /settings при першому введенні (першочергово
    показуємо вже відому версію, а не порожнє поле) і для порівняння
    на фронтенді, чи target вже досягнуто."""
    latest = db.get_latest_metric()
    router = db.get_router_status()
    return jsonify({
        "dish_target": db.get_setting("dish_target_version"),
        "router_target": db.get_setting("router_target_version"),
        "dish_current": latest.get("software_version") if latest else None,
        "router_current": router.get("software_version") if router else None,
    })


def _validate_and_save_target(
    target_raw: str, target_key: str, current_installed: Optional[str],
) -> tuple[str, list[tuple[str, str]]]:
    """Валідує й зберігає одне target-поле (dish або router — раніше окремі дублі). Повертає КОД стану, не
    текст: "cleared" (порожній рядок), "saved" (валідні кандидати), "rejected" (є старіші — друге
    значення: пари (кандидат, відома версія)), "unchanged". Тексти будує викликач — мовою інтерфейсу для
    відповіді й українською для журналу подій.
    """
    target_raw = target_raw.strip()
    if not target_raw:
        db.set_setting(target_key, "")
        return "cleared", []

    candidates = db.parse_version_list(target_raw)
    baseline_candidates = db.parse_version_list(db.get_setting(target_key)) + (
        [current_installed] if current_installed else []
    )
    older = _find_older_candidates(candidates, baseline_candidates, current_installed)
    if older:
        return "rejected", older
    if candidates:
        db.set_setting(target_key, target_raw)
        return "saved", []
    return "unchanged", []


_TARGET_NAME_KEYS = {"dish": "comp_dish_nom", "router": "comp_router_nom"}


def _target_saved_label(tr: Callable[..., str], component: str, status: str) -> str:
    name = tr(_TARGET_NAME_KEYS[component])
    return tr("api_target_cleared", component=name) if status == "cleared" else name


@app.route("/api/target-versions", methods=["POST"])
def api_set_target_versions() -> ResponseReturnValue:
    """Приймає нову очікувану версію (чи кілька через кому — для різних апаратних ревізій) лише якщо КОЖЕН
    кандидат не старіший за базову лінію (максимум із попереднього target-списку й поточної встановленої
    версії) АБО ТОЧНО збігається з встановленою (узгодження з реальністю після SpaceX-side відкату — не
    випадковий відкат). Захищає від описки й забутого новішого target: версії рухаються лише вперед.
    Якщо хоч один кандидат старіший — відхиляється ВЕСЬ список поля (без часткового прийняття).
    """
    payload = _json_body()
    dish_target = payload.get("dish_target")
    router_target = payload.get("router_target")
    for field, value in (("dish_target", dish_target), ("router_target", router_target)):
        if value is not None and not isinstance(value, str):
            return _bad_request(field)
    latest = db.get_latest_metric()
    router_status = db.get_router_status()
    installed = {
        "dish": latest["software_version"] if latest and latest.get("software_version") else None,
        "router": router_status["software_version"] if router_status and router_status.get("software_version") else None,
    }
    tr, uk = i18n.translator(), i18n.translator("uk")   # відповідь - мовою інтерфейсу, журнал - українською

    saved_ui: list[str] = []
    saved_uk: list[str] = []
    rejected: list[str] = []
    for component, target in (("dish", dish_target), ("router", router_target)):
        if target is None:
            continue
        status, older = _validate_and_save_target(target, f"{component}_target_version", installed[component])
        if status in ("saved", "cleared"):
            saved_ui.append(_target_saved_label(tr, component, status))
            saved_uk.append(_target_saved_label(uk, component, status))
        elif status == "rejected":
            pairs = ", ".join(tr("api_target_vs", candidate=c, baseline=b) for c, b in older)
            rejected.append(tr("api_target_older", component=tr(_TARGET_NAME_KEYS[component]), pairs=pairs))

    if saved_uk:
        db.insert_event("target_versions_updated", f"Очікувані версії прошивок оновлено: {', '.join(saved_uk)}", success=True)

    if rejected:
        return jsonify({"success": bool(saved_ui), "message": tr("api_target_rejected", details="; ".join(rejected))})
    if saved_ui:
        return jsonify({"success": True, "message": tr("api_saved", items=", ".join(saved_ui))})
    return jsonify({"success": True, "message": tr("api_no_changes")})


@app.route("/api/telegram-test", methods=["POST"])
def api_telegram_test() -> ResponseReturnValue:
    ok, msg = telegram_notify.test_connection()
    if ok:
        send_ok, send_msg = telegram_notify.send_message(
            i18n.t("tg_test_message")
        )
        return jsonify({"success": ok and send_ok, "message": f"{msg}; {send_msg}"})
    return jsonify({"success": False, "message": msg})


@app.route("/api/settings-backup")
def api_settings_backup() -> ResponseReturnValue:
    """Повертає всі налаштування одним JSON-файлом для завантаження -
    див. services.build_backup_dict() для повної логіки (спільна з
    автоматичним періодичним backup)."""
    return jsonify(services.build_backup_dict())


@app.route("/api/send-backup-telegram", methods=["POST"])
def api_send_backup_telegram() -> ResponseReturnValue:
    """Ручна кнопка на /settings: негайно надсилає ОСТАННІЙ наявний auto-backup у Telegram, незалежно від
    TELEGRAM_BACKUP_ENABLED/_INTERVAL_HOURS (вони стосуються лише періодичного надсилання). Новий backup
    не створює; якщо жодного ще немає (AUTO_BACKUP_ENABLED=0 чи щойно встановлено) — повертає зрозуміле
    повідомлення, не 500.
    """
    ok, msg = services.send_latest_backup_to_telegram()
    return jsonify({"success": ok, "message": msg})


@app.route("/api/settings-restore", methods=["POST"])
def api_settings_restore() -> ResponseReturnValue:
    """Відновлює налаштування з JSON, отриманого через /api/settings-backup.
    Приймає лише відомі поля - невідомі/сторонні ключі ігноруються.
    env_params (параметри app/config.py) записуються в env-файл так само,
    як через панель "Параметри моніторингу" - застосовуються лише після
    перезапуску сервісів (окрема кнопка на дашборді, тут не робимо цього
    автоматично, бо restore може виконуватись без наміру одразу рестартити)."""
    payload = _json_body()
    if "format_version" not in payload:
        return jsonify({"success": False, "message": i18n.t("api_restore_bad_format")})

    # (ключ перекладу, параметри): відповідь - мовою інтерфейсу, журнал - українською
    restored: list[tuple[str, dict[str, Any]]] = []
    try:
        if "telegram_bot_token" in payload or "telegram_chat_ids" in payload or "telegram_enabled" in payload:
            telegram_notify.set_telegram_config(
                token=payload.get("telegram_bot_token"),
                chat_ids=payload.get("telegram_chat_ids"),
                enabled=payload.get("telegram_enabled"),
            )
            restored.append(("rs_telegram", {}))

        if "auto_reboot_enabled" in payload:
            db.set_auto_reboot_enabled(bool(payload["auto_reboot_enabled"]))
            restored.append(("rs_auto_reboot", {}))

        if payload.get("dish_target_version"):
            db.set_setting("dish_target_version", payload["dish_target_version"])
            restored.append(("rs_dish_target", {}))

        if payload.get("router_target_version"):
            db.set_setting("router_target_version", payload["router_target_version"])
            restored.append(("rs_router_target", {}))

        if payload.get("known_devices"):
            added = db.merge_known_devices(payload["known_devices"])
            restored.append(("rs_devices", {"added": added, "total": len(payload["known_devices"])}))

        if payload.get("env_params"):
            ok, msg = config_editor.save_values(payload["env_params"])
            if ok:
                restored.append(("rs_env_ok", {}))
            else:
                restored.append(("rs_env_error", {"error": msg}))

        def render(tr: Callable[..., str]) -> str:
            return ", ".join(tr(key, **kw) for key, kw in restored) or tr("rs_nothing")

        db.insert_event("settings_restored", f"Відновлено з backup: {render(i18n.translator('uk'))}", success=True)
        return jsonify({"success": True, "message": i18n.t("api_restored", items=render(i18n.translator()))})
    except Exception as e:
        db.insert_event("settings_restored", f"Помилка відновлення backup: {e}", success=False)
        return jsonify({"success": False, "message": str(e)})


@app.route("/api/env-config")
def api_get_env_config() -> ResponseReturnValue:
    params = config_editor.read_current_values()
    tr = i18n.translator()
    for p in params:
        p["label"] = tr(p["label"])
    return jsonify({
        "params": params,
        "category_labels": {k: tr(v) for k, v in config_editor.CATEGORY_LABELS.items()},
    })


@app.route("/api/env-config", methods=["POST"])
def api_set_env_config() -> ResponseReturnValue:
    payload = _json_body()
    values = payload.get("values", {})
    if not isinstance(values, dict):
        return _bad_request("values")
    ok, msg = config_editor.save_values(values)
    db.insert_event("env_config_updated", f"Параметри config.py оновлено: {msg}", success=ok)
    return jsonify({"success": ok, "message": msg})


@app.route("/api/env-config-restart", methods=["POST"])
def api_restart_after_env_change() -> ResponseReturnValue:
    """Перезапускає starlink-monitor.service і starlink-webui.service, щоб
    застосувати щойно змінені env-параметри (читаються один раз при старті).
    webui.service перезапускає й самого себе - відповідь клієнту може не
    дійти, це очікувано."""
    ok1, msg1 = pi_power.run_system_command(["sudo", "systemctl", "restart", "starlink-monitor.service"])
    db.insert_event("service_restart", f"starlink-monitor.service: {msg1}", success=ok1)
    ok2, msg2 = pi_power.run_system_command(["sudo", "systemctl", "restart", "starlink-webui.service"])
    return jsonify({"success": ok1 and ok2, "message": f"monitor: {msg1}; webui: {msg2}"})


@app.route("/api/system-reboot", methods=["POST"])
def api_system_reboot() -> ResponseReturnValue:
    ok, msg = pi_power.execute_pi_power_action(
        ["sudo", "systemctl", "reboot"], "reboot", "pi_reboot",
        "Ручне перезавантаження Raspberry Pi через веб-інтерфейс",
        i18n.t("tg_pi_reboot_web"), i18n.t("verb_reboot"),
    )
    return jsonify({"success": ok, "message": msg})


@app.route("/api/system-shutdown", methods=["POST"])
def api_system_shutdown() -> ResponseReturnValue:
    ok, msg = pi_power.execute_pi_power_action(
        ["sudo", "systemctl", "poweroff"], "poweroff", "pi_shutdown",
        "Ручне виключення Raspberry Pi через веб-інтерфейс",
        i18n.t("tg_pi_shutdown_web"), i18n.t("verb_shutdown"),
    )
    return jsonify({"success": ok, "message": msg})


def main() -> None:
    db.init_db()
    app.run(host=config.WEBUI_HOST, port=config.WEBUI_PORT)


if __name__ == "__main__":
    main()
