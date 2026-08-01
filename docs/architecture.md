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
| `system_metrics.py` | Метрики Pi (CPU/RAM/диск/температура) + apt-оновлення (кешовано) |
| `shutdown_button.py` | Фізична кнопка виключення через GPIO (окремий процес) |
| `display.py` | Фізичний TFT-дисплей статусу (ST7789, SPI, окремий процес) |
| `gpio_utils.py` | Спільна gpiod v1/v2-сумісна логіка читання GPIO-входу (shutdown_button.py + display.py) |
| `speedtest_runner.py` | Періодичний реальний speedtest (потік у monitor.py, вимкнено за замовчуванням) |
| `config.py` | Конфігурація, env-змінні |
| `config_editor.py` | Читання/валідація/запис `/etc/starlink-monitor/env` через `/settings` |

`app/vendor/starlink_grpc.py` — **vendored** (фізично включений в архів
проєкту, не в таблиці вище, бо не наш код) файл зі стороннього
репозиторію sparky8512/starlink-grpc-tools, для відтворюваності
збірки: install.sh НЕ завантажує його динамічно з інтернету при
встановленні (раніше завантажував — `starlink-grpc-fetch.service`
через systemd при кожному першому встановленні; тепер той сервіс
встановлюється, але НЕ enabled/started автоматично, лише опційний
ручний виклик для оновлення до найновішої upstream-версії).
`starlink_client.py` імпортує через `from app.vendor import
starlink_grpc` з graceful fallback (`starlink_grpc = None` при
відсутньому файлі — код і далі коректно працює, лише
`DishStatus(online=False, error="starlink_grpc module missing")`).

## Автоматичний reboot dish/router — умови спрацювання

1. **Watchdog**: dish не відповідає N опитувань поспіль
2. **Update-ready dish**: `update_state == "REBOOT_REQUIRED"`
3. **Update-ready router**: `update_state == "REBOOT_PENDING"`

Усі три через `db.get_auto_reboot_enabled()` (runtime, перемикається
з веб-інтерфейсу без перезапуску сервісу) і захищені
`MIN_REBOOT_INTERVAL_SEC` (180с) від reboot-loop.

`Watchdog.first_failure_ts` відслідковує тривалість безперервної
недоступності dish. Якщо вона перевищує `NOTIFICATIONS_MUTE_AFTER_SEC`
(900с) — Telegram-сповіщення про watchdog auto-reboot придушуються
(`db.insert_event` не зачіпається, лише `_notify`); відновлення
зв'язку завжди повідомляється.

**Групування reboot-спаму** (`_notify_reboot()`/`_check_reboot_spam_
recovery()`) — інша ситуація, ніж `NOTIFICATIONS_MUTE_AFTER_SEC`: та
працює за ТРИВАЛІСТЮ однієї безперервної відмови, це — за ЧАСТОТОЮ
окремих коротких reboot-циклів (флап), кожен з яких минає
`MIN_REBOOT_INTERVAL_SEC` і тому НЕ приглушується першим механізмом.
`Watchdog.reboot_notify_ts` — ковзний список timestamps уже
надісланих reboot-сповіщень (не всіх спроб reboot, лише тих, що
дійшли до Telegram); коли їх назбирається `REBOOT_SPAM_THRESHOLD`+ за
`REBOOT_SPAM_WINDOW_SEC` — одне попередження про групування, далі
мовчки рахує (`muted_reboot_count`) до затишшя
(`_check_reboot_spam_recovery()`, викликається щоцикл на початку
`poll_once()`), тоді підсумок і повний скид стану. Застосовано лише
до **успішних** reboot-сповіщень (`🔁`) — невдалі (`❌`) лишаються
без групування, рідкісніші й важливіші показувати щоразу.

## systemd-сервіси

| Сервіс | Роль | Особливості безпеки |
|---|---|---|
| `starlink-monitor.service` | Watchdog + Telegram-бот (потік) | `NoNewPrivileges=true`, `AmbientCapabilities=CAP_NET_RAW` (SO_BINDTODEVICE), `CapabilityBoundingSet` звужено до цієї capability |
| `starlink-webui.service` | Flask dashboard | БЕЗ `NoNewPrivileges` (потрібен sudo для reboot/poweroff Pi), `AmbientCapabilities=CAP_NET_RAW`, `CapabilityBoundingSet` НЕ звужено (sudo systemctl reboot успадкував би обмеження), `ReadWritePaths` включає `signature_phrases.txt` |
| `starlink-shutdown-button.service` | Слухає GPIO-кнопку виключення | БЕЗ `NoNewPrivileges` (sudo poweroff), `Restart=on-failure` (не `always` — чистий вихід при вимкненій кнопці не збій) |
| `starlink-grpc-fetch.service` | Опційне ручне оновлення vendored `starlink_grpc.py` до найновішої upstream-версії — НЕ enabled/started автоматично (файл vendored, `app/vendor/`) | — |
| `starlink-wan-failover.service`/`.timer` | Періодична (кожні ~20с) перевірка інтернету через wlan0, коригування route-metric | root-сервіс; `CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW` — навіть root тут без решти системних можливостей |
| `starlink-monitor-healthcheck.service`/`.timer` | Раз/хв опитує `/healthz`, force-restart `starlink-monitor.service` при не-200 (deadlock/livelock, не crash — `Restart=always` цього не бачить) | root-сервіс (потрібен для `systemctl restart` іншого юніта); `NoNewPrivileges=true`, `ProtectSystem=strict` |
| `starlink-display.service` | Фізичний TFT-дисплей (ST7789, SPI), вимкнено за замовчуванням | `SupplementaryGroups=gpio spi`; БЕЗ `NoNewPrivileges` (кнопка виключення обробляється тут же, потребує `sudo poweroff` — реальний баг був знайдений і виправлений: старий коментар помилково лишав `NoNewPrivileges=true` вже після того, як обробку кнопки перенесли сюди) |

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
`synchronous=NORMAL` (не дефолтний `FULL`) — офіційно рекомендований
режим для WAL: `fsync()` лише при checkpoint, не на кожному commit,
суттєво менше фізичних записів на SD-картку при опитуванні кожні
~10с. Ризик — втрата лише кількох останніх транзакцій при раптовому
знеструмленні (БД не пошкоджується, це гарантує WAL сам по собі).

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
  `upsert_known_device_dish()`/`upsert_known_device_router()` повертають
  `(real_change, old_version)` — `real_change=True` лише коли версія
  реально відрізняється від РАНІШЕ ВІДОМОГО значення (не `None`, не
  перше знайомство з пристроєм/полем) — `monitor.py` надсилає Telegram
  "🔄 Прошивка тарілки/роутера оновлена: X → Y" саме на цій умові.

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

- Увімкнено за замовчуванням на `GPIO27` (`SHUTDOWN_BUTTON_GPIO_PIN=27`);
  `0` вимикає — сервіс одразу виходить з кодом 0, не помилка
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
(network-first, cache fallback) — принцип "не кешувати API-відповіді
на рівні SW" не змінився (застарілі дані без чіткого маркування були
б оманливими).

**Офлайн-fallback статусу** (`static/dashboard.js`, окремо від SW) —
інша задача: показати ОСТАННІЙ ВІДОМИЙ стан з ЯВНИМ маркуванням
застарілості, коли Pi недосяжний з телефону (не CDN/статика, а сам
локальний Pi — типовий мобільний сценарій: вийшов з WiFi-зони дії
Pi). `localStorage` (не `IndexedDB` — дані це один невеликий JSON-
об'єкт "останній latest", транзакції/великий обсяг не потрібні,
простий синхронний API достатній) зберігає `{latest, cachedAt}` при
кожному успішному `/api/status`. При невдалому fetch —
`_loadCachedStatus()` рендерить кешовані дані тим же кодом
(`_renderStatusData()`, винесений з `refreshStatus()` для
перевикористання), і банер `"⚠️ Немає зв'язку з Pi — показано
останній відомий стан (X хв тому)"` (`fmtAgo()` в `common.js` — той
самий формат, що `telegram_bot.py._fmt_ago()`). Це вирішує занепокоєння
з попереднього абзацу explicit маркуванням, не суперечить йому.

## Темна/світла тема

CSS custom properties в `static/style.css` — та сама семантика
змінних (`--sky-900`, `--text-hi` тощо) для обох тем, `[data-theme=
"light"]`-селектор інвертує значення. Знайдено й виправлено **7
хардкоджених hex-кольорів** поза `:root`-блоками під час впровадження
(градієнт `body`, warn/crit border, hover/active стани кнопок) — без
цього light-тема застосувалась би лише частково.

Застосування — inline `<script>` в `<head>` кожного шаблону, читає
`localStorage['theme']` **синхронно, до завантаження `style.css`**
(без цього — помітний "спалах" неправильної теми при кожному
завантаженні сторінки, FOUC). Критично важливий порядок у `<head>`:
скрипт має стояти **після** `<meta name="theme-color">` (інакше
`document.querySelector('meta[name="theme-color"]')` повертає `null`
— цей самий баг був знайдений і виправлений під час реалізації,
`try/catch` рятував від краху сторінки, але синхронне оновлення
статус-бару мобільного браузера просто не спрацьовувало б) і **до**
`<link rel="stylesheet">` (щоб усе ще запобігати FOUC для CSS).

`static/theme.js` (окремий файл, не в `common.js` — той не
підключений на `/settings`, а перемикач саме там) — `applyTheme()`,
викликається з `toggle`-обробника на `/settings` і оновлює й
`data-theme` атрибут, і `<meta theme-color>` (узгоджений колір
статус-бару мобільного браузера з фактичним фоном сторінки), і
`localStorage`.

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

**Графіки трендів на /stats** (throughput, ping/drop%, obstruction) —
`canvas`, `drawLineChart()` в `static/common.js` (спільна з
`throughputChart` на головній сторінці — див. нижче), БЕЗ сторонньої
бібліотеки (Chart.js тощо): дашборд про мережу має лишатись робочим
навіть без інтернету, коли dish саме offline (CDN-залежність тоді не
завантажилась б). Дані —
`/api/metrics-chart?hours=N` → `db.get_metrics_chart_data()`: SQL
`GROUP BY` bucket, `UNION ALL` **обох** таблиць — `metrics` (raw,
недавні ≤`DOWNSAMPLE_AFTER_DAYS`) і `metrics_downsampled` (старіші,
вже 5-хвилинні середні). Обидві частини фільтруються тим самим
`cutoff = now - period`, тому запиту не потрібно явно знати поріг
downsample — де `downsample_old_metrics()` перенесла дані, там і
буде читання з `metrics_downsampled`, решта — з raw `metrics`,
безшовно (перевірено живим тестом: кількість точок графіка і
відсутність прогалин на межі cutoff не змінюються до/після
downsampling). bucket-розмір масштабується залежно від періоду
(`period_sec / 150`), тому 24г і 30д запити повертають приблизно
однакову кількість точок, довший період не важчий для фронтенду.
Кожна серія на графіку масштабується під свій власний min/max
незалежно (без потреби dual-axis для різних одиниць вимірювання).

**`throughputChart` на головній сторінці** (`/`, `static/dashboard.js`
`refreshHistory()`) — той самий `drawLineChart()`, дані з
`/api/history?limit=120` (raw, не downsampled — короткий recency-
орієнтований графік, не потребує aggregation). Раніше — Chart.js
через CDN (`cdnjs.cloudflare.com`), що прямо суперечило принципу
"без CDN" з попереднього абзацу: якщо CDN недоступний (типовий
offline-сценарій, який офлайн-fallback вище саме покращує), цей
графік на головній не працював би взагалі. Замінено на власний
canvas для послідовної офлайн-надійності по всьому дашборду.
Легенда (кольорові мітки Downlink/Uplink) — проста текстова розмітка
в HTML (`.chart-legend`), Chart.js мав вбудовану, наш `drawLineChart()`
її не малює.

`downsample_old_metrics()` (щогодини, разом з `prune_old()`) агрегує
raw-рядки старші за `DOWNSAMPLE_AFTER_DAYS` (дефолт 3) у
`DOWNSAMPLE_BUCKET_SEC`-секундні (дефолт 300 = 5хв) середні,
видаляючи оригінальні детальні рядки. `PRIMARY KEY(bucket_ts)` +
`ON CONFLICT DO NOTHING` — ідемпотентність про всяк випадок (захист
не мав би бути потрібним, бо raw-рядки видаляються одразу після
агрегації в тій самій транзакції, але DELETE тут незворотний, тому
зайвий запобіжник виправданий). `prune_old()` очищує
`metrics_downsampled` за повною `HISTORY_RETENTION_DAYS`-межею
аналогічно до `metrics`.

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
(стан — `/run/starlink-wan-failover/state`, tmpfs, у межах
`RuntimeDirectory=` — потрібне для запису під `ProtectSystem=strict`).
Без цього кожен
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

## Фізичний TFT-дисплей (app/display.py)

Окремий процес (той самий патерн, що `shutdown_button.py`) —
`run_forever()` виходить миттєво, якщо `DISPLAY_ENABLED=0`. Малює
кадр через Pillow (`Image`/`ImageDraw`) кожні `DISPLAY_REFRESH_SEC`,
надсилає через `display.image(image)` — бібліотека **Adafruit
CircuitPython ST7789** (`adafruit-circuitpython-rgb-display` +
`adafruit-blinka`). `_status_lines()` повертає структурований список
`{"kind": ..., "text": ...}` (+ `"progress"` для `kind="update"`) —
не плаский список рядків за позицією, бо рядок оновлення опційний
(був би крихкий зсув індексів прошивок залежно від його наявності).
`_fmt_uptime()` — чиста функція форматування, незалежна від дисплея.

`_redraw()` малює за `kind`: `"status"` (статус+uptime **в одному
рядку**, `font_status`=18px - зменшено з 20px, щоб uptime понад добу
(наприклад "100г 30хв") теж вміщався без обрізання, lime/red),
`"update"` (`font_update`=15px,
+ прогрес-бар `draw.rectangle()` одразу під текстом, ширина
пропорційна `progress`, clamped у `[0,100]`) — **окремий рядок для
тарілки і для роутера** (`"Оновл.Т:"`/`"Оновл.Р:"`, компактні мітки,
кожен опційний незалежно). `DISH_UPDATE_STATE_LABELS`/
`ROUTER_UPDATE_STATE_LABELS` — короткі українські переклади
internal-кодів API (`FETCHING`→`"завантаження"` тощо), **не ті самі**,
що в `static/dashboard.js` (веб-переклади розраховані на широкий
екран і довші за оригінальні коди, тут — максимально стисло; усі
FAILED/EXHAUSTED-варіанти роутера об'єднано в одне слово `"помилка"`).
`DOWNLOADING_UPDATE_IMAGE_FAILED` для роутера свідомо приховується
(як і на веб-дашборді — частина нормального циклу перевірки, не
справжня помилка). `"firmware"`
(`font_tiny`=16px, повні мітки "Тарілка"/"Роутер" — влазять
завдяки landscape canvas, 320px завширшки при `ROTATION=90/270`,
не 170px портретної ширини). `_truncate_to_width()` обрізає з `…`
будь-який рядок, що не влазить у ширину екрана (виміряно через
`draw.textlength()`) — гарантовано коректно для довільного вмісту,
без здогадок про формат версії. Прошивка роутера читається окремим
`db.get_router_status()` (не з `get_latest_metric()`, який містить
лише dish-дані).

Ініціалізація через `board`/`digitalio`/`busio` (Blinka): SPI
(`board.SCK`/`MOSI`/`MISO`) завжди апаратний; DC/RST/CS/BL — окремі
`digitalio.DigitalInOut(getattr(board, f"D{pin}"))` об'єкти. **CS —
bit-banged GPIO** (`DISPLAY_SPI_CS_PIN`, дефолт 8), не апаратний
CE0/CE1 — бібліотека сама перемикає його програмно навколо кожної
SPI-транзакції.

Ключові особливості цієї бібліотеки:
- `DisplaySPI.__init__` сам коректно виводить RST з reset-стану
  (`self.rst.switch_to_output(value=0); self.reset()`) ДО виклику
  `init()` (SPI-команди);
- `rotation` дозволяє `0/90/180/270` для будь-якого aspect ratio —
  обертання застосовується програмно через `img.rotate(rotation,
  expand=True)` на самому PIL-зображенні, не через MADCTL-регістр;
- **немає** вбудованого керування підсвіткою (`set_backlight()`) —
  BL-пін керується напряму окремим `digitalio.DigitalInOut` і
  функцією `_set_backlight()` у цьому модулі.

**Важливо для `rotation=90/270`**: метод `image()` перевіряє розмір
зображення **після** `rotate()` проти `display.width`/`display.height`
(які самі не змінюються параметром `rotation`). `_redraw()` тому
створює полотно з **транспонованими** розмірами
(`(display.height, display.width)`) саме для `90`/`270` — після
внутрішнього `rotate()` бібліотеки воно повертається до
`display.width × display.height` і коректно проходить перевірку.
Без цього транспонування виникав би `ValueError` для прямокутного
(не квадратного) дисплея. `OFFSET_LEFT`/`OFFSET_TOP` при цьому
**не** потребують перерахунку — вони діють на рівні фізичної
GRAM-адресації (`_X_START`/`_Y_START` у `_block()`), застосовуються
вже після повороту зображення, тому лишаються чинними незалежно від
`rotation`.

Паспортна роздільна здатність модуля (SKU MSP1901) — 170×320, driver
ST7789, portrait — підтверджена офіційною документацією LCDWIKI і
дефолти проєкту відповідають їй (`WIDTH=170`, `HEIGHT=320`). Піни
DC/RST/BL (дефолти 25/24/18) все одно залежать від фактичного
підключення до Pi — редагуються через `/settings`. За специфікацією
виробника, `BLK` вмикається HIGH-рівнем; якщо підсвітку підключено
напряму до 3.3V (не через GPIO Pi) — постав `STARLINK_DISPLAY_BL_PIN=0`
(не керувати програмно, `_set_backlight()` тоді нічого не робить).
Потребує `dtparam=spi=on` (вимкнено за замовчуванням на Raspberry Pi
OS) — `install.sh` попереджає наприкінці встановлення, якщо SPI ще
не увімкнено. `RUN_USER` додається в групи `gpio`+`spi` для доступу
без sudo (`SupplementaryGroups` в unit-файлі).

`DISPLAY_OFFSET_LEFT` (дефолт **35**, `DISPLAY_OFFSET_TOP` дефолт 0) —
передаються напряму в конструктор як `x_offset`/`y_offset`. Значення
35-50 (принаймні) емпірично підтверджені як робочі на реальному Pi
для SKU MSP1901 — див. запис у `docs/decisions-log.md` про
діагностику кольорового шуму (спершу помилково прийнятого за
апаратний дефект).

`DISPLAY_SPI_SPEED_HZ` (дефолт 40МГц, передається як `baudrate`) —
свідомо консервативний для типового підключення джампер-дротами;
піднімай через `/settings`, якщо монтаж якісний (короткі дроти/шлейф).

**Кнопка** — та сама, що й `SHUTDOWN_BUTTON_GPIO_PIN`/`SHUTDOWN_BUTTON_HOLD_SEC`
(не окремий пін): коротке натискання перемикає підсвітку, довге
(як завжди) вимикає Pi через `_trigger_shutdown()`, імпортовану напряму
з `shutdown_button.py` (уникає дублювання логіки запису події/Telegram/
`sudo systemctl poweroff`). Обробляється в цьому самому циклі
`run_forever()`, не окремим сервісом — керування BL-піном можливе
лише з того самого процесу, що тримає `digitalio`-об'єкт; окремий
процес конкурував би за той самий GPIO.
`shutdown_button.py` сам себе вимикає (`watch_button()` виходить
одразу), коли `DISPLAY_ENABLED=1`, щоб не тримати запит на той самий
GPIO-пін одночасно з `display.py`.

`gpio_utils.ButtonPressTracker` — спільна, чиста (без залежності від
реального GPIO) стейтфул-структура детекції короткого/довгого
натискання (`poll(value)` → `"short_press"`/`"long_press"`/`None`),
використовується і тут, і в `shutdown_button.py` (там лише
`long_press`). Основний цикл `run_forever()` опитує кнопку кожні
`BUTTON_POLL_INTERVAL_SEC` (0.1с, для швидкого відгуку), а
перемальовує екран лише раз на `DISPLAY_REFRESH_SEC` — відстежується
окремим таймером `last_redraw`, не блокуючим сном.

`app/gpio_utils.py` — спільна gpiod v1/v2-сумісна логіка читання
GPIO-входу, винесена з `shutdown_button.py` (той самий патерн
потрібен і для дисплея) - `open_input_line(pin, consumer)`
повертає `(get_value, release)`.

**Автовимкнення підсвітки** (`DISPLAY_BACKLIGHT_AUTO_OFF_SEC`, дефолт
60с, 0=вимкнено): `_should_auto_off()` — чиста функція, перевіряється
щоцикл основного циклу (0.1с). `last_activity_ts` оновлюється при
кожному ввімкненні підсвітки (старт сервісу і кожен short_press, що
вмикає) — авто-off рахує час саме від моменту ввімкнення, не від
загальної бездіяльності системи.
