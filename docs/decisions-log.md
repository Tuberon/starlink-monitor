# Журнал рішень

Хронологічний запис значущих технічних рішень і знайдених багів.

## Виявлення реальної схеми gRPC (не за здогадками)

На старті проекту помилково припускалось, що Starlink Mini — єдиний
пристрій. Живі виклики `grpcurl describe` на dish (`192.168.100.1:9200`)
і router (`192.168.1.1:9000`) підтвердили: це два окремі логічні
пристрої з різними `DeviceInfo`, різними enum станів оновлення
(`SoftwareUpdateState` vs `WifiSoftwareUpdateState`), різними наборами
alert-полів (`DishAlerts` 19 полів vs `WifiAlerts` 21 поле). Уся
подальша логіка (`starlink_client.py`, `labels.py`) побудована на
цих підтверджених схемах, не на припущеннях.

**Практика, що склалась**: для будь-яких технічних даних (розміри
плат, protobuf-схеми, назви enum) — підтверджувати через `grpcurl
describe`/web_search/фото, не вигадувати правдоподібні числа.

## Баг: конфлікт параметра `timeout` в Telegram API

**Симптом**: `TypeError: got multiple values for argument 'timeout'`,
бот не отримував жодних вхідних команд, помилка тихо гасилась у
`except Exception` кожні 5с.

**Причина**: `_api_call(method, token, timeout, **params)` мав
формальний параметр `timeout`, а виклик `getUpdates` одночасно
передавав HTTP-таймаут позиційно і Telegram API параметр
`timeout=POLL_TIMEOUT_SEC` (long polling) як іменований — колізія.

**Виправлення**: перейменовано внутрішній параметр на `http_timeout`.

## NoNewPrivileges вимкнено на webui та shutdown-button сервісах

`starlink-webui.service` і `starlink-shutdown-button.service`
навмисно **без** `NoNewPrivileges=true` — обидва викликають
`sudo systemctl reboot/poweroff` (ручний reboot/shutdown Pi через
веб-інтерфейс і через фізичну кнопку). `NoNewPrivileges` на рівні
ядра блокує будь-який `sudo` незалежно від `/etc/sudoers`, тож із
цим прапорцем ці виклики не спрацювали б.

`starlink-monitor.service` лишається з `NoNewPrivileges=true` —
той процес sudo не викликає.

## Баг: signature_phrases.txt read-only при веб-редагуванні

**Симптом**: `[Errno 30] Read-only file system` при спробі зберегти
фрази через веб-інтерфейс.

**Причина**: `ProtectSystem=strict` робить всю ФС read-only, крім
`ReadWritePaths`, куди спочатку входив лише `/var/lib/starlink-monitor`,
не `/opt/starlink-monitor/app/signature_phrases.txt`.

**Виправлення**: додано конкретний файл (не весь `/opt/starlink-monitor`)
у `ReadWritePaths` — мінімальне розширення дозволів.

## Фізична кнопка виключення: окремий процес, не інтеграція в існуючі

**Рішення**: `app/shutdown_button.py` — окремий Python-модуль і
окремий systemd-сервіс, не частина `monitor.py`/`webapp.py`.

**Чому окремо**:
- GPIO-доступ вимагає групу `gpio` і системний `python3-libgpiod`
  (apt, не pip — чистіше на Raspberry Pi OS) — зайва залежність для
  установок без фізичної кнопки
- Вимкнено за замовчуванням (`SHUTDOWN_BUTTON_GPIO_PIN=0`), сервіс
  одразу виходить — не критична помилка, якщо кнопки немає
- `Restart=on-failure` (не `always`) свідомо: чистий вихід при
  вимкненій кнопці не має спричиняти нескінченний рестарт-цикл systemd

**Побічний ефект**: venv тепер створюється з `--system-site-packages`
(щоб бачити системний `gpiod`) — це змінює поведінку venv для *всіх*
пакетів, не лише gpiod (венв тепер бачить будь-який системний Python-
пакет). Прийнятний компроміс, бо альтернатива (pip-встановлення
gpiod з компіляцією C-розширення) менш надійна на слабкому Pi Zero 2 W.

**Третя зміна NoNewPrivileges**: аналогічно до `starlink-webui.service`
(див. розділ вище), `starlink-shutdown-button.service` теж без
`NoNewPrivileges` — потребує sudo для `poweroff`.

## Баг: reboot-loop при невдалій auto-reboot команді

`last_reboot_ts` оновлювався лише при `reboot_dish() == True`. Якщо
dish ще перезавантажується з попередньої спроби (`connection
refused`), команда reboot теж провалюється — `MIN_REBOOT_INTERVAL_SEC`
ніколи не спрацьовував, watchdog повторював спробу reboot щоцикл
опитування (кожні ~10с) замість одного разу на 15 хв. Виправлено:
`last_reboot_ts` оновлюється при кожній *спробі*, незалежно від
результату.

## Налаштування статичних IP (eth0/wlan0) в install.sh — інтерактивне, не автоматичне

Мережева конфігурація (USB-Ethernet + WiFi Starlink) ризикована для
автоматичного застосування без підтвердження — неправильні дефолти
(наприклад, конфлікт підмереж з реальною домашньою мережею) можуть
обірвати SSH-з'єднання, яким адмінить сам Pi. `install.sh` тому
питає підтвердження (`[т/N]`) і дозволяє ввести власні IP/gateway
замість дефолтних, а не застосовує зміни мовчки. `dhcpcd` вимикається
лише в межах цього опційного кроку (не для всіх встановлень) — він
конфліктує з NetworkManager, перевидаючи власні DHCP-лізинги
незалежно від `ipv4.method=manual` у профілі nmcli.

## Баг: gpiod v2 несумісний зі старим API кнопки виключення

**Симптом**: `'Chip' object has no attribute 'get_line'`, сервіс
`starlink-shutdown-button` одразу завершувався з помилкою.

**Причина**: `python3-libgpiod` на Raspberry Pi OS Bookworm+ ставить
gpiod **2.x**, де Python API повністю переписано (`chip.get_line()` +
`line.request()` з v1.x видалені; замість них — `gpiod.request_lines()`
з `LineSettings`, значення через `Value.ACTIVE`/`Value.INACTIVE`
замість `0`/`1`). Код спирався лише на v1 API.

**Виправлення**: `_init_line_v1`/`_init_line_v2` — обидві реалізації,
вибір через `hasattr(gpiod.Chip, "get_line")` (є лише в v1). Значення
з v2 конвертується в 0/1 всередині обгортки `get_value()`, решта
логіки (`watch_button()`) не залежить від версії API.

## Мережевий запит install.sh — лише при першому встановленні, не при оновленні

Питання про статичні IP (eth0/wlan0) обгорнуто умовою `MODE == "install"`
— мережа налаштовується один раз і не потребує перезапиту при кожному
`update.sh`/повторному `install.sh`. `scripts/uninstall.sh` — окремий
файл (не прапорець у `install.sh`), щоб не ускладнювати основний потік
встановлення умовними гілками, які рідко використовуються.

## Придушені конкретні Telegram-сповіщення (журнал не зачіпається)

`MUTED_DISH_ALERTS={"roaming"}`, `MUTED_ROUTER_ALERTS={"install_pending"}`,
`MUTED_ROUTER_UPDATE_STATES={"GETTING_TARGET_VERSION_FAILED"}` — на
запит користувача ці три конкретні події не надсилаються в Telegram
(шумні/непрактичні для сповіщення в реальному часі), але й далі
пишуться в журнал подій дашборду. На відміну від `IGNORED_ROUTER_ALERTS`
(повне ігнорування, включно з БД/журналом) — тут приховується лише
Telegram-звіт.

## Telegram-сповіщення про початок/кінець циклу оновлення ПЗ dish

`DOWNLOADING_UPDATE_STATES={"FETCHING","PRE_CHECK","WRITING","POST_CHECK"}`,
`ACTIVE_UPDATE_STATES` = той самий набір + `REBOOT_REQUIRED`. Перехід
з "не активного" стану в один з `DOWNLOADING_UPDATE_STATES` → "🔽
Розпочато оновлення"; повернення з будь-якого активного стану в
`IDLE` → "✅ Оновлення завершено". `REBOOT_REQUIRED`/`FAULTED`
перевіряються першими в ланцюжку `if/elif`, зберігаючи власні
специфічні повідомлення навіть при прямому переході `IDLE →
REBOOT_REQUIRED` (рідкісний edge case, коли поллінг пропустив
проміжні стани).

## Обмеження журналу watchdog-спроб при дуже тривалій недоступності

`consecutive_failures` не має верхньої межі й зростає на кожному
циклі опитування (10с), поки dish недоступний — при багатогодинній
відсутності WiFi Starlink лічильник сягає тисяч, і кожна watchdog-
спроба (кожні `MIN_REBOOT_INTERVAL_SEC`) писала окремий запис у
журнал/БД з дедалі більшим числом, без нової корисної інформації.
`MAX_LOGGED_CONSECUTIVE_FAILURES` (30) обмежує запис: спроби 1-30
логуються нормально (`watchdog_trigger` + `dish_reboot`), спроба 31
дає один фінальний маркер ("Понад 30... подальші спроби не
записуються"), спроби 32+ мовчать у журналі — але сама функція
`reboot_dish()` викликається на кожному циклі без змін, лише запис
у БД припиняється.

## Баг: Telegram-бот блокувався на повільних командах

**Симптом**: бот деколи довго (до ~15с) відповідав на команди.

**Причина**: `_handle_update()` (обробка `/status` тощо, з блокуючими
gRPC-викликами до dish/router — таймаут до `DISH_HTTP_TIMEOUT`+
router-таймаут ≈15с) виконувалась синхронно в тому самому потоці,
що й `getUpdates` polling-цикл. Поки одна команда оброблялась,
наступний `getUpdates`-запит і будь-які нові команди чекали.

**Виправлення**: `ThreadPoolExecutor(max_workers=3)` — кожен update
обробляється в окремому потоці, `_poll_once()` одразу повертається
до наступного `getUpdates` незалежно від тривалості обробки.

**Побічний ефект, виявлений і виправлений одразу**: паралельна
обробка усувала гарантію порядку виконання — `/reboot` і миттєвий
клік підтвердження від того самого користувача могли потрапити в
різні потоки пулу й завершитись у довільному порядку (callback міг
обробитись раніше за встановлення `_pending_reboot_confirm` командою
`/reboot`). Виправлено групуванням updates за `chat_id` у межах
кожного `getUpdates`-batch: той самий чат обробляється послідовно в
одному потоці (гарантує порядок), різні чати — паралельно (швидкий
чат не чекає повільний).

## Рефакторинг: спільний хелпер для update-ready reboot (dish + router)

`_maybe_reboot_for_update`/`_maybe_reboot_for_router_update` містили
майже ідентичну логіку (auto_reboot_enabled, MIN_REBOOT_INTERVAL_SEC,
insert_event, reboot_dish, notify) — об'єднано в `_reboot_for_update_ready`.
Виявлено й виправлено принагідно **другий екземпляр** того самого
reboot-loop бага, що вже виправлявся для watchdog-шляху: `last_reboot_ts`
оновлювався лише при `ok == True`, тому невдала спроба reboot при
update-ready стані не захищала від негайного повтору на наступному
циклі. Виправлено так само — оновлюється завжди, незалежно від
результату.

## Backup/restore розширено на параметри моніторингу (app/config.py)

`format_version` 1→2: `env_params` у backup містить лише **реально
перевизначені** (`overridden: True` з `config_editor.read_current_values()`)
env-параметри, не всі значення за замовчуванням — інакше відновлення
на іншій версії коду затерло б нові дефолти застарілими значеннями з
моменту створення backup. Restore записує їх через той самий
`config_editor.save_values()`, що й панель "Параметри моніторингу" —
застосовуються після перезапуску сервісів, не автоматично (щоб
restore не спричиняв неочікуваний рестарт сервісів сам по собі).

## Рефакторинг: усунення дублювання коду (webapp.py, telegram_notify.py)

`api_system_reboot`/`api_system_shutdown` (webapp.py) — спільна
логіка (виконати команду, журнал, Telegram) винесена в
`_execute_pi_power_action`, з параметризацією конкретних дієслівних
форм повідомлень (щоб текст лишався природним, не узагальненим).

`telegram_notify.append_signature()` — той самий патерн (перевірка
`get_signature_phrases_enabled()` + `_random_signature_phrase()` +
конкатенація) був продубльований тричі: у `send_message()` та двічі
в `telegram_bot.py` (`_send`, `_cmd_reboot_request`). Об'єднано в
один хелпер.

## Баг: StartLimitIntervalSec/StartLimitBurst у [Service] замість [Unit]

**Симптом**: systemd попереджав `Unknown key 'StartLimitIntervalSec'
in section [Service], ignoring` при кожному старті
`starlink-grpc-fetch.service`.

**Причина**: ці директиви ("Unit start rate limiting") належать до
розділу `[Unit]`, не `[Service]` — у неправильній секції systemd
просто ігнорує їх, обмеження кількості спроб перезапуску (5 за 30хв)
не діяло взагалі.

**Виправлення**: перенесено в `[Unit]`.

## fetch_starlink_grpc.sh: завантаження через eth0 насамперед

wlan0 (WiFi Starlink) навмисно має нижчий route-metric за eth0 (щоб
трафік до dish/router завжди йшов через WiFi) — але це ж означає, що
дефолтний маршрут для ЗОВНІШНЬОГО інтернету (завантаження
`starlink_grpc.py` з GitHub) теж намагається йти через wlan0 першим.
Якщо супутниковий канал Starlink саме недоступний (dish щойно
ввімкнувся, обслуговування, погода), інтернету через wlan0 немає
взагалі, попри робочий eth0 (домашня мережа). Виправлено: скрипт
пробує `curl --interface eth0` спочатку (якщо eth0 підключений), і
лише за невдачі падає на дефолтний маршрут.

## Telegram-сповіщення мовчали при відсутньому інтернеті на Starlink

Той самий root cause, що й для `fetch_starlink_grpc.sh`, але для
Python-коду: `requests.post`/`requests.get` до `api.telegram.org`
теж ідуть через дефолтний маршрут (wlan0), який не має інтернету,
коли супутниковий канал Starlink недоступний — саме тоді, коли
сповіщення про проблему найпотрібніші.

`telegram_notify._request_with_eth0_fallback()` — спільний хелпер:
звичайний запит спочатку, і при мережевій помилці - явний retry з
`requests.Session` + кастомний `HTTPAdapter`, що прив'язує сокет до
IP-адреси eth0 (`_get_eth0_ip()`, ioctl `SIOCGIFADDR`, Linux-специфічно).
Застосовано в `send_message()`, `test_connection()` (telegram_notify.py)
і `_api_call()` (telegram_bot.py, спільний для getUpdates/відповідей
на команди).
