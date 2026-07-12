"""Flask веб-інтерфейс: дашборд статусу Starlink, історія, журнал подій, ручний reboot."""
import logging

from flask import Flask, jsonify, render_template, request

from app import config, db
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
    limit = min(int(request.args.get("limit", 50)), 500)
    return jsonify(db.get_recent_events(limit))


@app.route("/api/reboot-dish", methods=["POST"])
def api_reboot_dish():
    ok, msg = client.reboot_dish()
    db.insert_event("dish_reboot", f"Ручний reboot через веб-інтерфейс: {msg}", success=ok)
    return jsonify({"success": ok, "message": msg})


@app.route("/api/config")
def api_config():
    """Не чутливі налаштування — для відображення на дашборді."""
    return jsonify({
        "poll_interval_sec": config.POLL_INTERVAL_SEC,
        "max_consecutive_failures": config.MAX_CONSECUTIVE_FAILURES,
        "min_reboot_interval_sec": config.MIN_REBOOT_INTERVAL_SEC,
        "auto_update_enabled": config.AUTO_UPDATE_ENABLED,
    })


def main():
    db.init_db()
    app.run(host=config.WEBUI_HOST, port=config.WEBUI_PORT)


if __name__ == "__main__":
    main()
