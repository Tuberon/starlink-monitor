#!/usr/bin/env bash
# Системний WAN-failover: періодично перевіряє, чи wlan0 (WiFi Starlink)
# реально має вихід в інтернет (не лише локальний зв'язок з dish/router),
# і динамічно коригує metric дефолтного маршруту, щоб eth0 (USB-Ethernet)
# автоматично ставав пріоритетним для ВСІЄЇ системи (apt, curl, будь-яка
# інша програма), коли супутниковий канал Starlink недоступний.
#
# На відміну від eth0-fallback у Python-коді проєкту (app/telegram_notify.py -
# спрацьовує лише для конкретних HTTP-запитів до Telegram API), це -
# system-wide рівень: коригує саму таблицю маршрутизації ядра, тому working
# для будь-якої програми в системі, не лише для нашого коду.
#
# Специфічні маршрути до dish (192.168.100.0/24) і router (192.168.1.0/24)
# НЕ зачіпаються - це окремі, більш конкретні маршрути, які завжди
# пріоритетніші за будь-який дефолтний маршрут незалежно від metric.
# Змінюється лише пріоритет ДЕФОЛТНОГО маршруту (для решти інтернету).
#
# Запускається через systemd timer (starlink-wan-failover.timer, кожні ~20с).
set -euo pipefail

WLAN_IFACE="wlan0"
CHECK_HOST="1.1.1.1"
CHECK_TIMEOUT=3
NORMAL_WLAN_METRIC=50
DEMOTED_WLAN_METRIC=9999

# Усі поточні default-маршрути через wlan0 (може бути кілька рядків -
# ВАЖЛИВО: `ip route replace ... metric X` шукає точний збіг ЗА metric
# теж, тому якщо раніше такого запису не було - команда ДОДАЄ новий,
# не замінюючи старий. Це реальний баг попередньої версії скрипта:
# накопичувались дублікати (metric=50 і metric=9999 одночасно), і
# ядро завжди обирало НАЙМЕНШИЙ - тобто wlan0 фактично ніколи не
# демотувався насправді, попри лог про "успіх". Тому тут явно
# видаляємо ВСІ default-маршрути на wlan0 перед додаванням одного
# чистого запису - і це ж самозцілює вже накопичені дублікати.
mapfile -t WLAN_ROUTES < <(ip route show default dev "$WLAN_IFACE" 2>/dev/null || true)
if [[ "${#WLAN_ROUTES[@]}" -eq 0 ]]; then
  # wlan0 не має дефолтного маршруту взагалі (не підключений до WiFi
  # Starlink чи щойно піднімається) - нічого коригувати, вихід.
  exit 0
fi

WLAN_GATEWAY="$(echo "${WLAN_ROUTES[0]}" | grep -oP 'via \K[0-9.]+' || true)"
if [[ -z "$WLAN_GATEWAY" ]]; then
  # Неочікуваний формат рядка маршруту - краще нічого не чіпати, ніж
  # зламати мережу невірною командою.
  exit 0
fi

if ping -c 1 -W "$CHECK_TIMEOUT" -I "$WLAN_IFACE" "$CHECK_HOST" >/dev/null 2>&1; then
  TARGET_METRIC="$NORMAL_WLAN_METRIC"
  TARGET_LABEL="wlan0 (Starlink) знову має інтернет - відновлюю пріоритет"
else
  TARGET_METRIC="$DEMOTED_WLAN_METRIC"
  TARGET_LABEL="wlan0 (Starlink) без інтернету - знижую пріоритет, eth0 стає дефолтним для всієї системи"
fi

# "Чистий" стан - рівно один default-маршрут на wlan0 із потрібним
# metric. Якщо це вже так - нічого не робимо (не смикаємо таблицю
# маршрутів зайвий раз кожні 20с). Інакше - видаляємо ВСІ наявні
# default-маршрути на wlan0 (цикл, бо кожен `ip route del` видаляє
# лише один matching запис за раз) і додаємо один чистий.
if [[ "${#WLAN_ROUTES[@]}" -eq 1 ]] && echo "${WLAN_ROUTES[0]}" | grep -q "metric $TARGET_METRIC\b"; then
  exit 0
fi

echo "==> $TARGET_LABEL (metric=$TARGET_METRIC)"
while ip route del default dev "$WLAN_IFACE" 2>/dev/null; do :; done
ip route add default via "$WLAN_GATEWAY" dev "$WLAN_IFACE" metric "$TARGET_METRIC" 2>/dev/null || true
