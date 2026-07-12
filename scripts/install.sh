#!/usr/bin/env bash
# Встановлення Starlink Monitor на Raspberry Pi OS (Bookworm) / RPi Zero 2 W.
# Запускати з правами sudo: `sudo bash scripts/install.sh`
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Запустіть з sudo: sudo bash scripts/install.sh"
  exit 1
fi

RUN_USER="${SUDO_USER:-pi}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="/opt/starlink-monitor"

echo "==> Встановлюю системні пакети"
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip git curl \
  unattended-upgrades apt-listchanges \
  network-manager

echo "==> Копіюю проєкт у $PROJECT_DIR"
mkdir -p "$PROJECT_DIR"
rsync -a --exclude 'venv' --exclude '.git' "$SRC_DIR/" "$PROJECT_DIR/"
chown -R "$RUN_USER:$RUN_USER" "$PROJECT_DIR"

echo "==> Створюю Python venv та встановлюю залежності"
sudo -u "$RUN_USER" python3 -m venv "$PROJECT_DIR/venv"
sudo -u "$RUN_USER" "$PROJECT_DIR/venv/bin/pip" install --upgrade pip
sudo -u "$RUN_USER" "$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"

echo "==> Каталог даних"
mkdir -p /var/lib/starlink-monitor
chown -R "$RUN_USER:$RUN_USER" /var/lib/starlink-monitor

mkdir -p /etc/starlink-monitor
if [[ ! -f /etc/starlink-monitor/env ]]; then
  cat > /etc/starlink-monitor/env <<'EOF'
# Локальні налаштування Starlink Monitor. Див. app/config.py для повного списку.
# STARLINK_DISH_ADDR=192.168.100.1:9200
# STARLINK_POLL_INTERVAL=10
# STARLINK_WEBUI_PORT=8080
EOF
fi

echo "==> Налаштовую обмежені sudo-права для сервісного користувача ($RUN_USER)"
# ВАЖЛИВО: надаємо право виконувати ЛИШЕ конкретні команди без пароля,
# необхідні updater.py для apt-оновлень, рестарту сервісів і reboot.
# Це навмисно вузько — НЕ blanket "ALL=(ALL) NOPASSWD: ALL".
cat > /etc/sudoers.d/starlink-monitor <<EOF
$RUN_USER ALL=(root) NOPASSWD: /usr/bin/apt-get update -qq
$RUN_USER ALL=(root) NOPASSWD: /usr/bin/apt-get -y -qq upgrade
$RUN_USER ALL=(root) NOPASSWD: /usr/bin/apt-get -y -qq autoremove
$RUN_USER ALL=(root) NOPASSWD: /bin/systemctl restart starlink-monitor.service
$RUN_USER ALL=(root) NOPASSWD: /bin/systemctl restart starlink-webui.service
$RUN_USER ALL=(root) NOPASSWD: /bin/systemctl reboot
EOF
chmod 0440 /etc/sudoers.d/starlink-monitor
visudo -c -f /etc/sudoers.d/starlink-monitor

echo "==> Вмикаю unattended-upgrades (системний рівень автооновлень безпеки)"
dpkg-reconfigure -f noninteractive unattended-upgrades || true

echo "==> Встановлюю systemd unit-файли"
cp "$PROJECT_DIR/systemd/starlink-monitor.service" /etc/systemd/system/
cp "$PROJECT_DIR/systemd/starlink-webui.service" /etc/systemd/system/
cp "$PROJECT_DIR/systemd/starlink-updater.service" /etc/systemd/system/
cp "$PROJECT_DIR/systemd/starlink-updater.timer" /etc/systemd/system/

systemctl daemon-reload
systemctl enable --now starlink-monitor.service
systemctl enable --now starlink-webui.service
systemctl enable --now starlink-updater.timer

echo ""
echo "======================================================================"
echo " Базове встановлення завершено."
echo ""
echo " НАСТУПНІ КРОКИ (вручну, потребують взаємодії):"
echo ""
echo " 1. Підключіть wlan0 до WiFi Starlink Mini:"
echo "      sudo nmcli device wifi connect \"<SSID Starlink>\" password \"<пароль>\" ifname wlan0"
echo ""
echo " 2. Отримайте protobuf-модулі gRPC dish (потребує активного WiFi-з'єднання"
echo "    зі Starlink Mini з кроку 1):"
echo "      sudo -u $RUN_USER bash $PROJECT_DIR/scripts/fetch_starlink_grpc.sh"
echo ""
echo " 3. Перезапустіть сервіси, щоб підхопити gRPC-модулі:"
echo "      sudo systemctl restart starlink-monitor.service starlink-webui.service"
echo ""
echo " 4. Дашборд: http://<ip-цього-pi>:8080"
echo "======================================================================"
