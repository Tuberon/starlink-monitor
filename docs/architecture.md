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
| `telegram_notify.py` | Вихідні сповіщення |
| `telegram_bot.py` | Вхідні команди `/status`, `/reboot`, `/help` (обробка кожного update у пулі потоків, не блокує polling) |
| `labels.py` | Спільні label-мапи (monitor.py + telegram_bot.py, без дублювання) |
| `system_metrics.py` | Метрики Pi (CPU/RAM/диск/температура) |
| `shutdown_button.py` | Фізична кнопка виключення через GPIO (окремий процес) |
| `pi_power.py` | Спільний reboot/poweroff для webapp.py/shutdown_button.py, DB-сигнал для повідомлення на дисплеї |
| `activity_led.py` | Опційний LED активності SD-картки - блимає при кожному commit у БД (не окремий процес, частина monitor.py) |
| `display.py` | Фізичний TFT-дисплей статусу (ST7789, SPI, окремий процес) |
| `gpio_utils.py` | Спільна gpiod v1/v2-сумісна логіка читання GPIO-входу (shutdown_button.py + display.py) і запису GPIO-виходу (activity_led.py) |
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
зв'язку повідомляється (`✅ Dish знову online...`), якщо
`NOTIFY_DISH_RECOVERY=1` (дефолт, `0` вимикає — journal-лог
(`logger.info`) і далі пишеться незалежно від цього параметра,
лише Telegram-сповіщення пригнічується; знайдено запитом
користувача — короткі flap-відновлення не завжди інформативні).

**Групування reboot-спаму** (`_notify_reboot()`/`_check_reboot_spam_
recovery()`) — інша ситуація, ніж `NOTIFICATIONS_MUTE_AFTER_SEC`: та
працює за ТРИВАЛІСТЮ однієї безперервної відмови, це — за ЧАСТОТОЮ
окремих коротких reboot-циклів (флап), кожен з яких минає
`MIN_REBOOT_INTERVAL_SEC` і тому НЕ приглушується першим механізмом.
`Watchdog.reboot_notify_ts` — ковзний список timestamps уже
надісланих reboot-сповіщень (не всіх спроб reboot, лише тих, що
дійшли до Telegram); коли їх назбирається `REBOOT_SPAM_THRESHOLD`+ за
`REBOOT_SPAM_WINDOW_SEC` — подальші мовчки рахуються (`muted_reboot_
count`), **без окремого повідомлення про початок групування** (щоб
не додавати ще одне сповіщення до вже частих — навмисно прибрано за
запитом користувача), до затишшя (`_check_reboot_spam_recovery()`,
викликається щоцикл на початку `poll_once()`), тоді підсумок і
повний скид стану. Застосовано лише до **успішних** reboot-
сповіщень (`🔁`) — невдалі (`❌`) лишаються без групування, рідкісніші
й важливіші показувати щоразу.

## systemd-сервіси

| Сервіс | Роль | Особливості безпеки |
|---|---|---|
| `starlink-monitor.service` | Watchdog + Telegram-бот (потік) | `NoNewPrivileges=true`, `AmbientCapabilities=CAP_NET_RAW` (SO_BINDTODEVICE), `CapabilityBoundingSet` звужено до цієї capability |
| `starlink-webui.service` | Flask dashboard | БЕЗ `NoNewPrivileges` (потрібен sudo для reboot/poweroff Pi), `AmbientCapabilities=CAP_NET_RAW`, `CapabilityBoundingSet` НЕ звужено (sudo systemctl reboot успадкував би обмеження) |
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
  повністю видаляє й перевстановлює. Наприкінці, **лише в install-режимі** —
  опційний інтерактивний блок налаштування статичних IP для eth0/wlan0
  (з підтвердженням, дефолти редаговані), вимикає конфліктуючий `dhcpcd`.

  **Зниження системного навантаження** (теж лише install-режим, після
  мережевого блоку) — інтерактивна пропозиція `systemctl disable --now`
  для короткого, добре перевіреного списку служб (`bluetooth`,
  `hciuart`, `avahi-daemon`(+`.socket`), `triggerhappy`, `ModemManager`).
  Кожен кандидат перевіряється **індивідуально** через `systemctl
  list-unit-files`+`is-enabled`/`is-active` **перед** пропозицією — не
  питає про службу, якої немає в системі (мінімальні образи вже можуть
  бракувати частини списку). **Вимикає, не видаляє** пакети — уникає
  ризику зламаних залежностей через `apt purge`, легко відновлюється
  через `systemctl enable --now`. Явне попередження про `avahi-daemon`
  (`.local`-mDNS-резолюція), якщо він серед виявлених кандидатів —
  вимкнення могло б забрати доступ через `raspberrypi.local` для тих,
  хто підключається так, не по IP. Мережеві служби (`ssh`,
  `NetworkManager`, `wpa_supplicant`, `dhcpcd`) і core systemd-юніти
  **ніколи не входять у список кандидатів** — на headless-пристрої без
  фізичного доступу заблокований SSH означає повну втрату контролю.
  Окремий, незалежний крок (той самий блок) — опційний `apt-get
  autoremove`/`clean` (дисковий простір, не RAM/CPU).
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

- `metrics` — історія опитувань dish (latency, dish_id, update_state, ...)
- `events` — журнал подій (reboot, зміни стану, попередження, підключення
  нової тарілки). Повтори того самого `kind`+`message` підряд стискаються
  в один рядок (`count` замість нового запису щоразу)
- `system_metrics` — CPU/RAM/диск/температура Pi
- `router_status` — останній відомий стан роутера, включно зі списком
  під'єднаних WiFi-клієнтів (`clients`: ім'я/MAC, IP, діапазон, сигнал,
  час у мережі)
- `settings` — runtime key-value (auto_reboot_enabled, telegram config,
  known_dish_ids, dish_target_version/router_target_version + їхні
  `*_notified` пари — див. нижче)

**Write-only колонки, залишені в схемі без запису** (аудит показав, що
ніде не читаються - `metrics.currently_obstructed` дублює `obstruction_
fraction`, `router_status.bootcount` ніколи не відображався, `events.
last_ts` дублював `ts` в одному й тому самому UPDATE): значення `NULL`
для нових рядків, старі рядки не чіпались. Схема НЕ змінена (без `DROP
COLUMN`) - менший ризик для вже існуючих БД на реальних пристроях, ніж
міграція.
- `known_devices` — по одному рядку на dish_id: версії ПЗ dish/router
  і час останньої зміни кожної. Джерело для `/id <dish_id>` у Telegram-боті.
  `upsert_known_device_dish()`/`upsert_known_device_router()` повертають
  `(real_change, old_version)` — `real_change=True` лише коли версія
  реально відрізняється від РАНІШЕ ВІДОМОГО значення (не `None`, не
  перше знайомство з пристроєм/полем), і завжди оновлюють `known_
  devices` реальною поточною версією незалежно від напрямку зміни.
  `monitor.upsert_dish_and_notify()`/`upsert_router_and_notify()`
  сповіщають лише про реальне ОНОВЛЕННЯ (`_format_firmware_change_
  message()`, використовує `db.is_older_version()` — той самий
  толерантний компаратор, що для target-версій): "🔄 Прошивка X
  оновлена: X → Y" для forward-змін. Якщо нова версія СТАРІША за
  попередню (SpaceX інколи відкочує прошивку глобально) - навмисно
  НЕ сповіщає (`_format_firmware_change_message()` повертає `None`,
  обидва виклики перевіряють на `None` перед `notify_fn(...)`) - за
  запитом користувача, `known_devices` все одно оновлюється мовчки.
  Спільний для dish і router (та сама функція).

`Watchdog._log_alerts_change()` журналює появу/зникнення кожного dish-
alert'а окремою подією в БД (`dish_alert`/`dish_alert_resolved`).
Виняток: `obstruction_map_reset` при ЗНИКНЕННІ (`resolved`) навмисно
пропускається повністю — Starlink періодично скидає карту перешкод
сам по собі як частину нормальної роботи (не аварійна подія), тому
"Попередження знято: карта перешкод скинута" не журналюється взагалі.
Точковий фільтр лише для цього одного alert'а й лише для `resolved`-
напрямку — при появі (`appeared`) той самий alert усе ще журналюється
й потрапляє в Telegram (не в `MUTED_DISH_ALERTS`), лише завершення
цього конкретного, нормального циклу не варте окремого запису.

**"🟢 Dish Watch запущено (Raspberry Pi перезавантажено)"** —
`monitor.pi_just_booted()` (чиста, тестована функція, порівнює
`psutil.boot_time()` з `time.time()`, поріг 120с) відрізняє РЕАЛЬНЕ
завантаження Pi від звичайного `sudo systemctl restart starlink-
monitor.service` (напр. під час `update.sh`) — без цього сповіщало б
набагато частіше, ніж "Pi увімкнувся/перезавантажився". Викликається
на початку `run_forever()`, одразу після `db.init_db()`, ПЕРЕД
`telegram_bot.start()` (не залежить від нього — `telegram_notify.
send_message()` окремий, самостійний модуль). `NOTIFY_PI_STARTUP=0`
(дефолт `1`) вимикає повністю.

## Буферизація dish-метрик (SD-card-wear reduction)

`Watchdog.metrics_buffer` — dish-зчитування (кожні `POLL_INTERVAL_SEC`,
типово 10с) накопичуються в пам'яті замість негайного окремого
запису. `flush_metrics_buffer()` викликає `db.insert_metrics_batch()`
(одна транзакція, `executemany`) раз на `DISH_METRICS_BATCH_INTERVAL_
SEC` (типово 30с) — зменшує кількість фізичних write-транзакцій на
SD-картку **в рази**, без втрати жодної точки даних (усі зчитування
все одно потрапляють у БД, лише трохи пізніше).

**Graceful shutdown** — без цього звичайний `sudo systemctl restart`
(наприклад, під час `update.sh`) втрачав би до `DISH_METRICS_BATCH_
INTERVAL_SEC` буферизованих даних щоразу, не лише при справжньому
раптовому вимкненні живлення (для якого ця втрата — свідомо прийнятий
компроміс, не помилка). `run_forever()` реєструє обробники `SIGTERM`/
`SIGINT`, які викликають `flush_metrics_buffer()` перед виходом
(`raise SystemExit(0)`).

**`/healthz`** — поріг свіжості (`webapp.py healthz()`) враховує
можливу batch-затримку: `max_age_sec = POLL_INTERVAL_SEC * 3 +
DISH_METRICS_BATCH_INTERVAL_SEC` — знайдено ДО реалізації буферизації
(не постфактум): без цього додавання сама буферизація хибно
спрацьовувала б як "watchdog завис", оскільки старий поріг (30с)
точно збігався з новим batch-інтервалом.

**Другорядний ефект, свідомо прийнятий**: `check_both_targets_
reached()` читає `db.get_latest_metric()` для комбінованого "🎉
обидва" сповіщення — це значення може відставати на до `DISH_
METRICS_BATCH_INTERVAL_SEC`. Головне, per-component сповіщення
(`check_target_version_reached()` через `upsert_dish_and_notify()`)
використовує live `DishStatus`-об'єкт напряму, не БД — працює без
жодної затримки.

**`db._metric_row_params()`** — спільний helper для формування
tuple параметрів INSERT, використовується і `insert_metric()`
(один рядок, ручна перевірка через `check_updates_now()` — навмисно
НЕ буферизується, користувач очікує негайного запису й відповіді),
і `insert_metrics_batch()` (`executemany` для кількох рядків) —
уникає дублювання SQL-запиту.

**`SYSTEM_METRICS_INTERVAL_SEC`** (типово 60с) — CPU/температура/
пам'ять Pi змінюються повільно, записувати їх із тією самою
частотою, що критичні dish-метрики (10с), зайве навантаження без
практичної користі. Окремий таймер у `run_forever()`, той самий
паттерн, що вже для router нижче.

## Надійність: integrity-check, автоматичний backup, Telegram-retry

**`db.check_integrity()`** (`PRAGMA quick_check`) — той самий
щоденний цикл, що `vacuum_and_analyze()`. Ловить `sqlite3.
DatabaseError` окремо (файл ВЗАГАЛІ не SQLite, не лише пошкоджені
дані всередині — `PRAGMA quick_check` сам кидає виняток у цьому
крайньому випадку, знайдено живим тестом). `monitor.check_db_
integrity_and_notify()` при пошкодженні — Telegram-сповіщення +
спроба аварійного `perform_auto_backup()` (в `try/except` — backup
теж може провалитись, читаючи з тієї самої пошкодженої БД).

**`monitor.build_backup_dict()`** — спільна для `webapp.py api_
settings_backup()` (ручний, веб-кнопка) і `perform_auto_backup()`
(автоматичний, періодичний з `run_forever()`) — уникає дублювання.
`db.BACKUP_FORMAT_VERSION` (не `webapp.py`) — доступна з `monitor.py`
без циклічного імпорту (`webapp.py` вже імпортує `monitor`).
`perform_auto_backup()` пише JSON у `AUTO_BACKUP_DIR` (дефолт поруч
з `DB_PATH`, обчислюваний — тому поза UI-редактором, той самий клас,
що `DB_PATH`), ротація залишає `AUTO_BACKUP_KEEP_COUNT` (дефолт 4)
найновіших файлів.

**Telegram send-retry** (`telegram_notify.send_message()`) — до
`TELEGRAM_SEND_RETRIES` (дефолт 1) повторних спроб з `TELEGRAM_SEND_
RETRY_DELAY_SEC` (дефолт 2с) затримкою, ЛИШЕ для `requests.
RequestException` (мережеві помилки). `for...else` на рівні спроб:
`break` одразу після отриманої HTTP-відповіді (успіх чи ні - повтор
на HTTP-рівня помилку типу "chat not found" нічого не змінить),
`else`-гілка (виконується, лише коли цикл завершився БЕЗ `break`)
записує помилку, лише якщо ВСІ спроби дали мережевий збій.

## Сповіщення про встановлення очікуваної версії

Окремо від "🔄 прошивка змінилась" (будь-яка зміна) — `/settings`
дозволяє вказати ОЧІКУВАНУ версію, чи кілька через кому (той самий
формат, що `telegram_chat_ids`) — корисно, коли SpaceX випускає різні
номери версій для різних апаратних ревізій під однією умовною
версією. Матч на будь-яку з перелічених версій дає "✅ Останнє
оновлення встановлено". `db.parse_version_list()` — спільний helper,
`monitor.check_target_version_reached()` — module-level функція (не
метод класу).

**Дедублікація**: `dish_target_notified` зберігає трійку (`dish_id` +
версія + повний target-список) — природно скидається при зміні
списку. `dish_id` у ключі — якщо один Pi моніторить різні фізичні
Starlink з часом (заміна обладнання), дедублікація без `dish_id`
блокувала б сповіщення для нового пристрою при збігу з версією
попереднього.

`/api/target-versions` (GET/POST) — CRUD, GET повертає `dish_current`/
`router_current` для першочергового введення в UI. POST приймає новий
список лише якщо КОЖЕН кандидат не старіший за вже відому версію
(`db.is_older_version()`/`version_key()` — толерантний компаратор для
`YYYY.MM.DD.mrXXXXX.N`, дата-префікс домінує). Якщо хоч один
кандидат старіший — відхиляється весь список для поля.

**`db.version_channel()`** — витягує буквений префікс build-сегмента
(`mr`/`cr`). Різні апаратні ревізії можуть отримувати оновлення з
незалежних build-каналів одночасно з різними датами — пряме
порівняння дат між каналами хибно відхиляло валідні кандидати.
`_find_older_candidates()` порівнює candidate лише проти baseline
того самого каналу; без явного каналу — fallback на глобальне
порівняння.

**Rollback-узгодження**: якщо dish/router реально відкотився
(SpaceX-side) після введення target для новішої версії, candidate,
що точно збігається з фактично встановленою версією, завжди
дозволяється незалежно від старішого попереднього target — захист
від описок лишається непорушним.

Часткова відмова між полями (dish vs router) — валідне поле
зберігається, невалідне відхиляється окремо з поясненням. Порожній
рядок — команда очистити поле. Відповідь API завжди містить
`message` з реальним результатом.

`monitor.check_both_targets_reached()` — комбіноване "🎉" при
одночасному збігу обох компонентів, доповнює per-component
сповіщення. Викликається з обох циклів опитування (dish/router
незалежні). Не входить у backup/restore — internal dedup-стан, не
user-налаштування.

**Module-level функції, спільні для watchdog і ручної кнопки**:
`version_in_target_list()`, `check_target_version_reached()`,
`check_both_targets_reached()`, `upsert_dish_and_notify()`,
`upsert_router_and_notify()`, `check_updates_now()` — усі поза класом
`Watchdog`, приймають `notify_fn` замість `self._notify`. Раніше
ручна кнопка "Перевірити оновлення" записувала статус без жодного
сповіщення — виправлено винесенням спільної логіки.

**`IGNORED_ROUTER_ALERTS`** (`wired_mesh_not_using_wan_iface`) —
раніше class-attribute, застосовувався лише у фоновому watchdog-
циклі; ручна перевірка записувала router-статус без фільтра,
"прибраний" alert повертався щоразу при ручному натисканні. Тепер
module-level константа, застосовується в обох місцях запису.

**`telegram_bot._api_call()`** — раніше перевіряла лише мережеві
помилки, не `ok`-поле Telegram-відповіді (відхилені запити, напр.
перевищення ліміту 4096 символів, проходили непоміченими — "команда
не відповідає"). Тепер `ok` перевіряється явно. `/id` без аргументу
обмежена `TELEGRAM_ID_LIST_MAX_ITEMS` (найвразливіша до ліміту
команда), з "…і ще N" поясненням при обрізанні.

Усі таблиці мають автоматичну міграцію колонок при `init_db()`.

## Backup/restore налаштувань

`GET /api/settings-backup` віддає JSON (Telegram bot token, chat_ids,
enabled, auto_reboot_enabled,
dish/router_target_version, історія відомих Starlink-пристроїв
(`known_devices` — dish_id, версії ПЗ/апаратні, часові мітки),
`env_params` — лише перевизначені параметри app/config.py) —
завантажується браузером як файл. `POST /api/settings-restore`
приймає той самий формат і застосовує лише відомі поля (env_params
через `config_editor.save_values()`, застосовується після рестарту
сервісів). `known_devices` через `db.merge_known_devices()` — НЕ
перезаписує dish_id, що вже є в цільовій БД (INSERT OR IGNORE):
локальна БД реально працюючого watchdog'а вважається авторитетнішою
за статичний backup-знімок з невідомо якого моменту в минулому.
Bot token у файлі — у відкритому вигляді, файл backup потрібно
берегти як secret.

## Періодична відправка backup у Telegram (app/telegram_notify.py:send_document)

`send_document()` — та сама retry/eth0-fallback інфраструктура, що
`send_message()` (`_request_with_eth0_fallback()` з `**kwargs`,
підтримує `files=` без змін), але через Telegram Bot API's
`sendDocument`-endpoint (multipart/form-data, не JSON). Файл
відкривається **заново** для кожного chat_id окремо (`with open(...)
as f` усередині циклу) — file handle споживається один раз при
завантаженні, повторне використання того самого відкритого файлового
об'єкта для другого запиту дало б порожнє тіло другому отримувачу
(перевірено живим тестом: обидва chat_id реально отримують файл
повного розміру).

`Watchdog._maybe_send_backup_to_telegram()` (`monitor.py`) — окремий,
незалежний таймер від `AUTO_BACKUP_INTERVAL_SEC` (створення файлу) —
`TELEGRAM_BACKUP_INTERVAL_HOURS` (типово тиждень). Знаходить
**найновіший** (`max(..., key=os.path.getmtime)`) `.json`-файл у
`AUTO_BACKUP_DIR`, надсилає його. Таймер (`last_telegram_backup_
sent_ts`) ініціалізується `time.time()` у `__init__()` (не `0.0`) —
інакше кожен рестарт сервісу негайно надсилав би файл, незалежно від
реально минулого інтервалу. Таймер оновлюється **безумовно, до самої
спроби відправки** — провал (напр. Telegram тимчасово недоступний) не
має спричиняти повторні спроби щоцикл опитування (~10с), а чекати
повного інтервалу.

Викликається в `run_forever()` окремим `try/except`-блоком (не
всередині `if AUTO_BACKUP_ENABLED and ...`-блоку створення backup) —
метод сам обробляє власні помилки й оновлює власний таймер
безумовно всередині себе, зовнішній `try/except` тут — лише
додатковий запобіжник від зовсім неочікуваних винятків.

**`monitor.send_latest_backup_to_telegram()`** — спільна логіка
"знайти найновіший backup + надіслати" винесена в окрему функцію
рівня модуля (рефакторинг після додавання ручної кнопки) - і
`Watchdog._maybe_send_backup_to_telegram()` (періодичний виклик, з
власною перевіркою `TELEGRAM_BACKUP_ENABLED`/інтервалу навколо), і
`webapp.py:/api/send-backup-telegram` (ручна кнопка "📨 Надіслати
backup у Telegram" на `/settings`) викликають цю саму функцію -
жодного дублювання логіки пошуку найновішого файлу чи запису події.
Ручний endpoint **навмисно ігнорує** `TELEGRAM_BACKUP_ENABLED` (той
параметр стосується лише автоматичного розкладу) - користувач явно
натиснув кнопку, очікує негайну дію незалежно від periодичних
налаштувань. Розрізняє два різні "немає що надсилати" сценарії з
окремими повідомленнями для UI: каталог `AUTO_BACKUP_DIR` ще не
існує (жодного backup ще не було зроблено) vs каталог існує, але
порожній (усі backup видалені ротацією чи вручну).

## Категоризація параметрів на /settings (app/config_editor.py)

`EDITABLE_PARAMS` (53 записи) — кожен має `category`
(`monitoring`/`reliability`/`telegram`/`gpio`), `CATEGORY_LABELS`
дає людський підпис для кожної. `/api/env-config` повертає обидва
разом (`{"params": [...], "category_labels": {...}}`).

2 реальні `STARLINK_`-env-змінні, які `config.py` читає, НАВМИСНО
відсутні з `EDITABLE_PARAMS` (задокументовано коментарями поруч із
кожним визначенням у `config.py`): `DB_PATH` (зміна шляху до БД
через веб-UI, поки сервіс уже читає/пише в стару, вимагала б ручної
міграції даних без явного попередження) і `WEBUI_HOST` (self-lockout
ризик — зміна адреси прослуховування через сам веб-інтерфейс могла б
відрізати користувача від `/settings` з іншого пристрою в мережі).
Обидва тести (`test_all_config_env_vars_are_in_settings_except_
documented_exceptions`, `test_intentionally_excluded_settings_are_
still_read_by_config`) перевіряють це у обидва боки — жоден новий
параметр не забутий у `/settings` без задокументованої причини, і
ці 2 винятки лишаються реально читаними `config.py`, не випадково
видаленими взагалі.
`static/settings.js:loadEnvConfig()` групує параметри в `Map` за
`category` (зберігаючи порядок першої появи — той самий, що в
`EDITABLE_PARAMS`, не алфавітний), малює `<h3 class="env-category-
head">` перед кожною групою.

**Мотивація** (запит користувача, після власного зауваження в
аудиті про 59+ параметрів у плоскому списку): плаский список без
структури важко сканувати оком, шукаючи конкретний параметр серед
50+. Групування — суто UI-зміна, не впливає на `save_values()`/
валідацію/env-файл (`category` — метадані для рендерингу, не
частина логіки збереження).

Два тести (`test_every_param_has_valid_category`, `test_no_orphan_
category_labels`) перевіряють узгодженість у **обидва боки**: кожен
параметр має валідну категорію, і кожна категорія реально
використовується хоч одним параметром (не залишиться підпис для
вже спорожнілої групи).

## Керування Raspberry Pi (веб)

`pi_power.execute_pi_power_action()` (`app/pi_power.py`, спільний
модуль для веб-дашборду, фізичної кнопки й `display.py` — див.
"Повідомлення на дисплеї при reboot/poweroff" нижче) — для
`/api/system-reboot`/`/api/system-shutdown`: виконати команду через
`run_system_command()` (обмежений sudo, `/etc/sudoers.d/starlink-
monitor` — навмисно вузько, конкретні команди, не blanket `ALL=(ALL)
NOPASSWD: ALL`), записати подію в журнал, надіслати Telegram-
сповіщення про результат.

## Фізична кнопка виключення (GPIO)

Окремий процес (`app/shutdown_button.py`, сервіс
`starlink-shutdown-button.service`), не інтегрований у `monitor.py`
чи `webapp.py` — свідома ізоляція, бо GPIO-доступ вимагає групу
`gpio` і `python3-libgpiod` (системний пакет, не pip), а не всі
установки мають фізичну кнопку.

## Повідомлення на дисплеї при reboot/poweroff (app/pi_power.py)

Спільний модуль для трьох джерел дії (веб-дашборд `webapp.py`,
фізична кнопка `shutdown_button.py`, і `display.py` — та сама кнопка,
короткий шлях через прямий import `_trigger_shutdown` з `shutdown_
button.py`, не дублювання коду) — `execute_pi_power_action()` виконує
`systemctl reboot`/`poweroff`, записує подію, надсилає Telegram.

**Показ повідомлення на TFT-дисплеї - через DB-сигнал, не прямий
виклик `display.py`**: це окремий процес, що ексклюзивно тримає
SPI-запит, тому неможливо намалювати щось на екрані напряму з іншого
процесу (той самий клас обмеження, що вже вирішувався для LED-
активності через GPIO character-device API). `db.set_setting(
pi_power.PENDING_ACTION_SETTING_KEY, action)` записується ПЕРЕД
реальним викликом `systemctl`. `display.py` опитує цей setting у
своєму швидкому (~100мс, `DISPLAY_BUTTON_POLL_INTERVAL_SEC`) циклі
кнопки — НЕ чекає звичайний 5-секундний `DISPLAY_REFRESH_SEC`-цикл
оновлення статусу, бо часу до SIGTERM від самого `systemctl reboot/
poweroff` замало. Виявивши сигнал, малює `_draw_power_action_message()`
і одразу завершує `run_forever()` (`return`, не продовжує звичайний
цикл — процес все одно скоро буде вбитий).

**`DISPLAY_SHUTDOWN_MESSAGE_DELAY_SEC`** (типово 2с, застосовується
лише якщо `DISPLAY_ENABLED=1`) — затримка ПЕРЕД реальним `systemctl`-
викликом у `pi_power.py`, дає час 100мс-циклу дисплея реально
побачити сигнал і намалювати кадр ДО того, як сам процес дисплея
буде вбитий.

**Сигнал прибирається ПЕРЕД виконанням реальної команди, не ПІСЛЯ**
— другий реальний баг, знайдений користувачем на практиці (перший
фікс, описаний нижче, лишав race condition). Якщо цю функцію
викликає `webapp.py`, вона виконується всередині процесу
`starlink-webui.service`. Коли САМЕ ЦЕЙ процес ініціює `systemctl
reboot`, systemd починає ГЛОБАЛЬНУ зупинку всіх сервісів — включно
з тим самим `starlink-webui.service`. `SIGTERM` міг прийти РАНІШЕ,
ніж код після `run_system_command()` встигав виконати очищення —
сигнал лишався в БД, і після реального перезавантаження `display.py`
бачив цей застарілий сигнал і малював повідомлення ПІСЛЯ, а не
до/під час reboot (симптом, з яким користувач звернувся повторно).
На момент цього рядка сигнал уже виконав свою єдину мету
(`display.py` встиг побачити його й намалювати повідомлення під час
затримки вище) — подальше виконання команди відбувається без нього.

**Перший фікс (історичний контекст)**: раніше сигнал очищався лише
при провалі команди — при успіху лишався в БД назавжди: після
реального перезавантаження `display.py` стартував заново, бачив
застарілий сигнал, малював повідомлення й завершувався (`return`) —
а `Restart=on-failure` не перезапускає процес після чистого
завершення (`exit 0` не вважається `failure`), тому сервіс лишався
`inactive` назавжди, і наступний reboot/poweroff уже нічого не
малював (сервіс вже мертвий). Перенесення очищення на "завжди,
одразу після команди" вирішило цей симптом, але залишило race
condition, вирішену вище.

**Реальна помилка, знайдена живим тестом, не в production-коді, а в
API-дизайні**: перша версія `notify_fn: Callable[[str], Any] =
telegram_notify.send_message` як early-bound default-параметр
обчислюється ОДИН раз при імпорті модуля - `patch("app.telegram_
notify.send_message")` у тестах не міг його замінити (default вже
"заморожений" як посилання на оригінальну функцію). Виправлено на
lazy-визначення (`notify_fn: Optional[...] = None`, `if notify_fn is
None: notify_fn = telegram_notify.send_message` всередині функції) -
краща практика незалежно від тестів, уникає прихованого "заморожування"
залежності при імпорті.

**Символи на дисплеї для повідомлення** — перша версія використовувала
`⏻`/`🔁` (Unicode symbol/emoji), автоматизована перевірка `font.
getmask(ch).getbbox() is not None` хибно підтвердила "гліф є" - лише
РЕАЛЬНИЙ рендеринг зображення показав порожні "тофу"-квадрати
(fallback-гліф теж дає непорожній bbox). Замінено на `●` (той самий
символ, що вже підтверджено працює в `_status_lines()` для online/
offline індикатора).

## LED активності SD-картки (GPIO)

На відміну від кнопки/дисплея — **не окремий процес**, частина
`monitor.py` (watchdog-цикл), бо реагує на кожен реальний commit у
SQLite, а не на зовнішню подію на власному таймері. `app/activity_
led.py:ActivityLed` — інкапсулює GPIO-стан; `init()` викликається раз
на старті `run_forever()`, реєструє `blink` як callback через
`db.set_activity_callback()`. `app/db.py:get_conn()` викликає цей
callback **лише** після успішного `conn.commit()` (не при винятку/
rollback — LED не має блимати на провалених записах).

**Лише watchdog-процес ініціалізує LED, не `webapp.py`** — уникає
конфлікту двох процесів за один ексклюзивний GPIO-запит (gpiod
character-device API не дозволяє двом процесам одночасно тримати
запит на ту саму лінію). `webapp.py` не імпортує `activity_led`
взагалі.

**Неблокуючий `blink()`** — вмикає LED синхронно, вимкнення
відкладається через `threading.Timer(blink_sec, ...)` (daemon-потік,
не заважає завершенню процесу) — сам запис у БД не отримує додаткової
затримки. Послідовні `blink()`-виклики (напр. dish-flush і system_
metrics в одному циклі) скасовують попередній таймер (`Timer.cancel()`)
перед стартом нового — LED лишається рівно увімкненим між ними, не
блимає нервово. Перевірено живим тестом: два `blink()` з малим
інтервалом дають послідовність `[1, 1, 0]` (не `[1, 0, 1, 0]`).

`gpio_utils.open_output_line()` — симетрична до `open_input_line()`
(gpiod v1/v2-сумісність), але `Direction.OUTPUT`/`LINE_REQ_DIR_OUT`
замість `INPUT`, і `set_value(int)` замість `get_value()`.

`ACTIVITY_LED_PIN=0` (дефолт, вимкнено) — той самий принцип opt-in,
що кнопка й дисплей. `starlink-monitor.service` отримав
`SupplementaryGroups=gpio` (раніше був відсутній - монітор не
потребував GPIO до цієї фічі), `install.sh` уже додавав `RUN_USER`
до групи `gpio` для кнопки/дисплея, тому додаткових кроків
встановлення не потрібно.

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

PWA: `/manifest.json` (динамічний Flask-endpoint, не статичний файл -
name/description перекладаються поточною мовою через `app/i18n.py`)
+ `static/sw.js` (service worker) + `static/pwa.js` (реєстрація SW,
підключена в усіх 3 HTML-шаблонах). Іконки `icon-192.png`/
`icon-512.png` згенеровані з `logo.png` (вписані в квадрат на
`--sky-900` фоні). Service worker кешує лише `/static/*`
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

## /stats — повна статистика

Головна сторінка показує лише 5 останніх подій журналу.
`/stats` (`templates/stats.html`, `static/stats.js`) — повний журнал
подій (`limit=300`), без інших елементів дашборду. "Очистити" на
обох сторінках — лише локально в браузері (`eventsClearedLocally`
в кожному JS-файлі окремо, БД не зачіпається), той самий підхід,
що вже був на головній.

**Дата-групування журналу** (`static/common.js:fmtDateHeader()`) —
300 подій з `HISTORY_RETENTION_DAYS` (типово 14 днів) зберігання
реально охоплюють багато днів, а `fmtTime()` показує лише час
(ГГ:ХХ:СС), без дати - без групування неможливо було зрозуміти, до
якого дня належить подія, особливо навколо півночі. `refreshEvents()`
(`static/stats.js`) групує вже відсортовані найновіші-першими події
(БД: `ORDER BY ts DESC`) за календарним днем, вставляючи
`.log-date-header` при кожній зміні дня - "Сьогодні"/"Вчора" для
двох найближчих днів, повна дата для решти. `dateKey` рахується за
локальним часовим поясом браузера (`Date.getFullYear/Month/Date()`,
не діленням `ts/86400`) - інакше межа групи не збігалася б із тим,
як користувач реально бачить час у `fmtTime()`. Головна сторінка
(лише 5 останніх подій) навмисно НЕ отримала цього групування -
компактний віджет, повні заголовки додали б візуального шуму без
пропорційної користі для такої малої кількості елементів.

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

## fstrim.timer

`install.sh` вмикає стандартний systemd-таймер `fstrim.timer` (частина
`util-linux`, майже напевно вже встановлена на Raspberry Pi OS — лише
`enable --now`, без встановлення пакету) — та сама лінія, що
`PRAGMA synchronous=NORMAL` у `db.py`: зменшення зношення SD-картки,
періодичний TRIM консолідує вільний простір на flash-носіях, що його
підтримують. Безпечно навіть якщо конкретна SD-картка TRIM не
підтримує — `fstrim` тоді просто нічого не робить, не шкодить.
Перевірка наявності юніта (`systemctl list-unit-files fstrim.timer`)
перед enable — на випадок застарілого `util-linux` без цього таймера.
Навмисно НЕ вимикається в `uninstall.sh` — загальносистемна корисна
практика, не специфічна для цього проєкту.

## Фізичний TFT-дисплей (app/display.py)

Окремий процес (той самий патерн, що `shutdown_button.py`) —
`run_forever()` виходить миттєво, якщо `DISPLAY_ENABLED=0`. Малює
кадр через Pillow кожні `DISPLAY_REFRESH_SEC`, надсилає через
`display.image(image)` — бібліотека Adafruit CircuitPython ST7789.
`_status_lines()` повертає структурований список `{"kind": ...,
"text": ...}` (+ `"progress"` для `kind="update"`) — не плаский
список за позицією (рядок оновлення опційний, індекси були б
крихкими).

`_redraw()` малює за `kind`: `"status"` (статус+uptime в одному
рядку, lime/red), `"update"` (окремий рядок для тарілки й роутера,
з прогрес-баром `draw.rectangle()`), `"firmware"` (повні мітки,
влазять завдяки landscape canvas при ROTATION=90/270).
`DOWNLOADING_UPDATE_IMAGE_FAILED`/`GETTING_TARGET_VERSION_FAILED`
для роутера приховуються (`HIDDEN_ROUTER_STATES` — той самий підхід,
що `static/dashboard.js`) — тимчасова хмарна помилка SpaceX, не
справжня проблема. `_truncate_to_width()` обрізає `…` для будь-якого
рядка, що не влазить (виміряно через `draw.textlength()`).

Ініціалізація через `board`/`digitalio`/`busio` (Blinka) — CS
bit-banged GPIO (не апаратний CE0/CE1), бібліотека сама коректно
виводить RST з reset ДО SPI-команд, rotation 0/90/180/270 для
будь-якого aspect ratio через програмний `img.rotate()`, підсвітка
керується напряму окремим `digitalio`-об'єктом (бібліотека не має
вбудованого `set_backlight()`).

**Важливо для rotation=90/270**: `image()` перевіряє розмір
зображення ПІСЛЯ `rotate()` проти `display.width`/`height` (самі не
змінюються параметром rotation) — `_redraw()` тому створює полотно з
транспонованими розмірами саме для 90/270, інакше `ValueError` для
прямокутного дисплея. `OFFSET_LEFT`/`OFFSET_TOP` діють на рівні
фізичної GRAM-адресації, застосовуються вже після повороту — не
потребують перерахунку залежно від rotation.

Паспортна роздільна здатність (SKU MSP1901) — 170×320, portrait,
підтверджена офіційною документацією виробника. `BLK` вмикається
HIGH; якщо підключено напряму до 3.3V — `DISPLAY_BL_PIN=0`. Потребує
`dtparam=spi=on` (install.sh попереджає, якщо не увімкнено).
`OFFSET_LEFT` дефолт 35 — емпірично підтверджене значення для цієї
моделі (див. `decisions-log.md` про діагностику кольорового шуму,
спершу помилково прийнятого за апаратний дефект). `SPI_SPEED_HZ`
дефолт 40МГц — консервативний для джампер-дротів, можна підняти при
якісному монтажі.

**Кнопка** — та сама, що `SHUTDOWN_BUTTON_GPIO_PIN` (не окремий пін):
коротке перемикає підсвітку, довге вимикає Pi через
`_trigger_shutdown()`, імпортовану напряму з `shutdown_button.py`.
Обробляється в цьому самому циклі (не окремим сервісом — керування
BL-піном можливе лише з процесу, що тримає `digitalio`-об'єкт).
`shutdown_button.py` сам себе вимикає, коли `DISPLAY_ENABLED=1`, щоб
не конкурувати за той самий GPIO. `gpio_utils.ButtonPressTracker` —
спільна, чиста стейтфул-структура детекції короткого/довгого
натискання, використовується і тут, і в `shutdown_button.py`. Кнопка
опитується кожні `BUTTON_POLL_INTERVAL_SEC` (0.1с), редрав — лише раз
на `DISPLAY_REFRESH_SEC` (окремий таймер, не блокуючий сон).

**Автовимкнення підсвітки** (`BACKLIGHT_AUTO_OFF_SEC`, дефолт 60с):
`_should_auto_off()` — чиста функція. `last_activity_ts` оновлюється
при кожному ввімкненні — рахує час від моменту ввімкнення, не від
загальної бездіяльності.

**Flash при зміні статусу оновлення** (`UPDATE_FLASH_SEC`, дефолт 5с):
`_update_state_changed()` — чиста функція, порівнює update_state
відносно попереднього опитування (перше зчитування НЕ вважається
зміною). При зміні — `last_activity_ts=now` (критично: інакше
60с-auto-off міг би вимкнути підсвітку до завершення коротшого
5с-flash), окремий `flash_until_ts` з явним guard проти конфлікту
двох таймерів. Ручне натискання під час flash скасовує
`flash_until_ts`.
