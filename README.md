# Starlink Mini Monitor & Watchdog — Raspberry Pi Zero 2 W

Пристрій на базі RPi Zero 2 W, який:

1. Підключається до WiFi Starlink Mini як клієнт.
2. Опитує dish через локальний gRPC API (`192.168.100.1:9200`) — стан,
   сигнал, obstruction, throughput, uptime, кількість перезавантажень.
3. Зберігає історію в SQLite.
4. Автоматично детектує "зависання"/деградацію лінку і, за потреби,
   виконує **reboot dish** через gRPC (`Device/Handle` → `reboot`).
5. Автоматично перевіряє та встановлює **системні оновлення** самого
   RPi (unattended-upgrades + власний апдейтер для python-залежностей
   і коду цього проєкту з git).
6. Надає веб-інтерфейс (Flask + Chart.js) зі статистикою в реальному
   часі, історією та кнопкою ручного reboot.

## Важливо розуміти про мережу

Starlink Mini роздає власний WiFi (SSID зазвичай `STARLINK` або
кастомний). RPi Zero 2 W має **один** WiFi-радіомодуль, тож він може
або:

- **Варіант A (рекомендований):** підключатись як клієнт до WiFi
  Starlink Mini. Тоді Pi отримує адресу в підмережі `192.168.1.0/24`
  (Wi-Fi роутера Starlink) або, якщо у dish увімкнено bypass-режим,
  напряму бачить dish на `192.168.100.1`. У звичайному режимі (без
  bypass) до dish можна достукатись через роутер, бо роутер
  проксіює/маршрутизує запити на `192.168.100.1:9200` — це стандартна
  поведінка Starlink, підтверджена усіма основними community-тулами
  (`starlink-grpc-tools`, `DishyPy` тощо), які завжди звертаються
  саме на `192.168.100.1:9200`, незалежно від того, який роутер
  використовується — головне, щоб він міг маршрутизувати трафік до
  dish.
- **Варіант B:** якщо потрібен ще й доступ в інтернет/до вашої
  локальної мережі для оновлень системи — на Pi Zero 2 W це
  проблематично, бо WiFi-модуль один. Рішення: використати
  **USB-Ethernet адаптер** (Pi Zero 2 W має USB) для доступу в
  "звичайний" інтернет (оновлення), а WiFi лишити виключно для
  Starlink. Це найнадійніший варіант і саме він рекомендований нижче.

**Рекомендована схема:**
```
USB-Ethernet ──► Домашня мережа/інтернет (apt update, git pull)
WiFi (wlan0) ──► WiFi Starlink Mini (моніторинг + reboot dish)
```//
Якщо Ethernet недоступний, у розділі "Альтернатива без Ethernet" нижче
описано, як періодично перемикати wlan0 між двома мережами.

## Структура проєкту

```
starlink-monitor/
├── app/
│   ├── starlink_client.py     # gRPC клієнт (статус + reboot)
│   ├── db.py                  # SQLite шар
│   ├── monitor.py             # фоновий збір метрик + watchdog-логіка
│   ├── updater.py             # автооновлення системи/проєкту
│   ├── webapp.py              # Flask веб-інтерфейс
│   └── config.py              # конфігурація (пороги, інтервали)
├── templates/
│   └── index.html             # дашборд
├── static/
│   └── dashboard.js
├── systemd/
│   ├── starlink-monitor.service
│   ├── starlink-webui.service
│   └── starlink-updater.service + .timer
├── scripts/
│   └── install.sh
└── requirements.txt
```

## Встановлення (коротко)

```bash
git clone <your-repo-url> ~/starlink-monitor
cd ~/starlink-monitor
sudo bash scripts/install.sh
```

Скрипт install.sh:
- ставить системні пакети та Python venv
- налаштовує `wpa_supplicant`/`NetworkManager` для підключення до
  WiFi Starlink Mini на `wlan0`
- копіює systemd unit-файли й вмикає сервіси
- вмикає `unattended-upgrades` для авто-апдейтів ОС

Після встановлення дашборд доступний на `http://<ip-pi>:8080`.
