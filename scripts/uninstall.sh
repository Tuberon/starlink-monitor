#!/usr/bin/env bash
# Повне видалення Starlink Monitor з Raspberry Pi.
# Запускати з правами sudo: `sudo bash scripts/uninstall.sh`
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Запустіть з sudo: sudo bash scripts/uninstall.sh"
  exit 1
fi

PROJECT_DIR="/opt/starlink-monitor"
DATA_DIR="/var/lib/starlink-monitor"
CONFIG_DIR="/etc/starlink-monitor"

echo "======================================================================"
echo " Видалення Starlink Monitor"
echo "======================================================================"
echo ""
echo " Буде видалено:"
echo "   - systemd-сервіси (monitor, webui, grpc-fetch, shutdown-button)"
echo "   - $PROJECT_DIR (код проєкту)"
echo "   - /etc/sudoers.d/starlink-monitor"
echo ""
echo " НЕ буде видалено без окремого підтвердження:"
echo "   - $DATA_DIR (історія метрик, журнал подій - SQLite БД)"
echo "   - $CONFIG_DIR (env-файл: Telegram bot token, налаштування)"
echo ""
read -r -p " Продовжити видалення коду і сервісів? [т/N]: " CONFIRM
if [[ ! "$CONFIRM" =~ ^[TtYyТт] ]]; then
  echo "Скасовано."
  exit 0
fi

echo "==> Зупиняю та вимикаю сервіси"
systemctl stop starlink-monitor.service starlink-webui.service \
  starlink-grpc-fetch.service starlink-shutdown-button.service \
  starlink-wan-failover.timer starlink-wan-failover.service 2>/dev/null || true
systemctl disable starlink-monitor.service starlink-webui.service \
  starlink-grpc-fetch.service starlink-shutdown-button.service \
  starlink-wan-failover.timer 2>/dev/null || true

# Якщо WAN failover-таймер щойно знизив пріоритет wlan0 (metric=9999,
# бо Starlink був без інтернету), відновлюємо нормальний пріоритет -
# інакше після видалення проєкту (і самого таймера, який це коригував)
# wlan0 лишився б назавжди з "зіпсованою" маршрутизацією. Перевіряємо
# УСІ default-маршрути на wlan0 (не лише перший) - `ip route replace`
# із вказаним metric додає новий запис замість заміни, якщо такого
# metric раніше не було, тому могли накопичитись дублікати (metric=50
# і metric=9999 одночасно) - видаляємо всі, додаємо один чистий.
mapfile -t WLAN_ROUTES < <(ip route show default dev wlan0 2>/dev/null || true)
NEEDS_FIX=0
for route in "${WLAN_ROUTES[@]:-}"; do
  echo "$route" | grep -q "metric 9999\b" && NEEDS_FIX=1
done
if [[ "${#WLAN_ROUTES[@]}" -gt 1 || "$NEEDS_FIX" -eq 1 ]]; then
  WLAN_GATEWAY=""
  for route in "${WLAN_ROUTES[@]:-}"; do
    WLAN_GATEWAY="$(echo "$route" | grep -oP 'via \K[0-9.]+' || true)"
    [[ -n "$WLAN_GATEWAY" ]] && break
  done
  if [[ -n "$WLAN_GATEWAY" ]]; then
    echo "==> Відновлюю нормальний пріоритет маршруту wlan0 (metric=50)"
    while ip route del default dev wlan0 2>/dev/null; do :; done
    ip route add default via "$WLAN_GATEWAY" dev wlan0 metric 50 2>/dev/null || true
  fi
fi

echo "==> Видаляю systemd unit-файли"
rm -f /etc/systemd/system/starlink-monitor.service \
      /etc/systemd/system/starlink-webui.service \
      /etc/systemd/system/starlink-grpc-fetch.service \
      /etc/systemd/system/starlink-shutdown-button.service \
      /etc/systemd/system/starlink-wan-failover.service \
      /etc/systemd/system/starlink-wan-failover.timer
systemctl daemon-reload

echo "==> Видаляю sudoers-правило"
rm -f /etc/sudoers.d/starlink-monitor

echo "==> Видаляю код проєкту ($PROJECT_DIR)"
rm -rf "$PROJECT_DIR"

echo ""
echo " Код і сервіси видалено."
echo ""

if [[ -d "$DATA_DIR" || -d "$CONFIG_DIR" ]]; then
  echo "----------------------------------------------------------------------"
  echo " Знайдено дані попередньої інсталяції:"
  [[ -d "$DATA_DIR" ]] && echo "   - $DATA_DIR (історія метрик, журнал подій)"
  [[ -d "$CONFIG_DIR" ]] && echo "   - $CONFIG_DIR (Telegram bot token, налаштування)"
  echo ""
  read -r -p " Видалити ці дані НАЗАВЖДИ (без можливості відновлення)? [т/N]: " CONFIRM_DATA
  if [[ "$CONFIRM_DATA" =~ ^[TtYyТт] ]]; then
    rm -rf "$DATA_DIR" "$CONFIG_DIR"
    echo " ==> Дані видалено."
  else
    echo " ==> Дані залишено. Якщо встановите проєкт знову — стара історія"
    echo "     й налаштування Telegram підхопляться автоматично."
  fi
fi

echo ""
echo "======================================================================"
echo " Видалення завершено."
echo "======================================================================"

echo ""
echo " Примітка: GPIO-група та системні пакети (grpcurl, python3-libgpiod,"
echo " network-manager тощо), встановлені разом із проєктом, НЕ видаляються —"
echo " вони можуть використовуватись іншими застосунками на цьому Pi."
echo " Налаштування статичних IP (eth0/wlan0), якщо застосовувались через"
echo " install.sh, теж НЕ відкочуються автоматично — керуйте вручну через nmcli."
