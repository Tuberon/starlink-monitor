# Архітектура: Starlink Mini Monitor & Watchdog

## Призначення

Моніторинг і автоматичне обслуговування Starlink Mini через Raspberry
Pi Zero 2 W: веб-дашборд, watchdog з авто-reboot, Telegram-сповіщення
й вхідні команди.

## Фізична топологія

Starlink Mini = **два логічні пристрої в одному корпусі**, кожен зі
своєю прошивкою:
- **dish** (тарілка): `192.168.100.1:9200`, gRPC через `starlink_grpc.py`
- **router** (WiFi-роутер): `192.168.1.1:9000`, gRPC через `grpcurl` subprocess

Це підтверджено живими викликами (`grpcurl describe`) під час
розробки — обидва мають окремі `DeviceInfo`, окремі стани оновлення
ПЗ (`SoftwareUpdateState` для dish, `WifiSoftwareUpdateState` для
router — різні enum з різними назвами станів).

## Модулі (app/)

| Файл | Відповідальність |
|---|---|
| `starlink_client.py` | gRPC-клієнт: статус dish/router, reboot_dish() |
| `monitor.py` | Watchdog: цикл опитування, авто-reboot, логування подій, запуск Telegram-бота |
| `webapp.py` | Flask, REST API, роздає `/`, `/settings`, `/stats`, `/healthz` |
| `db.py` | SQLite: metrics, events, system_metrics, router_status, settings |
| `telegram_notify.py` | Вихідні сповіщення + підпис-фрази |
| `telegram_bot.py` | Вхідні команди `/status`, `/reboot`, `/help` (обробка кожного update у пулі потоків, не блокує polling) |
| `labels.py` | Спільні label-мапи (monitor.py + telegram_bot.py, без дублювання) |
| `system_metrics.py` | Метрики самого Pi (CPU/RAM/диск/температура) |
| `shutdown_button.py` | Фізична кнопка виключення через GPIO (окремий процес) |
| `speedtest_runner.py` | Періодичний реальний speedtest (потік у monitor.py, вимкнено за замовчуванням) |
| `config.py` | Конфігурація, env-змінні |
| `config_editor.py` | Читання/валідація/запис `/etc/starlink-monitor/env` через `/settings` |

## Автоматичний reboot dish/router — умови спрацювання

1. **Watchdog**: dish не відповідає N опитувань поспіль
2. **Update-ready dish**: `update_state == "REBOOT_REQUIRED"`
3. **Update-ready router**: `update_state == "REBOOT_PENDING"`

Усі три через `db.get_auto_reboot_enabled()` (runtime, перемикається
з веб-інтерфейсу без перезапуску сервісу) і захищені
`MIN_REBOOT_INTERVAL_SEC` (120с) від reboot-loop.

`Watchdog.first_failure_ts` відслідковує тривалість безперервної
недоступності dish. Якщо вона перевищує `NOTIFICATIONS_MUTE_AFTER_SEC`
(900с) — Telegram-сповіщення про watchdog auto-reboot придушуються
(`db.insert_event` не зачіпається, лише `_notify`); відновлення
зв'язку завжди повідомляється.

## systemd-сервіси

| Сервіс | Роль | Особливості безпеки |
|---|---|---|
| `starlink-monitor.service` | Watchdog + Telegram-бот (потік) | `NoNewPrivileges=true`, `AmbientCapabilities=CAP_NET_RAW` (SO_BINDTODEVICE), `CapabilityBoundingSet` звужено до цієї capability |
| `starlink-webui.service` | Flask dashboard | БЕЗ `NoNewPrivileges` (потрібен sudo для reboot/poweroff Pi), `AmbientCapabilities=CAP_NET_RAW`, `CapabilityBoundingSet` НЕ звужено (sudo systemctl reboot успадкував би обмеження), `ReadWritePaths` включає `signature_phrases.txt` |
| `starlink-shutdown-button.service` | Слухає GPIO-кнопку виключення | БЕЗ `NoNewPrivileges` (sudo poweroff), `Restart=on-failure` (не `always` — чистий вихід при вимкненій кнопці не збій) |
| `starlink-grpc-fetch.service` | Одноразово тягне `starlink_grpc.py` при старті | — |
| `starlink-wan-failover.service`/`.timer` | Періодична (кожні ~20с) перевірка інтернету через wlan0, коригування route-metric | root-сервіс; `CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW` — навіть root тут без решти системних можливостей |
| `starlink-monitor-healthcheck.service`/`.timer` | Раз/хв опитує `/healthz`, force-restart `starlink-monitor.service` при не-200 (deadlock/livelock, не crash — `Restart=always` цього не бачить) | root-сервіс (потрібен для `systemctl restart` іншого юніта); `NoNewPrivileges=true`, `ProtectSystem=strict` |

`ProtectSystem=strict` на всіх — файлова система read-only, крім явно
дозволених шляхів.

## Встановлення / оновлення

- `scripts/install.sh` — детектує install vs update режим
  (`/opt/starlink-monitor` існує чи ні). У update-режимі: пропускає
  apt/pip якщо `requirements.txt` не змінився; **виявляє суттєві
  зміни** (нові пакети — не просто зміна версії) і при виявленні
  повністю видаляє й перевстановлює; `app/signature_phrases.txt`
  (відредагований користувачем) зберігається окремо і відновлюється
  після перевстановлення. Наприкінці, **лише в install-режимі** —
  опційний інтерактивний блок налаштування статичних IP для eth0/wlan0
  (з підтвердженням, дефолти редаговані), вимикає конфліктуючий `dhcpcd`.
- `scripts/update.sh` — ручне оновлення: sha256-перевірка архіву,
  розпакування, виклик install.sh.
- `scripts/uninstall.sh` — зупиняє й видаляє сервіси, sudoers-правило,
  код (`/opt/starlink-monitor`). БД (`/var/lib/starlink-monitor`) і
  env-конфіг (`/etc/starlink-monitor`) видаляються лише після окремого
  підтвердження — за замовчуванням лишаються для повторного встановлення.

## База даних (SQLite, /var/lib/starlink-monitor/history.db)

WAL journal_mode — паралельне читання (webui) і запис (monitor) без блокувань.

- `metrics` — історія опитувань dish (throughput, latency, dish_id, update_state, ...)
- `events` — журнал подій (reboot, зміни стану, попередження, підключення
  нової тарілки). Повтори того самого `kind`+`message` підряд стискаються
  в один рядок (`count`, `last_ts` замість нового запису щоразу)
- `system_metrics` — CPU/RAM/диск/температура Pi
- `router_status` — останній відомий стан роутера, включно зі списком
  під'єднаних WiFi-клієнтів (`clients`: ім'я/MAC, IP, діапазон, сигнал,
  час у мережі)
- `settings` — runtime key-value (auto_reboot_enabled, telegram config, known_dish_ids)
- `known_devices` — по одному рядку на dish_id: версії ПЗ dish/router
  і час останньої зміни кожної. Джерело для `/id <dish_id>` у Telegram-боті.

Усі таблиці мають автоматичну міграцію колонок при `init_db()` —
безпечно для вже існуючих БД при оновленні коду.

## Backup/restore налаштувань

`GET /api/settings-backup` віддає JSON (Telegram bot token, chat_ids,
enabled, auto_reboot_enabled, вміст і перемикач signature_phrases,
`env_params` — лише перевизначені параметри app/config.py) —
завантажується браузером як файл. `POST /api/settings-restore`
приймає той самий формат і застосовує лише відомі поля (env_params
через `config_editor.save_values()`, застосовується після рестарту
сервісів). Bot token у файлі — у відкритому вигляді, файл backup
потрібно берегти як secret.

## Фізична кнопка виключення (GPIO)

Окремий процес (`app/shutdown_button.py`, сервіс
`starlink-shutdown-button.service`), не інтегрований у `monitor.py`
чи `webapp.py` — свідома ізоляція, бо GPIO-доступ вимагає групу
`gpio` і `python3-libgpiod` (системний пакет, не pip), а не всі
установки мають фізичну кнопку.

- Вимкнено за замовчуванням (`SHUTDOWN_BUTTON_GPIO_PIN=0`) — сервіс
  одразу виходить з кодом 0, не помилка
- `gpiod` (character-device API, не застарілий `RPi.GPIO`) слухає
  пін з внутрішнім pull-up; утримання довше
  `SHUTDOWN_BUTTON_HOLD_SEC` (типово 3с) → `sudo systemctl poweroff`
- venv створюється з `--system-site-packages`, щоб бачити системний
  `python3-libgpiod` (pip-версія gpiod не завжди чисто збирається
  без системних заголовків `libgpiod-dev`). Побічний ефект: `pip
  install` під час встановлення може вивести попередження про
  конфлікт залежностей сторонніх системних пакетів (напр.
  `types-flask-migrate` вимагає `Flask-SQLAlchemy`) — це не
  стосується коду проєкту (`Flask-SQLAlchemy`/`Flask-Migrate` ніде
  не імпортуються), встановлення завершується успішно
  (`Successfully installed ...`), попередження безпечно ігнорувати
- Статус (увімкнено/пін/час утримання) віддається через `/api/config`

## /healthz та PWA

`GET /healthz` — читає `db.get_latest_metric()["ts"]`, порівнює з
`config.POLL_INTERVAL_SEC * 3`; `503` якщо watchdog не оновлював
метрики довше цього порогу (сервіс завис/впав, хоч webui й далі
відповідає). Не пише подій у журнал — придатний для частого
зовнішнього опитування.

PWA: `static/manifest.json` + `static/sw.js` (service worker) +
`static/pwa.js` (реєстрація SW, підключена в обох HTML-шаблонах).
Іконки `icon-192.png`/`icon-512.png` згенеровані з `logo.png` (вписані
в квадрат на `--sky-900` фоні). Service worker кешує лише `/static/*`
(network-first, cache fallback) — жодного кешування API-відповідей,
щоб офлайн-режим не показував застарілі дані Starlink як актуальні.

## Реальний speedtest (app/speedtest_runner.py)

`run_once()` — один прогін через бібліотеку `speedtest-cli`, ніколи
не кидає виняток (помилка в полі `error`, `success=False`).
`run_forever(stop_event)` — цикл з інтервалом `SPEEDTEST_INTERVAL_SEC`,
перевіряє `stop_event` кожні 5с сну (не чекає повний інтервал при
зупинці сервісу). Запускається як потік у `Watchdog.run_forever()`
поруч із Telegram-ботом, лише якщо `SPEEDTEST_ENABLED=1` (за
замовчуванням вимкнено — реальний трафік + навантаження WiFi-радіо).
Результати — таблиця `speedtest_results` (SQLite), очищення разом з
іншими таблицями в `prune_old()`. `POST /api/speedtest-run` виконує
одноразовий синхронний прогін на вимогу користувача (10-30с, окей
блокувати — це усвідомлена дія, не фоновий цикл).

## /stats — повна статистика

Головна сторінка показує лише 5 останніх подій журналу і коротку
summary-панель speedtest (поточні значення + кнопка запуску).
`/stats` (`templates/stats.html`, `static/stats.js`) — повний журнал
подій (`limit=500`) і повна історія speedtest-результатів, без інших
елементів дашборду. "Очистити" на обох сторінках — лише локально в
браузері (`eventsClearedLocally` в кожному JS-файлі окремо, БД не
зачіпається), той самий підхід, що вже був на головній.

## Системний WAN-failover (scripts/wan_failover_check.sh)

Окремо від eth0-fallback у Python-коді (`telegram_notify.py`, працює
лише для конкретних HTTP-запитів проєкту) — системний рівень:
`starlink-wan-failover.timer` кожні ~20с запускає
`wan_failover_check.sh`, який `ping -I wlan0` перевіряє реальний
інтернет через wlan0 і через **nmcli** (`connection modify
ipv4.route-metric` + `device reapply`) підвищує/знижує пріоритет
дефолтного маршруту wlan0 (50 ⟷ 9999) залежно від результату. Пряма
зміна через `ip route` тут не годиться — NetworkManager сам володіє
wlan0 і періодично перевідновлює власний metric з конфігурації
з'єднання, ігноруючи зовнішні зміни таблиці маршрутів в обхід себе.
Коли wlan0 демотовано, eth0 (metric 1002) автоматично стає дефолтним
для **всієї системи** (apt, curl тощо), не лише для нашого коду.

**Важливо**: `192.168.1.0/24` (router) має автоматичний kernel-scope
маршрут (це власна підмережа wlan0-інтерфейсу) — завжди пріоритетніший
за дефолтний, незалежно від metric. Але `192.168.100.0/24` (dish) **не
має** такого автоматичного маршруту — весь трафік туди йшов лише через
дефолтний маршрут, і при демотуванні wlan0 dish ставав недосяжним
(реальний виявлений баг). Виправлено: `install.sh` при першому
встановленні додає постійний `nmcli +ipv4.routes "192.168.100.0/24
$WLAN_GW"` на wlan0-з'єднанні — окремий явний маршрут, незалежний від
стану WAN-failover.

**Гістерезис**: перемикання metric стається лише після
`REQUIRED_CONSECUTIVE=3` поспіль однакових результатів ping-перевірки
(стан — `/run/starlink-wan-failover.state`, tmpfs). Без цього кожен
`nmcli device reapply` на мить розриває маршрут до dish, і сам факт
переключення впливав на результат наступної ж перевірки — петля
самопідживлення (реальний виявлений баг: часті короткі флапи dish,
підтверджено точним часовим збігом у `journalctl`).

root-сервіс — `CapabilityBoundingSet` звужує навіть root до
`CAP_NET_ADMIN`+`CAP_NET_RAW` (ping потребує raw-сокети).
`uninstall.sh` відновлює нормальний metric wlan0 через той самий
nmcli-підхід, якщо він був демотований на момент видалення.

## Watchdog для watchdog-а (scripts/watchdog_healthcheck.sh)

`Restart=always` на `starlink-monitor.service` рятує від crash, але
не від зависання (deadlock/livelock) — процес технічно живий,
systemd цього не бачить. `starlink-monitor-healthcheck.timer`
(раз/хв) опитує вже наявний `GET /healthz` (окремий процес
`starlink-webui.service`, незалежний від можливого зависання
watchdog-потоку) — якщо відповідь не `200`, примусовий `systemctl
restart starlink-monitor.service`. `OnBootSec=90` — довше за перший
цикл опитування, щоб уникнути хибного restart одразу після
завантаження ("no data yet" у `/healthz` саме по собі не є
деградацією, `ok=True`).

## Періодична оптимізація БД (db.vacuum_and_analyze)

`VACUUM` + `ANALYZE` раз на добу в `monitor.py` (окремий таймер,
рідше за `prune_old()` — щогодини). `VACUUM` фізично звільняє диску
сторінки, вивільнені після `DELETE` в `prune_old()` (SQLite сам їх не
повертає), `ANALYZE` оновлює статистику планувальника запитів. Окреме
autocommit-з'єднання (не `get_conn()` з WAL) — простіше й надійніше
для команди, що вимагає ексклюзивного доступу.

## Ротація журналу systemd

`install.sh` ідемпотентно дописує `SystemMaxUse=200M` у
`/etc/systemd/journald.conf` (лише якщо там ще немає власного
значення користувача) — без обмеження journald міг би з часом
накопичити помітний обсяг логів на SD-картці.
