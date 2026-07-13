#!/usr/bin/env bash
# Встановлення Starlink Monitor на Raspberry Pi OS (Bookworm) / RPi Zero 2 W.
# Запускати з правами sudo: `sudo bash scripts/install.sh`
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Запустіть з sudo: sudo bash scripts/install.sh"
  exit 1
fi

if [[ -z "${SUDO_USER:-}" || "$SUDO_USER" == "root" ]]; then
  echo "Запустіть через sudo від імені звичайного користувача"
  echo "(не з-під прямого root-логіна): sudo bash scripts/install.sh"
  exit 1
fi
RUN_USER="$SUDO_USER"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_DIR="/opt/starlink-monitor"

echo "==> Встановлюю системні пакети"
apt-get update
apt-get install -y --no-install-recommends \
  python3 python3-venv python3-pip git curl \
  network-manager

echo "==> Перевіряю наявність grpcurl"
if ! command -v grpcurl >/dev/null 2>&1; then
  echo "==> grpcurl не знайдено, встановлюю"
  # 1) Спроба через apt (Debian trixie+ вже має пакет grpcurl у репозиторіях)
  if apt-get install -y --no-install-recommends grpcurl 2>/dev/null; then
    echo "==> grpcurl встановлено через apt"
  else
    # 2) Fallback: завантажити готовий бінарник з GitHub releases під поточну архітектуру.
    #    "latest/download" - стабільне посилання GitHub, що завжди вказує на останній реліз.
    ARCH="$(dpkg --print-architecture)"
    case "$ARCH" in
      arm64)  GRPCURL_ARCH="arm64" ;;
      armhf)  GRPCURL_ARCH="armv6" ;;
      amd64)  GRPCURL_ARCH="x86_64" ;;
      *) echo "!! Невідома архітектура $ARCH, пропускаю авто-встановлення grpcurl"; GRPCURL_ARCH="" ;;
    esac
    if [[ -n "$GRPCURL_ARCH" ]]; then
      # GitHub asset-файли завжди містять номер версії в імені
      # (напр. grpcurl_1.9.3_linux_arm64.tar.gz), тому спершу резолвимо
      # реальний тег останнього релізу через redirect
      # "releases/latest" -> ".../tag/vX.Y.Z", і лише потім будуємо URL.
      LATEST_TAG="$(curl -fsSL -o /dev/null -w '%{url_effective}' \
        "https://github.com/fullstorydev/grpcurl/releases/latest" | sed -n 's#.*/tag/v##p')"
      if [[ -z "$LATEST_TAG" ]]; then
        LATEST_TAG="1.9.3"  # fallback, якщо резолв версії не вдався
      fi
      TMP_TGZ="$(mktemp)"
      curl -fsSL \
        "https://github.com/fullstorydev/grpcurl/releases/download/v${LATEST_TAG}/grpcurl_${LATEST_TAG}_linux_${GRPCURL_ARCH}.tar.gz" \
        -o "$TMP_TGZ"
      tar -xzf "$TMP_TGZ" -C /usr/local/bin grpcurl
      chmod +x /usr/local/bin/grpcurl
      rm -f "$TMP_TGZ"
      echo "==> grpcurl $LATEST_TAG встановлено в /usr/local/bin ($(grpcurl --version 2>&1 | head -1))"
    fi
  fi
else
  echo "==> grpcurl вже встановлено ($(command -v grpcurl))"
fi

echo "==> Копіюю проєкт у $PROJECT_DIR"
mkdir -p "$PROJECT_DIR"
rsync -a --exclude 'venv' --exclude '.git' "$SRC_DIR/" "$PROJECT_DIR/"
chown -R "$RUN_USER:$RUN_USER" "$PROJECT_DIR"

echo "==> Створюю Python venv та встановлюю залежності"
sudo -u "$RUN_USER" python3 -m venv "$PROJECT_DIR/venv"
sudo -u "$RUN_USER" "$PROJECT_DIR/venv/bin/pip" install --upgrade pip setuptools wheel
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
# необхідні для рестарту сервісів і reboot dish.
# Це навмисно вузько — НЕ blanket "ALL=(ALL) NOPASSWD: ALL".
cat > /etc/sudoers.d/starlink-monitor <<EOF
$RUN_USER ALL=(root) NOPASSWD: /bin/systemctl restart starlink-monitor.service
$RUN_USER ALL=(root) NOPASSWD: /bin/systemctl restart starlink-webui.service
$RUN_USER ALL=(root) NOPASSWD: /bin/systemctl reboot
EOF
chmod 0440 /etc/sudoers.d/starlink-monitor
visudo -c -f /etc/sudoers.d/starlink-monitor

echo "==> Встановлюю systemd unit-файли (підстановка користувача $RUN_USER)"
for svc in starlink-monitor.service starlink-webui.service starlink-grpc-fetch.service; do
  sed "s/__RUN_USER__/$RUN_USER/g" \
    "$PROJECT_DIR/systemd/$svc" > "/etc/systemd/system/$svc"
done

systemctl daemon-reload
systemctl enable --now starlink-monitor.service
systemctl enable --now starlink-webui.service
# starlink-grpc-fetch.service: одноразовий, чекає на WiFi Starlink Mini і сам
# завантажує starlink_grpc.py + рестартує сервіси. enable (не --now), щоб він
# також запускався автоматично при кожному наступному завантаженні системи
# (напр. якщо Pi перезавантажиться і WiFi треба буде піднімати заново),
# а start запускаємо окремо, щоб не блокувати install.sh очікуванням dish.
systemctl enable starlink-grpc-fetch.service
systemctl start starlink-grpc-fetch.service &

echo ""
echo "======================================================================"
echo " Базове встановлення завершено."
echo ""
echo " НАСТУПНІ КРОКИ:"
echo ""
echo " 1. Підключіть wlan0 до WiFi Starlink Mini (якщо ще не підключений):"
echo "      sudo nmcli device wifi connect \"<SSID Starlink>\" password \"<пароль>\" ifname wlan0"
echo ""
echo " 2. starlink_grpc.py завантажиться АВТОМАТИЧНО, щойно dish стане доступним"
echo "    (сервіс starlink-grpc-fetch.service вже запущений у фоні й чекає)."
echo "    Перевірити прогрес:"
echo "      journalctl -u starlink-grpc-fetch.service -f"
echo ""
PI_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
if [[ -z "$PI_IP" ]]; then
  PI_IP="<ip-пристрою>"
fi
echo " 3. Дашборд: http://$PI_IP:8080"
echo "======================================================================"
