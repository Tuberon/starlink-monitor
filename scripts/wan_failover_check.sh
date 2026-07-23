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
# Специфічні маршрути до dish (192.168.100.0/24) і router (192.168.1.0/24)
# НЕ зачіпаються - це окремі, більш конкретні маршрути, які завжди
# пріоритетніші за будь-який дефолтний маршрут незалежно від metric.
#
# Запускається через systemd timer (starlink-wan-failover.timer, кожні ~20с).
set -euo pipefail

WLAN_IFACE="wlan0"
CHECK_HOST="1.1.1.1"
CHECK_TIMEOUT=3
NORMAL_WLAN_METRIC=50
DEMOTED_WLAN_METRIC=9999

CONN_NAME="$(nmcli -t -f NAME,DEVICE connection show --active 2>/dev/null | awk -F: -v d="$WLAN_IFACE" '$2==d{print $1; exit}' || true)"
if [[ -z "$CONN_NAME" ]]; then
  # wlan0 не має активного з'єднання NetworkManager (не підключений
  # до WiFi Starlink чи щойно піднімається) - нічого коригувати.
  exit 0
fi

CURRENT_METRIC="$(nmcli -t -g ipv4.route-metric connection show "$CONN_NAME" 2>/dev/null || echo "$NORMAL_WLAN_METRIC")"
[[ -z "$CURRENT_METRIC" || "$CURRENT_METRIC" == "-1" ]] && CURRENT_METRIC="$NORMAL_WLAN_METRIC"

if ping -c 1 -W "$CHECK_TIMEOUT" -I "$WLAN_IFACE" "$CHECK_HOST" >/dev/null 2>&1; then
  TARGET_METRIC="$NORMAL_WLAN_METRIC"
  TARGET_LABEL="wlan0 (Starlink) знову має інтернет - відновлюю пріоритет"
else
  TARGET_METRIC="$DEMOTED_WLAN_METRIC"
  TARGET_LABEL="wlan0 (Starlink) без інтернету - знижую пріоритет, eth0 стає дефолтним для всієї системи"
fi

if [[ "$CURRENT_METRIC" == "$TARGET_METRIC" ]]; then
  exit 0
fi

echo "==> $TARGET_LABEL (metric=$TARGET_METRIC)"
nmcli connection modify "$CONN_NAME" ipv4.route-metric "$TARGET_METRIC"
# reapply застосовує зміну без повного перепідключення (уникає
# короткого розриву WiFi, який спричинив би `connection up`); якщо
# reapply недоступний (старіша версія NetworkManager) - fallback на
# повне перепідключення.
nmcli device reapply "$WLAN_IFACE" 2>/dev/null || nmcli connection up "$CONN_NAME" 2>/dev/null || true
