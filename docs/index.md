# Індекс проєкту

Навігатор по файлах Starlink Monitor. Детальний технічний опис —
[`architecture.md`](architecture.md); історія рішень і виправлених
багів — [`decisions-log.md`](decisions-log.md); загальний
опис/встановлення — [`../README.md`](../README.md).

## Корінь

| Файл | Опис |
|---|---|
| `README.md` | Опис проєкту, встановлення, конфігурація, усі env-параметри |
| `requirements.txt` | Python-залежності (pip) |
| `LICENSE` | Ліцензія |

## `app/` — Python-модулі

| Файл | Опис |
|---|---|
| `starlink_client.py` | gRPC-клієнт: статус dish/router, `reboot_dish()` |
| `monitor.py` | Watchdog: цикл опитування, авто-reboot, логування подій, запуск Telegram-бота |
| `webapp.py` | Flask, REST API, роздає `/`, `/settings`, `/stats`, `/healthz` |
| `db.py` | SQLite: metrics, events, system_metrics, router_status, settings |
| `telegram_notify.py` | Вихідні сповіщення + підпис-фрази |
| `telegram_bot.py` | Вхідні команди `/status`, `/reboot`, `/help` |
| `labels.py` | Спільні label-мапи (monitor.py + telegram_bot.py) |
| `system_metrics.py` | Метрики Pi (CPU/RAM/диск/температура) + apt-оновлення |
| `shutdown_button.py` | Фізична кнопка виключення через GPIO (окремий процес) |
| `display.py` | Фізичний TFT-дисплей статусу (ST7789, SPI, окремий процес) |
| `gpio_utils.py` | Спільна gpiod v1/v2-логіка читання GPIO (shutdown_button.py + display.py) |
| `speedtest_runner.py` | Періодичний реальний speedtest (вимкнено за замовчуванням) |
| `config.py` | Конфігурація, env-змінні |
| `config_editor.py` | Читання/валідація/запис `/etc/starlink-monitor/env` через `/settings` |

## `static/` — фронтенд

| Файл | Опис |
|---|---|
| `common.js` | Спільні функції для dashboard.js/stats.js (напр. `fmtTime`) |
| `dashboard.js` | Логіка головної сторінки (`/`) |
| `settings.js` | Логіка сторінки налаштувань (`/settings`) |
| `stats.js` | Логіка сторінки статистики (`/stats`) |
| `pwa.js`, `sw.js`, `manifest.json` | PWA (встановлення як застосунок, offline-кеш) |
| `style.css` | Стилі усіх сторінок |
| `favicon.ico`, `icon-192.png`, `icon-512.png`, `logo.png` | Іконки |

## `templates/` — HTML

| Файл | Опис |
|---|---|
| `index.html` | Головний дашборд |
| `settings.html` | Сторінка налаштувань |
| `stats.html` | Сторінка статистики/графіків |

## `systemd/` — unit-файли

| Файл | Опис |
|---|---|
| `starlink-monitor.service` | Watchdog + Telegram-бот |
| `starlink-webui.service` | Flask dashboard |
| `starlink-shutdown-button.service` | Слухає GPIO-кнопку виключення |
| `starlink-display.service` | Фізичний TFT-дисплей |
| `starlink-grpc-fetch.service` | Одноразово тягне `starlink_grpc.py` при старті |
| `starlink-wan-failover.service`/`.timer` | Періодична перевірка інтернету через wlan0 |
| `starlink-monitor-healthcheck.service`/`.timer` | Раз/хв опитує `/healthz`, force-restart при зависанні |

## `scripts/` — bash

| Файл | Опис |
|---|---|
| `install.sh` | Повне встановлення (системні пакети, venv, sudo-права, systemd) |
| `update.sh` | Оновлення вже встановленого проєкту |
| `uninstall.sh` | Повне видалення |
| `fetch_starlink_grpc.sh` | Завантаження `starlink_grpc.py` (чекає dish WiFi) |
| `wan_failover_check.sh` | Перевірка інтернету через wlan0, коригування route-metric |
| `watchdog_healthcheck.sh` | Перевірка `/healthz`, force-restart при не-200 |

## `docs/` — документація

| Файл | Опис |
|---|---|
| `architecture.md` | Детальний технічний опис усіх компонентів |
| `decisions-log.md` | Історичний журнал рішень і виправлених багів (не стискається) |
| `plan.md` | Початковий план проєкту |
| `index.md` | Цей файл |
