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
  в один рядок (`count`, `last_ts` замість нового запису щоразу)
- `system_metrics` — CPU/RAM/диск/температура Pi
- `router_status` — останній відомий стан роутера, включно зі списком
  під'єднаних WiFi-клієнтів (`clients`: ім'я/MAC, IP, діапазон, сигнал,
  час у мережі)
- `settings` — runtime key-value (auto_reboot_enabled, telegram config,
  known_dish_ids, dish_target_version/router_target_version + їхні
  `*_notified` пари — див. нижче)
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
дозволяє явно вказати ОЧІКУВАНУ версію, чи КІЛЬКА через кому (той
самий формат, що `telegram_chat_ids`) — корисно, коли SpaceX випускає
РІЗНІ номери версій для різних апаратних ревізій під однією умовною
версією, і користувач не певен, який саме рядок реально прийде на
його конкретне обладнання. Матч на БУДЬ-ЯКУ з перелічених версій дає
"✅ Останнє оновлення тарілки/роутера встановлено: версія X".
`db.parse_version_list()` — спільний helper (не дублюється в
`monitor.py`/`webapp.py`), розбирає comma-separated рядок.
`monitor.version_in_target_list()` — перевірка входження.
`monitor.check_target_version_reached()` (module-level функція, НЕ
метод класу `Watchdog` — колишні `Watchdog._version_in_target_list()`/
`check_target_version_reached()` thin-wrapper методи видалені як
мертвий код: жоден production-виклик не використовував їх напряму
після рефакторингу на спільні функції для watchdog-циклу й ручної
кнопки, лише застарілі тести — тести оновлено на прямі виклики) —
порівнює `current_version` з `settings["dish_target_version"]`.
Дедублікація без окремого boolean-прапорця: `dish_target_notified`
зберігає ТРІЙКУ (`dish_id` + яка саме версія збіглась + повний
target-список) — природно "скидається", коли користувач змінює
список (наприклад додає версію для іншої апаратної ревізії), без
потреби explicit очищати стан. `dish_id` у ключі — знайдено
користувачем реальний сценарій: якщо один Pi моніторить РІЗНІ фізичні
Starlink з часом (напр. заміна обладнання), дедублікація БЕЗ
`dish_id` помилково блокувала б сповіщення для нового пристрою, якщо
той збігається версією+target з ПОПЕРЕДНІМ (уже сповіщеним) фізичним
Starlink — dish_id гарантує, що кожен фізичний пристрій отримує
власне, незалежне сповіщення. `/api/target-versions` (GET/POST) —
CRUD, GET також повертає `dish_current`/`router_current` (з
`get_latest_metric()`/`get_router_status()`) для **першочергового
введення**: `settings.js` `loadTargetVersions()` показує поточну
відому версію як стартове
значення інпута, коли target ще не задано (не порожнє поле) — типовий
сценарій "щойно встановив, приймаю поточну версію за базову, зміню
коли дізнаюсь про наступну".

`POST /api/target-versions` приймає новий список версій лише якщо
КОЖЕН кандидат у ньому НЕ старіший за вже відому (`is_older_version()`,
`webapp.py`) — база для порівняння = максимум із (попередній target-
список, якщо був; поточна встановлена версія). Захист від випадкового
відкату (описка, забутий раніше введений новіший target). Якщо ХОЧ
ОДИН кандидат у списку старіший — відхиляється ВЕСЬ список для цього
поля (не часткове прийняття окремих кандидатів — простіша, чіткіша
семантика для користувача, ніж часткові стани всередині одного поля).
`db.version_key()` (публічна, у `db.py` — спільна для `webapp.py` і
`monitor.py`, не webapp.py-приватна) — толерантний компаратор для
формату `YYYY.MM.DD.mrXXXXX.N` (не строгий semver): дата-префікс
домінує як найнадійніший індикатор "новіше", посегментне порівняння
як fallback, коли дата однакова чи відсутня (не падає на нетиповому
форматі — регулярка просто не збігається, `date_part = (0,0,0)` для
обох, порівняння йде повністю по сегментах).

**`db.version_channel()`** — витягує буквений префікс build-сегмента
(напр. `mr` із `mr82648`, `cr` із `cr81950`). Знайдено на реальному
запиті користувача: різні апаратні ревізії Starlink можуть отримувати
оновлення з НЕЗАЛЕЖНИХ build-каналів одночасно, з різними датами —
пряме порівняння дати МІЖ каналами (`2026.07.06.cr...` vs
`2026.07.19.mr...`) хибно відхиляло валідний candidate як "старіший".
`_find_older_candidates()` (`webapp.py`) — порівнює candidate ЛИШЕ
проти baseline ТОГО САМОГО каналу; якщо candidate НЕ МАЄ явного
каналу (проста дата без mr/cr-суфікса, напр. `2020.01.01`) —
порівнюється глобально з УСІМА baseline (fallback, не "свій окремий
непорівнюваний канал" — цей edge case знайдено власним тестом одразу
після першої реалізації, до живого запуску).

**Другий реальний сценарій, знайдений на практиці**: якщо dish/router
РЕАЛЬНО відкотився (SpaceX-side rollback, вже підтверджений раніше)
ПІСЛЯ того, як user уже ввів target для ОЧІКУВАНОЇ (новішої) версії —
поточна встановлена версія стає СТАРІШОЮ за раніше збережений target.
User хоче узгодити target із ФАКТИЧНОЮ реальністю (щоб отримати
сповіщення, коли ЦЯ, фактично встановлена зараз версія стабільно
підтвердиться) — стара логіка блокувала це, порівнюючи проти
`max(попередній_target, поточна)`, не розрізняючи "випадкова описка"
від "свідоме узгодження з поточним станом". `_find_older_candidates()`
приймає окремий параметр `current_installed` — candidate, що ТОЧНО
збігається з фактично встановленою версією, ЗАВЖДИ дозволяється,
незалежно від старішого попереднього target. Захист від справжніх
описок (candidate, що НЕ збігається з поточною версією І не новіший)
лишається непорушним (контрольний тест).

Часткова відмова МІЖ
полями (dish vs router,
не ВСЕРЕДИНІ одного поля): якщо dish valid, а router — ні (чи
навпаки), валідне поле зберігається, невалідне відхиляється окремо
з поясненням у `message` (не "усе або нічого" для обох компонентів
одночасно). Порожній рядок, явно надісланий — команда ОЧИСТИТИ поле
(не "нічого не робити": до цього порожній рядок мовчки ігнорувався,
без жодного способу скасувати раніше введений target). Відповідь API
завжди містить `message` з описом реального результату (збережено/
відхилено/очищено/без змін) — не лише коли є відхилені кандидати.

`monitor.check_both_targets_reached()` (module-level, не метод класу
— той самий рефакторинг, що вище) — окреме, комбіноване
підтвердження "🎉 Процедуру оновлення завершено", коли ОБИДВІ
(тарілка й роутер) очікувані версії одночасно збігаються зі
встановленими (на додачу до per-component "✅", не замість них — той
корисний сам по собі: dish оновився, вже цікаво знати, навіть якщо
router ще ні). Викликається з ОБОХ `poll_once()` і `poll_router()` —
або один, або інший цикл опитування може стати тим, що робить умову
істинною одночасно для обох (компоненти опитуються незалежно, різними
циклами). Дедублікація — той самий принцип, що в
`check_target_version_reached()`, notified-ключ (`both_targets_
notified`) включає `self.last_known_dish_id` теж (той самий принцип
ізоляції МІЖ фізичними пристроями) + обидва target одночасно —
природно "скидається", щойно користувач змінить БУДЬ-ЯКИЙ з двох
(не лише обидва разом) чи фізичний Starlink зміниться. Не входить у
backup/restore — internal dedup-стан
конкретного пристрою й моменту, не user-налаштування (той самий
принцип, що вже застосований до `dish_target_notified`/
`router_target_notified`).

**Module-level функції, спільні для watchdog і ручної кнопки** —
`version_in_target_list()`, `check_target_version_reached()`,
`check_both_targets_reached()`, `upsert_dish_and_notify()`,
`upsert_router_and_notify()`, `check_updates_now()` (усі в `app/
monitor.py`, ПОЗА класом `Watchdog`, приймають `notify_fn: Callable[
[str], None]` замість `self._notify`). Колишні `Watchdog`-thin-wrapper
методи (`check_target_version_reached()` тощо) видалені як мертвий
код (нижче) — жоден production-виклик не використовував їх напряму
після цього рефакторингу.

`monitor.check_updates_now(client, notify_fn) -> (DishStatus,
RouterInfo)` — ручна перевірка стану оновлень: опитує dish/router,
записує в БД, викликає `upsert_dish_and_notify()`/`upsert_router_and_
notify()`. Спільна для `webapp.py` `api_check_updates()` (кнопка
"Перевірити оновлення" на дашборді) і `telegram_bot.py` `_cmd_check_
updates()` (команда `/checkupdates`) — уникає дублювання ІДЕНТИЧНОЇ
логіки в двох місцях (та сама категорія прогалини, що вже кілька
разів знаходилась у цьому проєкті: дублювання накопичується непомітно
при паралельних правках однієї бізнес-логіки в різних інтерфейсах).
Раніше (до винесення) — **знайдена й виправлена реальна прогалина**:
ручна кнопка на дашборді лише записувала статус у БД, БЕЗ жодного
сповіщення (ні "✅ target-версія", ні "🔄 прошивка оновлена"), навіть
коли версія якраз збігалась із target у момент натискання.
`dish_id` для router-виклику — якщо dish
зараз online, береться напряму з відповіді; інакше падає на
найновіший запис `known_devices` (dish міг бути offline саме в
момент ручної перевірки, поки router усе ще відповідає — той самий
фізичний Mini).

**`IGNORED_ROUTER_ALERTS`** (module-level множина, зараз лише
`wired_mesh_not_using_wan_iface` — шумний для mesh-конфігурацій
router-alert, без практичної цінності) — **знайдений і виправлений
реальний баг**: раніше визначений як class-attribute ВСЕРЕДИНІ
`Watchdog.poll_router()`, фільтр застосовувався лише у фоновому циклі
watchdog'а. `check_updates_now()` (винесена module-level функція,
описана вище) записувала router-статус НАПРЯМУ, без цього фільтра —
"прибраний" alert повертався щоразу, коли user тиснув "Перевірити
оновлення" вручну (веб-кнопка чи `/checkupdates`), аж до наступного
фонового циклу `poll_router()` (~35с), який знову коректно його
прибирав. Виправлено — константа тепер module-level, застосовується
в ОБОХ місцях запису `db.set_router_status()`.

**`telegram_bot.py` `_api_call()`** — знайдений і виправлений реальний
баг (запит користувача: "не приходить відповідь на /id"): раніше
перевіряла ЛИШЕ `requests.RequestException` (мережеві помилки) — якщо
Telegram API ВІДХИЛЯВ запит (валідний JSON з `ok: false`, напр.
"message is too long" при перевищенні ліміту 4096 символів), це
проходило непоміченим, виглядаючи як "команда взагалі не відповідає".
Тепер `ok`-поле перевіряється явно, `logger.warning()` із причиною.
`_cmd_id()` без аргументу (список УСІХ known_devices, зростає з часом)
— найвразливіша команда до цього ліміту, тому додатково обмежена
`TELEGRAM_ID_LIST_MAX_ITEMS` (дефолт 40, `get_all_known_devices()`
уже сортує за `last_seen_ts DESC` — найновіші показуються першими),
з явним "…і ще N" поясненням, коли список обрізаний.

Усі таблиці мають автоматичну міграцію колонок при `init_db()` —
безпечно для вже існуючих БД при оновленні коду.

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

**Сигнал прибирається ЗАВЖДИ (успіх і провал), одразу після
`run_system_command()`** — реальний баг, знайдений користувачем на
практиці (не гіпотетичний): раніше сигнал очищався ЛИШЕ при провалі
команди. При УСПІШНОМУ reboot/poweroff `systemctl` не миттєвий —
`subprocess.run()` повертається одразу після ІНІЦІЮВАННЯ команди
(systemd послідовно зупиняє процеси), лишаючи реальні кілька секунд
до фактичного вимкнення — достатньо часу виконати очищення сигналу.
Без цього сигнал лишався б у БД назавжди після успіху: після
реального перезавантаження `display.py` стартує заново, бачить той
самий застарілий сигнал, малює повідомлення ПОВТОРНО і завершується
(`return`) — а `Restart=on-failure` НЕ перезапускає процес після
чистого завершення (`exit 0` не вважається `failure`), тому сервіс
лишається `inactive` назавжди. Наступний reboot/poweroff тоді
взагалі нічого не малює (сервіс вже мертвий - симптом "під час
перезавантаження нічого не відображається"), а TFT-дисплей фізично
зберігає останній намальований кадр у власному framebuffer незалежно
від того, чи процес, що його малював, ще працює - виглядає, ніби
повідомлення "висить" на екрані безкінечно довго.

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

## /stats — повна статистика

Головна сторінка показує лише 5 останніх подій журналу.
`/stats` (`templates/stats.html`, `static/stats.js`) — повний журнал
подій (`limit=500`), без інших елементів дашборду. "Очистити" на
обох сторінках — лише локально в браузері (`eventsClearedLocally`
в кожному JS-файлі окремо, БД не зачіпається), той самий підхід,
що вже був на головній.

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
`DOWNLOADING_UPDATE_IMAGE_FAILED` і `GETTING_TARGET_VERSION_FAILED`
для роутера свідомо приховуються (`HIDDEN_ROUTER_STATES`, module-level
константа, той самий паттерн ім'я в `static/dashboard.js`) — обидва
"тимчасова хмарна помилка перевірки/завантаження оновлення на боці
SpaceX", частина нормального циклу перевірки, не справжня помилка;
приховано і на веб-дашборді. `"firmware"`
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

**Flash-сповіщення при зміні статусу оновлення**
(`DISPLAY_UPDATE_FLASH_SEC`, дефолт 5с, 0=вимкнено): `_update_state_
changed()` — чиста функція (легко тестується без реального дисплея),
порівнює `update_state` dish/router ВІДНОСНО ПОПЕРЕДНЬОГО опитування
(перевіряється разом з redraw, кожні `DISPLAY_REFRESH_SEC`, не щоцикл
поллінгу кнопки). `prev_dish_state`/`prev_router_state`=`None` на
старті сервісу — перше зчитування лише запам'ятовує стан, НЕ
вважається зміною (інакше кожен запуск сервісу спалахував би
підсвіткою даремно). При виявленій зміні — `backlight_on=True`,
`last_activity_ts=now` (**критично**: інакше `_should_auto_off()`
(типово 60с) міг би вимкнути підсвітку ще ДО завершення коротшого
flash-періоду, 5с — знайдено й виправлено під час реалізації), окремий
`flash_until_ts` для явного вимкнення саме після flash-тривалості
(незалежно від звичайного `_should_auto_off()`-механізму — `if
flash_until_ts is None and _should_auto_off(...)`, явний guard проти
конфлікту двох таймерів). Ручне натискання кнопки (short_press) під
час активного flash-періоду скасовує `flash_until_ts` — user явно
взаємодіє з дисплеєм, не форсувати несподіване вимкнення одразу
після цього.
