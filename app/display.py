"""
Фізичний TFT-дисплей (ST7789, SPI) - показує live-статус Starlink
Mini (online/offline, throughput, ping, uptime) прямо на екрані,
підключеному до Pi, без потреби відкривати веб-дашборд.

Окремий процес (той самий патерн, що shutdown_button.py) - періодично
перемальовує кадр через Pillow і надсилає на дисплей бібліотекою
st7789 (SPI). Вимкнено за замовчуванням (DISPLAY_ENABLED=0) - не всі
мають цей дисплей підключений.
"""
import logging
import time

from app import config, db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("display")

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def _fmt_uptime(uptime_s) -> str:
    if not uptime_s:
        return "—"
    h = int(uptime_s) // 3600
    m = (int(uptime_s) % 3600) // 60
    return f"{h}г {m}хв"


def _status_lines(latest_metric: dict) -> list:
    """Формує рядки тексту для відображення - чиста функція без
    залежності від самого дисплея, легко тестується окремо."""
    if not latest_metric:
        return ["Немає даних"]

    online = bool(latest_metric.get("online"))
    lines = ["● ONLINE" if online else "○ OFFLINE"]
    if online:
        down = latest_metric.get("downlink_mbps")
        up = latest_metric.get("uplink_mbps")
        ping = latest_metric.get("ping_latency_ms")
        lines.append(f"\u2193 {down if down is not None else '—'} Мбіт/с")
        lines.append(f"\u2191 {up if up is not None else '—'} Мбіт/с")
        lines.append(f"Ping: {ping if ping is not None else '—'} мс")
    lines.append(f"Uptime: {_fmt_uptime(latest_metric.get('uptime_s'))}")
    return lines


def _load_font(size: int):
    from PIL import ImageFont
    for path in FONT_PATHS:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def run_forever(stop_event=None):
    if not config.DISPLAY_ENABLED:
        logger.info("DISPLAY_ENABLED не встановлено (0) - дисплей вимкнено, завершення")
        return

    try:
        import st7789
        from PIL import Image, ImageDraw
    except ImportError as e:
        logger.error("Пакети для дисплея не встановлено (st7789/Pillow): %s", e)
        return

    try:
        display = st7789.ST7789(
            port=config.DISPLAY_SPI_PORT,
            cs=config.DISPLAY_SPI_CS,
            dc=config.DISPLAY_DC_PIN,
            rst=config.DISPLAY_RST_PIN,
            backlight=config.DISPLAY_BL_PIN or None,
            width=config.DISPLAY_WIDTH,
            height=config.DISPLAY_HEIGHT,
            rotation=config.DISPLAY_ROTATION,
            spi_speed_hz=60_000_000,
        )
    except Exception as e:
        logger.error(
            "Не вдалося ініціалізувати дисплей (перевір SPI/піни в /etc/starlink-monitor/env): %s", e
        )
        return

    logger.info("Дисплей ініціалізовано (%dx%d, поворот %d°)",
                config.DISPLAY_WIDTH, config.DISPLAY_HEIGHT, config.DISPLAY_ROTATION)
    font_big = _load_font(26)
    font_small = _load_font(20)

    try:
        db.init_db()
    except Exception as e:
        logger.warning("Не вдалося ініціалізувати БД: %s", e)

    while True:
        if stop_event and stop_event.is_set():
            return
        try:
            latest = db.get_latest_metric()
            lines = _status_lines(latest)
            online = bool(latest and latest.get("online"))

            img = Image.new("RGB", (display.width, display.height), "black")
            draw = ImageDraw.Draw(img)
            y = 10
            for i, line in enumerate(lines):
                font = font_big if i == 0 else font_small
                color = ("lime" if online else "red") if i == 0 else "white"
                draw.text((10, y), line, font=font, fill=color)
                y += 34 if i == 0 else 28

            display.display(img)
        except Exception:
            logger.exception("Помилка оновлення дисплея")

        slept = 0
        while slept < config.DISPLAY_REFRESH_SEC:
            if stop_event and stop_event.is_set():
                return
            time.sleep(min(1, config.DISPLAY_REFRESH_SEC - slept))
            slept += 1


def main():
    run_forever()


if __name__ == "__main__":
    main()
