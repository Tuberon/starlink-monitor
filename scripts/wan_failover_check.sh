#!/usr/bin/env bash
# Системний WAN-failover: періодично перевіряє, чи wlan0 (WiFi Starlink) реально має інтернет (не
# лише зв'язок з dish/router), і коригує metric дефолтного маршруту, щоб eth0 (USB-Ethernet) став
# пріоритетним для ВСІЄЇ системи (apt, curl, будь-яка програма), коли супутниковий канал
# недоступний.
# Керування через nmcli/NetworkManager, НЕ через `ip route`: wlan0 має persistent
# `ipv4.route-metric` (з install.sh), а пряма зміна таблиці в обхід NetworkManager викликала
# нескінченну "боротьбу" (він щоразу відновлював metric=50). `nmcli connection modify` + `nmcli
# device reapply` змінюють саму конфігурацію з'єднання без конфлікту.
# Маршрут до router (192.168.1.0/24) — власна підмережа wlan0, kernel-запис, завжди пріоритетніший.
# Маршрут до dish (192.168.100.0/24) такого запису НЕ має — install.sh додає його явно (`nmcli
# +ipv4.routes`), щоб він не залежав від змін тут.
# Запускається systemd-таймером starlink-wan-failover.timer (кожні ~30 с).
# ГІСТЕРЕЗИС: кожен `nmcli device reapply` на мить розриває маршрут до dish (192.168.100.1) — хибні
# "Dish недоступний" у журналі й Telegram і вплив на наступну ping-перевірку (reapply сам породжує
# наступний "провал" і знову тригерить reapply). Тому перемикання — лише після REQUIRED_CONSECUTIVE
# однакових результатів поспіль (стан у /run — tmpfs, скидається перезавантаженням, це прийнятно).
set -euo pipefail

WLAN_IFACE="wlan0"
CHECK_HOST="1.1.1.1"
CHECK_TIMEOUT=3
NORMAL_WLAN_METRIC=50
DEMOTED_WLAN_METRIC=9999
REQUIRED_CONSECUTIVE=3
STATE_FILE="/run/starlink-wan-failover/state"

CONN_NAME="$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | awk -F: -v d="$WLAN_IFACE" '$2==d{print $1; exit}' || true)"
if [[ -z "$CONN_NAME" ]]; then
  # wlan0 не має активного з'єднання NetworkManager (не підключений
  # до WiFi Starlink чи щойно піднімається) - нічого коригувати.
  rm -f "$STATE_FILE"
  exit 0
fi

CURRENT_METRIC="$(nmcli -t -g ipv4.route-metric connection show "$CONN_NAME" 2>/dev/null || echo "$NORMAL_WLAN_METRIC")"
[[ -z "$CURRENT_METRIC" || "$CURRENT_METRIC" == "-1" ]] && CURRENT_METRIC="$NORMAL_WLAN_METRIC"

if ping -c 1 -W "$CHECK_TIMEOUT" -I "$WLAN_IFACE" "$CHECK_HOST" >/dev/null 2>&1; then
  CHECK_RESULT="online"
  TARGET_METRIC="$NORMAL_WLAN_METRIC"
  TARGET_LABEL="wlan0 (Starlink) знову має інтернет - відновлюю пріоритет"
else
  CHECK_RESULT="offline"
  TARGET_METRIC="$DEMOTED_WLAN_METRIC"
  TARGET_LABEL="wlan0 (Starlink) без інтернету - знижую пріоритет, eth0 стає дефолтним для всієї системи"
fi

# Стан уже правильний - нічого не робимо, лічильник теж не потрібен.
if [[ "$CURRENT_METRIC" == "$TARGET_METRIC" ]]; then
  rm -f "$STATE_FILE"
  exit 0
fi

if [[ -f "$STATE_FILE" ]]; then
  read -r PREV_RESULT PREV_COUNT < "$STATE_FILE" || { PREV_RESULT=""; PREV_COUNT=0; }
else
  PREV_RESULT=""
  PREV_COUNT=0
fi
if [[ "$CHECK_RESULT" == "$PREV_RESULT" ]]; then
  COUNT=$((PREV_COUNT + 1))
else
  COUNT=1
fi
echo "$CHECK_RESULT $COUNT" > "$STATE_FILE"

if [[ "$COUNT" -lt "$REQUIRED_CONSECUTIVE" ]]; then
  # Ще недостатньо підтверджень поспіль - не перемикаємо (уникаємо
  # реакції на одиничний флап/хибний результат ping).
  exit 0
fi

echo "==> $TARGET_LABEL (metric=$TARGET_METRIC)"
nmcli connection modify "$CONN_NAME" ipv4.route-metric "$TARGET_METRIC"
# reapply застосовує зміну без повного перепідключення (уникає
# короткого розриву WiFi, який спричинив би `connection up`); якщо
# reapply недоступний (старіша версія NetworkManager) - fallback на
# повне перепідключення.
nmcli device reapply "$WLAN_IFACE" 2>/dev/null || nmcli connection up "$CONN_NAME" 2>/dev/null || true
rm -f "$STATE_FILE"
