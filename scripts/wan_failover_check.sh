#!/usr/bin/env bash
# Системний WAN-failover: періодично перевіряє, чи wlan0 (WiFi Starlink)
# реально має вихід в інтернет (не лише локальний зв'язок з dish/router),
# і динамічно коригує metric дефолтного маршруту, щоб eth0 (USB-Ethernet)
# автоматично ставав пріоритетним для ВСІЄЇ системи (apt, curl, будь-яка
# інша програма), коли супутниковий канал Starlink недоступний.
#
# ВАЖЛИВО: керування через nmcli/NetworkManager, НЕ напряму через
# `ip route`. wlan0 - NetworkManager-кероване з'єднання з власним
# persistent-налаштуванням `ipv4.route-metric` (задане в install.sh).
# Пряма зміна таблиці маршрутів через `ip route add/del` в обхід
# NetworkManager призводила до постійної "боротьби": NetworkManager
# перевідновлював свій власний metric=50 (з конфігурації з'єднання)
# незалежно від наших змін, тому кожен цикл таймера знову "виправляв"
# те саме - нескінченний цикл. `nmcli connection modify` + `nmcli
# device reapply` змінює саму persistent-конфігурацію з'єднання, яку
# NetworkManager і застосовує, без конфлікту.
#
# Маршрут до router (192.168.1.0/24) - власна підмережа wlan0,
# автоматичний kernel-scope запис, завжди пріоритетніший за дефолтний
# незалежно від metric. Маршрут до dish (192.168.100.0/24) такого
# автоматичного запису НЕ має - install.sh додає його окремо, явно
# (`nmcli +ipv4.routes`), саме для того щоб він не залежав від змін
# тут.
#
# Запускається через systemd timer (starlink-wan-failover.timer, кожні ~20с).
#
# ГІСТЕРЕЗИС: реальні логи показали, що переключення (і супутній
# `nmcli device reapply`) стається щоразу, коли одна перевірка ping
# випадково провалюється - кожен reapply на мить розриває маршрут до
# dish (192.168.100.1), спричиняючи хибні "Dish недоступний" в журналі
# й Telegram, і, ймовірно, впливаючи на РЕЗУЛЬТАТ НАСТУПНОЇ ping-
# перевірки самого скрипта (замкнене коло самопідживлення - reapply
# сам спричиняє наступний "провал", який знову тригерить reapply).
# Тому перемикання відбувається лише після REQUIRED_CONSECUTIVE
# поспіль однакових результатів (стан зберігається між запусками в
# /run - tmpfs, скидається щоперезавантаження, що прийнятно).
set -euo pipefail

WLAN_IFACE="wlan0"
CHECK_HOST="1.1.1.1"
CHECK_TIMEOUT=3
NORMAL_WLAN_METRIC=50
DEMOTED_WLAN_METRIC=9999
REQUIRED_CONSECUTIVE=3
STATE_FILE="/run/starlink-wan-failover.state"

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
