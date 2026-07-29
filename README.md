# 🛰️ Starlink Mini Monitor & Watchdog — Raspberry Pi Zero 2 W

Автономний монітор і watchdog для Starlink Mini на Raspberry Pi Zero 2 W.

**Зміст**: [Що робить](#що-робить) · [GPIO-кнопка](#фізична-кнопка-виключення-gpio) ·
[TFT-дисплей](#фізичний-tft-дисплей-st7789-spi) ·
[Telegram](#telegram-сповіщення-та-команди) · [Backup](#backuprestore-налаштувань) ·
[healthz/PWA/speedtest](#додатково-healthz-pwa-speedtest) · [Надійність](#надійність) ·
[Перевірка оновлень](#про-ручну-перевірку-оновлень) · [Мережа](#важливо-розуміти-про-мережу) ·
[Структура](#структура-проєкту) · [Встановлення](#встановлення-та-оновлення) ·
[Конфігурація](#конфігурація) · [Ліцензія](#ліцензія)

## 📋 Що робить

1. Підключається до WiFi Starlink Mini як клієнт.
2. Опитує dish (`192.168.100.1:9200`, кожні 10с) і router
   (`192.168.1.1:9000`, раз/хв) — Mini складається з ДВОХ логічних
   пристроїв в одному корпусі, кожен зі своєю прошивкою.
3. Auto-reboot усього Mini при watchdog-таймауті dish або готовому
   оновленні ПЗ (dish/router), зі спільним захистом від reboot-loop.
4. Збирає метрики Pi та історію Starlink в SQLite з автоочищенням.
5. Веб-дашборд (Flask + Chart.js): live-метрики, графіки, прошивки,
   WiFi-клієнти, останні 5 подій журналу, ручний reboot/перевірка
   оновлень, реальний speedtest поруч із заявленою швидкістю dish.
   Сторінка `/stats` — повний журнал подій і повна історія speedtest.
6. Telegram: сповіщення + команди `/status /reboot /id /help`.
7. Reboot/shutdown Pi з веб-інтерфейсу або фізичної GPIO-кнопки,
   backup/restore всіх налаштувань одним файлом, `/healthz` для
   зовнішнього моніторингу, встановлюваний як PWA.

## 🔘 Фізична кнопка виключення (GPIO)

Кнопка (нормально розімкнута) між обраним GPIO-піном (BCM) і GND,
внутрішній pull-up налаштовується сервісом. Вимкнено за замовчуванням
(`STARLINK_SHUTDOWN_BUTTON_PIN=0`); увімкнення — у
`/etc/starlink-monitor/env`:
```
STARLINK_SHUTDOWN_BUTTON_PIN=17
STARLINK_SHUTDOWN_BUTTON_HOLD_SEC=3
```
потім `sudo systemctl restart starlink-shutdown-button.service`.
Утримання довше заданого часу (типово 3с) → `systemctl poweroff`, з
подією в журналі й Telegram.

Якщо ввімкнено фізичний TFT-дисплей (`STARLINK_DISPLAY_ENABLED=1`) —
**та сама кнопка** отримує другу дію: **коротке** натискання
перемикає підсвітку дисплея, **довге** (як і раніше) вимикає Pi.
Обробляється тоді всередині `starlink-display.service`, а
`starlink-shutdown-button.service` сам себе вимикає (щоб не
конкурувати за той самий GPIO-пін).

## 🖥️ Фізичний TFT-дисплей (ST7789, SPI)

Показує live-статус dish (online/offline, uptime) прямо на екрані. Вимкнено за замовчуванням
(`STARLINK_DISPLAY_ENABLED=0`). Використовує бібліотеку **Adafruit
CircuitPython ST7789** (`adafruit-circuitpython-rgb-display` +
`adafruit-blinka`).

> ⚠️ SPI зазвичай вимкнений за замовчуванням на Raspberry Pi OS:
> `sudo raspi-config nonint do_spi 0 && sudo reboot`
> (`install.sh` попереджає про це наприкінці встановлення, якщо SPI
> ще не увімкнено).

Паспортна роздільна здатність модуля — 170×320 (SKU MSP1901,
driver ST7789, portrait), підтверджена офіційною документацією
LCDWIKI і написом на самій платі. Піни DC/RST/BL все одно залежать
від фактичного підключення до Pi — скоригуй за потреби через
`/settings` або `/etc/starlink-monitor/env`:
```
STARLINK_DISPLAY_ENABLED=1
STARLINK_DISPLAY_DC_PIN=25
STARLINK_DISPLAY_RST_PIN=24
STARLINK_DISPLAY_BL_PIN=18
STARLINK_DISPLAY_WIDTH=170
STARLINK_DISPLAY_HEIGHT=320
STARLINK_DISPLAY_ROTATION=0
```
потім `sudo systemctl restart starlink-display.service`. Якщо `BLK`
підключено напряму до 3.3V (не через GPIO Pi) — постав
`STARLINK_DISPLAY_BL_PIN=0` (без програмного керування підсвіткою).

> ⚠️ `SPI_CS_PIN` — це bit-banged GPIO (не апаратний CE0/CE1), бо
> бібліотека Adafruit CircuitPython ST7789 сама перемикає CS програмно.

Якщо зображення зміщене чи має шум по краях —
`STARLINK_DISPLAY_OFFSET_LEFT`/`STARLINK_DISPLAY_OFFSET_TOP` (дефолт
`0`) підбираються емпірично: типова потреба для дешевих ST7789-клонів,
де видима область менша за фізичний GRAM контролера. **Не міняй
місцями WIDTH/HEIGHT** — панель фізично portrait, поміняні місцями
значення командують контролеру стовпці поза межами реальної матриці
й спричиняють шум/спотворення.

Керування підсвіткою — фізичною кнопкою виключення (`STARLINK_SHUTDOWN_BUTTON_PIN`,
див. секцію вище): коротке натискання вмикає/вимикає підсвітку, довге
(як завжди) вимикає Pi. Автоматично вимикається через
`STARLINK_DISPLAY_BACKLIGHT_AUTO_OFF_SEC` (типово 60с) після
кожного ввімкнення — нічний режим/економія; `0` вимикає авто-off.

## 💬 Telegram-сповіщення та команди

Налаштовуються на сторінці `/settings` (перехід за іконкою ⚙ на
дашборді):
1. Створіть бота через [@BotFather](https://t.me/BotFather), отримайте bot token.
2. Дізнайтесь свій chat_id через [@userinfobot](https://t.me/userinfobot).
3. Вставте token і chat_id (через кому — кілька отримувачів), "Зберегти",
   потім "Надіслати тестове".
4. Увімкніть перемикач "Увімкнути сповіщення".

> ⚠️ Bot token зберігається в локальній SQLite (таблиця `settings`), у
> веб-інтерфейсі завжди замаскований.

**Команди** (лише для chat_id зі списку):
- `/status` — стан оновлення ПЗ dish/роутера, кількість попереджень
- `/reboot` — підтвердження через inline-кнопки, діє 2 хвилини
- `/id` — список підключених тарілок; `/id <ID або частина>` — версії
  ПЗ dish/router конкретної тарілки і час останнього оновлення
- `/help` — список команд

Long polling (без webhook) у потоці `starlink-monitor.service`.

Кожне повідомлення завершується випадковою фразою з
`app/signature_phrases.txt` (одна на рядок, редагується на `/settings`);
вимикається перемикачем "Додавати фразу підпису" там же.

**Особливості поведінки сповіщень:**
- Тиша при недоступності dish довше `STARLINK_NOTIFICATIONS_MUTE_AFTER`
  (15 хв) — Telegram-звіти про auto-reboot призупиняються (журнал і
  далі пишеться), відновлення завжди повідомляється з тривалістю простою
- Журнал watchdog-спроб reboot зупиняється після `STARLINK_MAX_LOGGED_FAILURES`
  (30) послідовних невдач — сама спроба reboot триває, лише запис
  припиняється, щоб не засмічувати журнал при багатогодинному збої
- Завжди приглушені (без Telegram, з журналом): помилка перевірки
  оновлення роутера, "оновлення очікує встановлення", dish-роумінг
- Цикл оновлення ПЗ dish повідомляється повністю: початок завантаження,
  готовність до перезавантаження, успішне завершення

## 💾 Backup/restore налаштувань

На `/settings`: "Завантажити backup" — JSON з bot token, chat_id,
auto-reboot, фразами підпису й перевизначеними параметрами моніторингу
(лише ті, що відрізняються від значень за замовчуванням); "Відновити
з backup" — застосовує файл (параметри моніторингу — після
перезапуску сервісів).

> ⚠️ Bot token у файлі — у відкритому вигляді, зберігай backup як secret.

## ➕ Додатково: healthz, PWA, speedtest

- **`GET /healthz`** — для зовнішнього моніторингу (UptimeRobot тощо):
  БД доступна + watchdog реально опитує dish (свіжість метрик < 3
  циклів опитування). `200 ok` / `503 degraded`, нічого не пише в
  журнал — безпечно опитувати часто.
- **Встановлюваний дашборд (PWA)** — "Додати на головний екран" у
  Chrome/Edge. Service worker кешує лише статику (CSS/JS/іконки),
  API-дані завжди наживо, ніколи не кешуються.
- **Реальний speedtest** — `downlink_mbps` з dish показує заявлену
  пропускну здатність каналу, не реальну користувацьку швидкість.
  Панель "Заявлена vs реальна швидкість" додає незалежний вимір через
  `speedtest-cli`. Вимкнено за замовчуванням (реальний трафік +
  навантаження WiFi-радіо); увімкни на `/settings`
  (`STARLINK_SPEEDTEST_ENABLED=1`, інтервал `STARLINK_SPEEDTEST_INTERVAL`,
  типово двічі/год). Кнопка "Запустити зараз" — для ручного тесту.

## 🛡️ Надійність

- **Watchdog для watchdog-а** — `starlink-monitor-healthcheck.timer`
  (раз/хв) опитує `/healthz`; якщо `starlink-monitor.service` завис
  (не crash, `Restart=always` цього не бачить) — примусовий restart.
- **VACUUM/ANALYZE SQLite** — раз на добу, автоматично.
- **Ротація журналу systemd** — `install.sh` обмежує `SystemMaxUse=200M`,
  щоб журнал не з'їв SD-картку за тривалий час роботи.

## 🔄 Про ручну перевірку оновлень

Кнопка "Перевірити стан оновлень" форсує негайне локальне опитування
dish/router. Локальний gRPC API не має команди "перевір оновлення в
хмарі SpaceX" (це робить хмарний бекенд офіційного застосунку) —
`software_update` повертає `FailedPrecondition`/`Unimplemented`, тож
кнопка робить максимум технічно можливого.

## 🌐 Важливо розуміти про мережу

Starlink Mini роздає власний WiFi. RPi Zero 2 W має один WiFi-модуль,
тож для одночасного доступу до Starlink і звичайного інтернету
потрібен або USB-Ethernet (рекомендовано — WiFi лишається виключно
для Starlink), або підключення лише до Starlink WiFi (моніторинг і
reboot dish не залежать від зовнішнього інтернету).

При використанні USB-Ethernet `install.sh` пропонує (лише при
першому встановленні) опційно налаштувати статичні IP для обох
інтерфейсів — за замовчуванням DHCP на обох може спричиняти конфлікти
маршрутів (dish/router стають недоступні, якщо домашня мережа
перетягує пріоритет default-маршруту).

**Системний WAN-failover**: сервіс `starlink-wan-failover.timer`
(кожні ~20с) перевіряє, чи `wlan0` реально має вихід в інтернет (не
лише локальний зв'язок з dish/router), і через `nmcli` динамічно
знижує пріоритет дефолтного маршруту `wlan0`, коли супутниковий канал
Starlink недоступний — `eth0` автоматично стає дефолтним для **всієї
системи** (apt, curl, будь-яка програма), не лише для нашого коду.
Маршрут до router (своя підмережа wlan0) не зачіпається автоматично;
маршрут до dish — окремий явний запис, доданий `install.sh`
(`nmcli +ipv4.routes`), незалежний від стану WAN-failover. Встановлюється
й вмикається автоматично разом з рештою сервісів.

## 🗂️ Структура проєкту

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
│   ├── display.py             # фізичний TFT-дисплей (ST7789, SPI)
│   ├── config.py              # конфігурація
│   ├── config_editor.py       # редагування env-параметрів через /settings
│   ├── speedtest_runner.py    # періодичний реальний speedtest
│   └── vendor/                # сюди завантажується starlink_grpc.py
├── templates/index.html, settings.html, stats.html
├── static/dashboard.js, settings.js, stats.js, pwa.js, sw.js, style.css,
│           logo.png, favicon.ico, manifest.json, icon-192.png, icon-512.png
├── systemd/
│   ├── starlink-monitor.service          # watchdog + метрики
│   ├── starlink-webui.service            # веб-інтерфейс
│   ├── starlink-shutdown-button.service  # GPIO-кнопка
│   ├── starlink-display.service          # TFT-дисплей (ST7789)
│   ├── starlink-grpc-fetch.service       # тягне starlink_grpc.py при старті
│   ├── starlink-wan-failover.service/.timer  # системний WAN-failover
│   └── starlink-monitor-healthcheck.service/.timer  # watchdog для watchdog-а
├── scripts/
│   ├── install.sh              # встановлення/оновлення (авто-детекція)
│   ├── update.sh                # ручне оновлення з архіву
│   ├── uninstall.sh             # повне видалення з Pi
│   ├── fetch_starlink_grpc.sh  # (пере)завантажити starlink_grpc.py
│   ├── wan_failover_check.sh   # системний WAN-failover (wlan0 vs eth0)
│   └── watchdog_healthcheck.sh # перевірка /healthz, force-restart при зависанні
└── requirements.txt
```

## 📦 Встановлення та оновлення

**📥 Перше встановлення:**
```bash
tar -xzf starlink-monitor.tar.gz && cd starlink-monitor
sudo bash scripts/install.sh
```
Ставить системні пакети, `grpcurl`, Python venv, sudo-права,
systemd-сервіси; наприкінці — опційний запит статичних IP (лише при
першому встановленні, див. секцію про мережу вище).

**🔁 Оновлення** (архів у домашньому каталозі, напр. через `scp`):
```bash
sudo bash /opt/starlink-monitor/scripts/update.sh
```
Розпаковує й викликає `install.sh` в update-режимі: системні пакети
не чіпаються, синхронізуються лише змінені файли,
`signature_phrases.txt` не перезаписується, мережевий запит не
повторюється. SHA-256 запобігає повторному встановленню того самого
архіву; шлях можна вказати аргументом.

**✅ Після встановлення:**
1. `sudo nmcli device wifi connect "<SSID>" password "<пароль>" ifname wlan0`
2. `starlink_grpc.py` завантажиться автоматично; прогрес:
   `journalctl -u starlink-grpc-fetch.service -f`
3. Дашборд: `http://<ip-пристрою>:8080`

**🗑️ Повне видалення:**
```bash
sudo bash /opt/starlink-monitor/scripts/uninstall.sh
```
Зупиняє сервіси, видаляє код і sudoers-правило. Історію метрик і
Telegram-налаштування видаляє лише після окремого підтвердження — за
замовчуванням лишаються для повторного встановлення.

## ⚙️ Конфігурація

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
| `STARLINK_NOTIFICATIONS_MUTE_AFTER` | `900` | приглушити Telegram-звіти при безперервній недоступності dish, сек |
| `STARLINK_MAX_LOGGED_FAILURES` | `30` | макс. послідовних невдач опитування, що пишуться в журнал/БД |
| `STARLINK_OBSTRUCTION_WARN` | `0.05` | поріг попередження про перешкоди (0-1) |
| `STARLINK_AUTO_REBOOT_ON_UPDATE` | `1` | авто-reboot dish коли оновлення готове до встановлення |
| `STARLINK_HISTORY_DAYS` | `30` | скільки днів зберігати історію метрик/подій |
| `STARLINK_WEBUI_PORT` | `8080` | порт веб-інтерфейсу |
| `STARLINK_SHUTDOWN_BUTTON_PIN` | `0` | GPIO-пін фізичної кнопки виключення (BCM), 0=вимкнено |
| `STARLINK_SHUTDOWN_BUTTON_HOLD_SEC` | `3` | скільки секунд утримувати кнопку перед виключенням |
| `STARLINK_DISPLAY_ENABLED` | `0` | фізичний TFT-дисплей статусу (0/1) |
| `STARLINK_DISPLAY_SPI_CS_PIN` | `8` | дисплей: GPIO-пін CS (bit-banged) |
| `STARLINK_DISPLAY_DC_PIN` | `25` | дисплей: GPIO-пін DC |
| `STARLINK_DISPLAY_RST_PIN` | `24` | дисплей: GPIO-пін RES |
| `STARLINK_DISPLAY_BL_PIN` | `18` | дисплей: GPIO-пін підсвітки (0=не керувати) |
| `STARLINK_DISPLAY_WIDTH` | `170` | дисплей: ширина, px |
| `STARLINK_DISPLAY_HEIGHT` | `320` | дисплей: висота, px |
| `STARLINK_DISPLAY_ROTATION` | `0` | дисплей: поворот, ° (0/180; 90/270 лише якщо width=height) |
| `STARLINK_DISPLAY_OFFSET_LEFT` | `0` | дисплей: зміщення X відносно GRAM, px |
| `STARLINK_DISPLAY_OFFSET_TOP` | `0` | дисплей: зміщення Y відносно GRAM, px |
| `STARLINK_DISPLAY_REFRESH_SEC` | `5` | дисплей: інтервал оновлення, сек |
| `STARLINK_DISPLAY_SPI_SPEED_HZ` | `40000000` | дисплей: швидкість SPI, Гц |
| `STARLINK_DISPLAY_BACKLIGHT_AUTO_OFF_SEC` | `60` | дисплей: автовимкнення підсвітки, сек (0=вимк.) |
| `STARLINK_SPEEDTEST_ENABLED` | `0` | періодичний реальний speedtest (0/1) |
| `STARLINK_SPEEDTEST_INTERVAL` | `1800` | інтервал між speedtest-прогонами, сек |

Параметри читаються один раз при старті процесів — після збереження
на `/settings` натисни "Зберегти й перезапустити сервіси" (кнопка
поруч), або вручну:
```bash
sudo systemctl restart starlink-monitor.service starlink-webui.service
```

## 📄 Ліцензія

© 2026 JunioR. Розповсюджується під ліцензією MIT — див. файл
[`LICENSE`](./LICENSE).

Проєкт використовує `starlink_grpc.py` зі стороннього репозиторію
[sparky8512/starlink-grpc-tools](https://github.com/sparky8512/starlink-grpc-tools),
який завантажується окремо (`scripts/fetch_starlink_grpc.sh`) і не входить
до складу цього репозиторію — його ліцензійні умови визначає власний автор.
