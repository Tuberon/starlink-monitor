# Індекс проєкту

Навігатор по файлах Starlink Monitor. Детальний технічний опис —
[`architecture.md`](architecture.md); історія рішень і виправлених
багів — [`decisions-log.md`](decisions-log.md); загальний
опис/встановлення — [`../README.md`](../README.md).

## Корінь

| Файл | Опис |
|---|---|
| `README.md` | Опис проєкту, встановлення, конфігурація, усі env-параметри |
| `README.en.md` | Той самий README англійською - синхронізувати вручну при зміні README.md |
| `requirements.txt` | Python-залежності (pip), встановлюються на Pi |
| `constraints.txt` | Закріплені ТРАНЗИТИВНІ версії (urllib3, idna, Werkzeug...): `pip install -r requirements.txt -c constraints.txt`; апаратні adafruit-* свідомо відсутні |
| `requirements-dev.txt` | Залежності розробки (mypy, pytest, ruff, pip-audit, html5lib) - НЕ встановлюються на Pi |
| `mypy.ini` | Конфігурація статичної типізації: суворий режим для `app/*`, м'який для `tests/`, `app/vendor/` виключено |
| `pytest.ini` | Мінімальна конфігурація pytest (`testpaths = tests`) |
| `ruff.toml` | Правила `ruff check` (F, B, E9, PLE, S; виключає `app/vendor/`); без `ruff format` |
| `.gitignore` | `__pycache__/`, `.mypy_cache/`, `.pytest_cache/`, `*.db` — не мають потрапляти в архів |
| `LICENSE` | Ліцензія |

## `app/` — Python-модулі

| Файл | Опис |
|---|---|
| `starlink_client.py` | gRPC-клієнт: статус dish/router, `reboot_dish()`; vendored gRPC-стек вантажиться ліниво (`_grpc_module()`) |
| `monitor.py` | Watchdog: цикл опитування, авто-reboot, логування подій, запуск Telegram-бота (точка входу) |
| `services.py` | Спільна логіка для monitor/webapp/telegram_bot: відстеження версій прошивок (`check_updates_now`, `upsert_*`, `check_*_targets_reached`), бекапи (`build_backup_dict`, `perform_auto_backup`, `send_latest_backup_to_telegram`, `check_db_integrity_and_notify`) |
| `webapp.py` | Flask, REST API, роздає `/`, `/settings`, `/stats`, `/healthz`; відхиляє POST із чужого сайту (CSRF), у JSON немає `Infinity`/`NaN` |
| `db.py` | SQLite: metrics, events, system_metrics, router_status, known_devices, settings; атомарні `claim_setting`/`BEGIN IMMEDIATE`, "останній" запис ігнорує рядки з майбутнього |
| `i18n.py` | Мультимовний інтерфейс (uk/en) - словник перекладів, `t()`, мова з settings |
| `telegram_notify.py` | Вихідні сповіщення |
| `telegram_bot.py` | Вхідні команди `/status`, `/checkupdates`, `/reboot`, `/id`, `/help` (`/start` = `/help`) |
| `atomic_io.py` | Атомарний запис файлів (тимчасовий файл → fsync → os.replace), права існуючого файлу зберігаються; для env і автобекапів |
| `labels.py` | Таблиці "код -> ключ перекладу" для станів оновлення й alert-прапорців (monitor.py + telegram_bot.py), `short_error()` для чату |
| `log_redact.py` | Очищення токена бота з текстів помилок і логів (`redact()`, `RedactingFilter`, `install()`) |
| `system_metrics.py` | Метрики Pi (CPU/RAM/диск/температура) |
| `shutdown_button.py` | Фізична кнопка виключення через GPIO (окремий процес) |
| `pi_power.py` | Спільний reboot/poweroff, `shutdown_from_button()`, DB-сигнал для дисплея |
| `activity_led.py` | Опційний LED активності SD-картки, частина monitor.py (не окремий процес) |
| `display.py` | Фізичний TFT-дисплей статусу (ST7789, SPI, окремий процес); `DisplayController.tick()` — логіка однієї ітерації без заліза |
| `gpio_utils.py` | Спільна gpiod v1/v2-логіка читання GPIO (shutdown_button.py + display.py) і запису GPIO (activity_led.py) |
| `config.py` | Конфігурація, env-змінні |
| `config_editor.py` | Читання/валідація (`_VALIDATORS`, `LIMITS`, формат адрес)/атомарний запис `/etc/starlink-monitor/env` через `/settings` |
| `vendor/starlink_grpc.py` | Vendored (не наш код) — gRPC-хелпери з sparky8512/starlink-grpc-tools (Unlicense) |
| `vendor/PROVENANCE` | Походження vendored файлу: upstream-коміт, дата, sha256 (тест звіряє з файлом); пише `fetch_starlink_grpc.sh` |

## `static/` — фронтенд

| Файл | Опис |
|---|---|
| `common.js` | Спільні для dashboard.js/stats.js: `fmtTime`, `fmtAgo`, `fmtDateHeader` |
| `theme.js` | Темна/світла тема - toggle на /settings, підключено на всіх 3 сторінках |
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
| `stats.html` | Сторінка статистики (журнал подій) |

## `systemd/` — unit-файли

| Файл | Опис |
|---|---|
| `starlink-monitor.service` | Watchdog + Telegram-бот |
| `starlink-webui.service` | Flask dashboard |
| `starlink-shutdown-button.service` | Слухає GPIO-кнопку виключення |
| `starlink-display.service` | Фізичний TFT-дисплей |
| `starlink-grpc-fetch.service` | Опційне ручне оновлення vendored `starlink_grpc.py` (не автоматичне) |
| `starlink-wan-failover.service`/`.timer` | Періодична перевірка інтернету через wlan0 |
| `starlink-monitor-healthcheck.service`/`.timer` | Раз/хв опитує `/healthz`, force-restart при зависанні |

## `scripts/` — bash

| Файл | Опис |
|---|---|
| `install.sh` | Повне встановлення (системні пакети, venv, sudo-права, systemd); після кроків від користувача сервісів каталог проєкту й `scripts/` стають власністю root |
| `update.sh` | Оновлення вже встановленого проєкту |
| `uninstall.sh` | Повне видалення |
| `fetch_starlink_grpc.sh` | Опційне оновлення vendored `starlink_grpc.py`: тимчасовий файл → перевірки (розмір, синтаксис, ChannelContext/get_status) → `.prev` → атомарна заміна; `--commit=<sha>`; пише `PROVENANCE` |
| `audit_deps.sh` | Аудит вразливостей закріплених залежностей (`pip-audit` по requirements.txt + constraints.txt) |
| `wan_failover_check.sh` | Перевірка інтернету через wlan0, коригування route-metric |
| `watchdog_healthcheck.sh` | Перевірка `/healthz`, force-restart при не-200 |

## `tests/` — pytest

| Файл | Опис |
|---|---|
| `conftest.py` | Спільні fixtures: autouse-ізоляція робочих шляхів Pi (env, бекапи, БД за замовчуванням → tmp) для КОЖНОГО тесту; `db_path` (ізольована БД), `watchdog` (Watchdog з mock `_notify`, `router_reachable=True`) |
| `test_monitor.py` | Reboot-спам, дедублікація target-версій, update-state/alerts (dish+router), auto-reboot логіка |
| `test_monitor_run_forever.py` | Головний watchdog-цикл: усі періодичні таймери, стійкість до збою запису в БД, РЕАЛЬНИЙ обробник SIGTERM, пауза при поганому інтервалі, фонова відправка |
| `test_webapp.py` | Компаратор версій прошивки, `/api/target-versions`; основні status-endpoints, `/healthz` except-гілки, `/api/telegram-test` |
| `test_dependencies.py` | Піни requirements/constraints (точні, без дублів, без adafruit), версії urllib3/idna не нижче виправлених, install.sh передає `-c constraints.txt`, логіка `REQ_CHANGED` (СПРАВЖНІЙ фрагмент скрипту у 4 сценаріях) |
| `test_vendor_fetch.py` | `PROVENANCE` збігається з файлом побайтово; `fetch_starlink_grpc.sh` проти ЛОКАЛЬНОГО сервера: успіх, обірване/замале/без контракту завантаження відхиляється без зміни робочого файлу й без залишків, `.prev`, `--commit`, стрічка недоступна |
| `test_docs.py` | Охоронні перевірки документації: код-спани не розірвані посеред ідентифікатора, посилання `модуль.функція()` ведуть на існуючий код, паритет двох README, покриття файлів у index.md, зміст README збігається із заголовками, згадані в README файли/змінні/тести існують, команди Telegram збігаються з ботом, твердження розділу «Безпека» звірені з install.sh, юнітами й конфігом, таблиця модулів в architecture.md = модулі app/, команди бота в README/architecture/index/plan = бот, дерево проєкту в README = корінь, структура двох README збігається по розділах |
| `test_services.py` | Сервісна логіка (app/services.py) через фікстуру `sink` без Watchdog: target-версії, напрямок зміни прошивки, бекапи, цілісність БД (переклад коду причини), ідентифікатори компонентів |
| `test_architecture.py` | Граф імпортів між модулями app/: без циклів (навіть лінивих), точки входу не є бібліотеками, `db` не залежить від шару подання |
| `test_atomic_io.py` | Атомарний запис: обрив посеред запису не чіпає старий файл, збій на кожному кроці без тимчасового сміття, права файлу, `save_values` і автобекап |
| `test_install_security.py` | Власність root для root-скриптів: root-юніти виконують лише `scripts/`, блок `root-owned-scripts` стоїть після кроків від RUN_USER, справжні атаки від nobody (переписати скрипт, додати файл, підмінити каталог) до блоку вдаються, після ні (потрібен root) |
| `test_labels.py` | Повнота таблиць міток: кожен ключ існує в обох мовах, кожен alert-прапорець dish/роутера має мітку, невідомий код повертається як є, мітки слідують за мовою, переклад лише потрібного коду |
| `test_log_redact.py` | Токен бота не потрапляє в логи/повідомлення/БД: `redact()`, фільтр логера (з traceback), наскрізні шляхи помилок Telegram (шар джерела і захисна сітка перевіряються незалежно), AST-гарантія для точок входу |
| `test_i18n.py` | Цілісність перекладів (паритет плейсхолдерів uk/en, наявність кожного ключа з коду), `get_language()` при зламаній БД, англійські Telegram-сповіщення реальними шляхами коду |
| `test_system_metrics.py` | Кожна метрика (uptime/cpu/memory/disk/temp) незалежно, ніколи не кидає виняток навіть при повному провалі psutil |
| `test_pi_power.py` | DB-сигнал записаний ДО затримки, очищений ДО systemctl-команди (успіх і провал однаково); lazy notify_fn default, edge cases (subprocess-виняток, DB-провал не блокує реальну дію) |
| `test_telegram_notify.py` | HTTP-запити, retry; eth0-fallback (усі 3 рівні: звичайний/eth0/manual DNS через eth0); send_document (backup-файл) |
| `test_telegram_bot.py` | /checkupdates диспетчеризація; _poll_once/групування за chat_id; _run_loop/start/stop (SystemExit-трюк) |
| `test_db.py` | Prune старих метрик, `check_integrity()`, callback-хук LED активності |
| `test_display.py` | update_state/auto-off/power-message + _redraw, _load_font, _truncate_to_width, _set_backlight |
| `test_activity_led.py` | Неблокуюче blink/close, послідовні виклики без блимання, callback-помилки не поширюються |
| `test_gpio_utils.py` | find_gpio_chip, ButtonPressTracker (short/long_press), open_input/output_line через fake gpiod v1/v2 |
| `test_shutdown_button.py` | Фізична кнопка вимкнення: пін 0/відсутній gpiod/збій GPIO — вихід без падіння; довге/коротке натискання; поступається дисплею при `DISPLAY_ENABLED`; зламана БД не блокує вимкнення |
| `test_display_run_forever.py` | Повна ініціалізація дисплея через fake CircuitPython-модулі, stop_event, pending-shutdown, кнопка, auto-off |
| `test_config_editor.py` | Запис env-файлу: валідація типів І меж (nan/inf/0/-5, дефолти й реальні перевизначення в межах), атомарність, коментарі, звірка з config.py, заборона exec/eval |
| `test_starlink_client.py` | Парсинг gRPC-відповіді dish (enum-мапінг, getattr-fallback'и, конвертація одиниць) і router (subprocess+JSON шлях, snake→camelCase, clients, enum і числом/рядком/ім'ям), `reboot_dish()` (точна grpcurl-команда, усі гілки помилок) |

## `docs/` — документація

| Файл | Опис |
|---|---|
| `architecture.md` | Детальний технічний опис усіх компонентів |
| `decisions-log.md` | Історичний журнал рішень і виправлених багів |
| `plan.md` | Початковий план проєкту |
| `index.md` | Цей файл |
