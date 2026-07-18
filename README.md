# Starlink Mini Monitor & Watchdog — Raspberry Pi Zero 2 W

Пристрій на базі RPi Zero 2 W, який:

1. Підключається до WiFi Starlink Mini як клієнт.
2. Опитує dish (`192.168.100.1:9200`, кожні 10с) і router
   (`192.168.1.1:9000`, раз/хв) — Mini складається з ДВОХ логічних
   пристроїв в одному корпусі, кожен зі своєю прошивкою.
3. Автоматично перезавантажує весь Mini (dish і router — один фізичний
   пристрій) при: watchdog-таймауті dish, готовому оновленні ПЗ dish
   (`REBOOT_REQUIRED`) або router (`REBOOT_PENDING`), зі спільним
   захистом від reboot-loop.
4. Збирає системні метрики Pi (CPU/RAM/диск/температура) і зберігає
   всю історію в SQLite з автоочищенням.
5. Надає веб-дашборд (Flask + Chart.js): live-метрики, графіки,
   прошивки/ID тарілки й роутера, список WiFi-клієнтів роутера,
   журнал подій з дедублікацією, ручний reboot і перевірка оновлень.
6. Дублює ключові події в Telegram і приймає команди `/status /reboot
   /id /help` — деталі нижче.
7. Дозволяє reboot/shutdown самого Pi з веб-інтерфейсу або фізичною
   GPIO-кнопкою — деталі нижче.
8. Дозволяє backup/restore усіх налаштувань одним JSON-файлом —
   деталі нижче.

## Фізична кнопка виключення (GPIO)

Дозволяє коректно вимкнути Pi апаратною кнопкою, без веб-інтерфейсу
чи SSH.

**Підключення**: кнопка (нормально розімкнута) між обраним GPIO-піном
(BCM) і GND. Внутрішній pull-up налаштовується сервісом.

**Активація**: за замовчуванням вимкнено (`STARLINK_SHUTDOWN_BUTTON_PIN=0`).
Щоб увімкнути, додай у `/etc/starlink-monitor/env`:
```
STARLINK_SHUTDOWN_BUTTON_PIN=17
STARLINK_SHUTDOWN_BUTTON_HOLD_SEC=3
```
(приклад для GPIO17 — фізичний пін 11), потім:
```bash
sudo systemctl restart starlink-shutdown-button.service
```

**Поведінка**: утримання довше `STARLINK_SHUTDOWN_BUTTON_HOLD_SEC`
секунд (типово 3с) → `systemctl poweroff`, з подією в журналі й
Telegram. Короткий дотик ігнорується.

## Telegram-сповіщення та команди

Налаштовуються на сторінці `/settings` (перехід за іконкою ⚙ на
дашборді):
1. Створіть бота через [@BotFather](https://t.me/BotFather), отримайте bot token.
2. Дізнайтесь свій chat_id через [@userinfobot](https://t.me/userinfobot).
3. Вставте token і chat_id (через кому — кілька отримувачів), "Зберегти",
   потім "Надіслати тестове".
4. Увімкніть перемикач "Увімкнути сповіщення".

Bot token зберігається в локальній SQLite (таблиця `settings`), у
веб-інтерфейсі завжди замаскований.

**Команди** (лише для chat_id зі списку):
- `/status` — стан оновлення ПЗ dish/роутера, кількість попереджень
- `/reboot` — підтвердження через inline-кнопки, діє 2 хвилини
- `/id` — список підключених тарілок; `/id <ID або частина>` — версії
  ПЗ dish/router конкретної тарілки і час останнього оновлення
- `/help` — список команд

Long polling (без webhook) у потоці `starlink-monitor.service`.

Кожне повідомлення завершується випадковою фразою з
`app/signature_phrases.txt` (одна на рядок) — редагується на сторінці
`/settings` або вручну у файлі. Додавання фраз можна вимкнути
перемикачем "Додавати фразу підпису" там же.

## Backup/restore налаштувань

На сторінці `/settings`:
- **"Завантажити backup"** — JSON з bot token, chat_id, auto-reboot,
  фразами підпису
- **"Відновити з backup"** — застосовує раніше збережений файл

Файл містить bot token у відкритому вигляді — зберігай як secret.

## Про ручну перевірку оновлень

Кнопка "Перевірити стан оновлень" негайно опитує dish і router,
минаючи фоновий цикл.

Локальний gRPC API **не має** команди "перевір оновлення в хмарі
SpaceX" — офіційний застосунок робить це через хмарний бекенд, не
локально. Виклик `software_update` повертає `FailedPrecondition:
Sideload update stream not open` на dish (метод для sideload-
завантаження прошивки вручну, не для перевірки) і `Unimplemented` на
router. Кнопка на дашборді робить максимум технічно можливого —
форсує негайне локальне опитування.

## Важливо розуміти про мережу

Starlink Mini роздає власний WiFi (SSID зазвичай `STARLINK` або
кастомний). RPi Zero 2 W має **один** WiFi-радіомодуль, тож для
доступу і до Starlink Mini, і до звичайного інтернету (для
оновлень) знадобиться або:

- **Варіант A (рекомендований):** USB-Ethernet адаптер для доступу в
  "звичайний" інтернет, а WiFi лишити виключно для підключення до
  Starlink Mini.
  ```
  USB-Ethernet ──► Домашня мережа/інтернет
  WiFi (wlan0) ──► WiFi Starlink Mini (моніторинг + reboot dish)
  ```
- **Варіант B:** підключатись лише до WiFi Starlink Mini. Основний
  функціонал моніторингу й reboot dish не залежить від доступності
  зовнішнього інтернету.

При Варіанті A обидва інтерфейси за замовчуванням отримують адресу по
DHCP, що може спричиняти конфлікти маршрутів (dish/router стають
недоступні, якщо домашня мережа перетягує на себе пріоритет
default-маршруту). `install.sh` пропонує опційно налаштувати статичні
IP для обох інтерфейсів (за замовчуванням: eth0 `192.168.0.95/24`,
gateway `192.168.0.1`; wlan0 `192.168.1.95/24`, gateway `192.168.1.1`,
з нижчим route-metric — вищим пріоритетом за eth0), і вимикає системний
`dhcpcd`, якщо він конфліктує з NetworkManager. Значення можна змінити
під час встановлення або пропустити крок і налаштувати вручну пізніше.

## Структура проєкту

```
starlink-monitor/
├── app/
│   ├── starlink_client.py     # gRPC клієнт: dish + router, reboot
│   ├── system_metrics.py      # метрики Pi (CPU/RAM/диск/темп.)
│   ├── labels.py              # людські назви станів/попереджень
│   ├── db.py                  # SQLite (metrics, events, system_metrics)
│   ├── monitor.py             # опитування + watchdog-логіка
│   ├── telegram_notify.py     # відправка сповіщень Telegram
│   ├── telegram_bot.py        # команди /status /reboot /id
│   ├── signature_phrases.txt  # фрази в кінці Telegram-повідомлень
│   ├── webapp.py              # Flask + REST API
│   ├── shutdown_button.py     # фізична кнопка виключення (GPIO)
│   ├── config.py              # конфігурація
│   ├── config_editor.py       # редагування env-параметрів через /settings
│   └── vendor/                # сюди завантажується starlink_grpc.py
├── templates/index.html, settings.html
├── static/dashboard.js, settings.js, style.css, logo.png
├── systemd/
│   ├── starlink-monitor.service          # watchdog + метрики
│   ├── starlink-webui.service            # веб-інтерфейс
│   ├── starlink-shutdown-button.service  # GPIO-кнопка
│   └── starlink-grpc-fetch.service       # тягне starlink_grpc.py при старті
├── scripts/
│   ├── install.sh              # встановлення/оновлення (авто-детекція)
│   ├── update.sh                # ручне оновлення з архіву
│   ├── uninstall.sh             # повне видалення з Pi
│   └── fetch_starlink_grpc.sh  # (пере)завантажити starlink_grpc.py
└── requirements.txt
```

## Встановлення та оновлення

**Перше встановлення:**
```bash
tar -xzf starlink-monitor.tar.gz
cd starlink-monitor
sudo bash scripts/install.sh
```
Ставить системні пакети, `grpcurl`, Python venv, sudo-права, systemd-сервіси.
Наприкінці (лише при першому встановленні) — опційний запит на
налаштування статичних IP для eth0/wlan0 (див. секцію про мережу вище).

**Оновлення до нової версії** (архів `starlink-monitor.tar.gz` у
домашньому каталозі, напр. через `scp`):
```bash
sudo bash scripts/update.sh
```
Розпаковує архів і викликає `install.sh` — той сам визначає режим
оновлення: системні пакети не чіпаються, синхронізуються лише змінені
файли, `requirements.txt`/systemd-юніти оновлюються лише за потреби,
`app/signature_phrases.txt` не перезаписується, запит про мережу не
показується повторно. SHA-256-перевірка запобігає повторному
встановленню того самого архіву; шлях можна вказати аргументом
(`update.sh /шлях/до/архіву.tar.gz`).

**Після встановлення:**
1. Підключіть `wlan0` до WiFi Starlink Mini:
   `sudo nmcli device wifi connect "<SSID>" password "<пароль>" ifname wlan0`
2. `starlink_grpc.py` завантажиться автоматично (`starlink-grpc-fetch.service`);
   прогрес: `journalctl -u starlink-grpc-fetch.service -f`
3. Дашборд: `http://<ip-пристрою>:8080`

**Повне видалення:**
```bash
sudo bash scripts/uninstall.sh
```
Зупиняє й видаляє сервіси, sudoers-правило, код проєкту. Історію
метрик і налаштування Telegram (`/var/lib/starlink-monitor`,
`/etc/starlink-monitor`) видаляє лише після окремого підтвердження —
за замовчуванням лишаються, тож повторне встановлення підхопить стару
історію й налаштування автоматично.

## Конфігурація

Редагується на сторінці `/settings` (панель "Параметри моніторингу")
або вручну в `/etc/starlink-monitor/env`. Повний список — у
`app/config.py`. Найважливіші:

| Змінна | За замовчуванням | Опис |
|---|---|---|
| `STARLINK_DISH_ADDR` | `192.168.100.1:9200` | адреса тарілки |
| `STARLINK_ROUTER_ADDR` | `192.168.1.1:9000` | адреса роутерного компонента Mini |
| `STARLINK_POLL_INTERVAL` | `10` | інтервал опитування dish, сек |
| `STARLINK_MAX_FAILURES` | `6` | скільки невдалих опитувань перед watchdog-reboot |
| `STARLINK_MIN_REBOOT_INTERVAL` | `120` | мін. інтервал між авто-ребутами dish, сек |
| `STARLINK_AUTO_REBOOT_ON_UPDATE` | `1` | авто-reboot dish коли оновлення готове до встановлення |
| `STARLINK_WEBUI_PORT` | `8080` | порт веб-інтерфейсу |
| `STARLINK_SHUTDOWN_BUTTON_PIN` | `0` | GPIO-пін фізичної кнопки виключення (BCM), 0=вимкнено |
| `STARLINK_SHUTDOWN_BUTTON_HOLD_SEC` | `3` | скільки секунд утримувати кнопку перед виключенням |

Параметри читаються один раз при старті процесів — після збереження
на `/settings` натисни "Зберегти й перезапустити сервіси" (кнопка
поруч), або вручну:
```bash
sudo systemctl restart starlink-monitor.service starlink-webui.service
```

## Ліцензія

© 2026 JunioR. Розповсюджується під ліцензією MIT — див. файл
[`LICENSE`](./LICENSE).

Проєкт використовує `starlink_grpc.py` зі стороннього репозиторію
[sparky8512/starlink-grpc-tools](https://github.com/sparky8512/starlink-grpc-tools),
який завантажується окремо (`scripts/fetch_starlink_grpc.sh`) і не входить
до складу цього репозиторію — його ліцензійні умови визначає власний автор.
