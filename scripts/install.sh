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

if [[ -d "$PROJECT_DIR" ]]; then
  MODE="update"
  echo "==> Виявлено існуючу інсталяцію в $PROJECT_DIR — режим оновлення"
else
  MODE="install"
  echo "==> Існуючої інсталяції не знайдено — повне встановлення"
fi

# Суттєва зміна = у новому requirements.txt з'явився пакет, якого не було
# в попередній інсталяції (порівняння за НАЗВОЮ пакета, без версії - зміна
# версії існуючого пакета не вважається суттєвою і не вимагає перевстановлення).
# Новий пакет типово вимагає додаткового системного ПЗ (build-залежності,
# системні бібліотеки тощо), тому в такому випадку безпечніше повністю
# видалити попередню інсталяцію і пройти install-потік з нуля.
MAJOR_CHANGE=0
if [[ "$MODE" == "update" && -f "$PROJECT_DIR/requirements.txt" ]]; then
  NEW_PKGS="$(grep -oE '^[A-Za-z0-9_.-]+' "$SRC_DIR/requirements.txt" | tr 'A-Z' 'a-z' | sort -u)"
  OLD_PKGS="$(grep -oE '^[A-Za-z0-9_.-]+' "$PROJECT_DIR/requirements.txt" | tr 'A-Z' 'a-z' | sort -u)"
  ADDED_PKGS="$(comm -13 <(echo "$OLD_PKGS") <(echo "$NEW_PKGS"))"
  if [[ -n "$ADDED_PKGS" ]]; then
    MAJOR_CHANGE=1
    echo "==> Суттєва зміна: нові пакети в requirements.txt:"
    echo "$ADDED_PKGS" | sed 's/^/     - /'
  fi
fi

if [[ "$MAJOR_CHANGE" -eq 1 ]]; then
  echo "==> Видаляю попередню інсталяцію перед повним перевстановленням"
  systemctl stop starlink-monitor.service starlink-webui.service starlink-grpc-fetch.service 2>/dev/null || true
  systemctl disable starlink-monitor.service starlink-webui.service starlink-grpc-fetch.service 2>/dev/null || true
  rm -f /etc/systemd/system/starlink-monitor.service \
        /etc/systemd/system/starlink-webui.service \
        /etc/systemd/system/starlink-grpc-fetch.service
  systemctl daemon-reload
  rm -f /etc/sudoers.d/starlink-monitor
  # signature_phrases.txt (можливо відредагований користувачем) зберігаємо
  # окремо і повертаємо назад після перевстановлення файлів проєкту.
  SAVED_PHRASES=""
  if [[ -f "$PROJECT_DIR/app/signature_phrases.txt" ]]; then
    SAVED_PHRASES="$(mktemp)"
    cp "$PROJECT_DIR/app/signature_phrases.txt" "$SAVED_PHRASES"
  fi
  rm -rf "$PROJECT_DIR"
  MODE="install"
  echo "==> Попередню інсталяцію видалено — продовжую як повне встановлення"
fi

REQ_CHANGED=1
if [[ "$MODE" == "update" && -f "$PROJECT_DIR/requirements.txt" ]]; then
  if diff -q "$SRC_DIR/requirements.txt" "$PROJECT_DIR/requirements.txt" >/dev/null 2>&1; then
    REQ_CHANGED=0
  fi
fi

if [[ "$MODE" == "install" ]]; then
  echo "==> Встановлюю системні пакети"
  apt-get update
  apt-get install -y --no-install-recommends \
    python3 python3-venv python3-pip git curl \
    network-manager python3-libgpiod gpiod

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

  echo "==> Додаю $RUN_USER до групи gpio (доступ до /dev/gpiochip* для кнопки виключення)"
  usermod -aG gpio "$RUN_USER" 2>/dev/null || echo "!! Група gpio відсутня в системі - пропускаю (кнопка виключення не працюватиме без неї)"
else
  echo "==> Режим оновлення — пропускаю перевірку/оновлення системних пакетів"
fi

echo "==> Синхронізую файли проєкту в $PROJECT_DIR (лише змінені відносно попередньої інсталяції)"
mkdir -p "$PROJECT_DIR"
RSYNC_EXCLUDES=(--exclude 'venv' --exclude '.git')
if [[ "$MODE" == "update" && -f "$PROJECT_DIR/app/signature_phrases.txt" ]]; then
  RSYNC_EXCLUDES+=(--exclude 'app/signature_phrases.txt')
fi
# -c: порівняння за контрольною сумою (не лише за розміром/часом), --itemize-changes
# показує, які файли реально змінились - корисно бачити, що саме оновилось.
RSYNC_OUT="$(rsync -ac --itemize-changes "${RSYNC_EXCLUDES[@]}" "$SRC_DIR/" "$PROJECT_DIR/")"
echo "$RSYNC_OUT"
CHANGED_FILES="$(echo "$RSYNC_OUT" | grep -c '^[<>ch]' || true)"
chown -R "$RUN_USER:$RUN_USER" "$PROJECT_DIR"

if [[ -n "${SAVED_PHRASES:-}" && -f "$SAVED_PHRASES" ]]; then
  cp "$SAVED_PHRASES" "$PROJECT_DIR/app/signature_phrases.txt"
  chown "$RUN_USER:$RUN_USER" "$PROJECT_DIR/app/signature_phrases.txt"
  rm -f "$SAVED_PHRASES"
  echo "==> Відновлено попередній signature_phrases.txt після перевстановлення"
fi

if [[ "$MODE" == "update" && "$CHANGED_FILES" -eq 0 ]]; then
  echo "==> Змінених файлів не виявлено, файлова частина без змін"
fi

echo "==> Створюю/оновлюю Python venv та залежності"
if [[ ! -d "$PROJECT_DIR/venv" ]]; then
  # --system-site-packages: дозволяє venv бачити системний python3-libgpiod
  # (кнопка виключення) - libgpiod не завжди чисто ставиться через pip,
  # системний пакет через apt надійніший на Raspberry Pi OS.
  sudo -u "$RUN_USER" python3 -m venv --system-site-packages "$PROJECT_DIR/venv"
  sudo -u "$RUN_USER" "$PROJECT_DIR/venv/bin/pip" install --upgrade pip setuptools wheel
  sudo -u "$RUN_USER" "$PROJECT_DIR/venv/bin/pip" install -r "$PROJECT_DIR/requirements.txt"
elif [[ "$REQ_CHANGED" -eq 1 ]]; then
  echo "==> requirements.txt змінився — оновлюю залежності"
  sudo -u "$RUN_USER" "$PROJECT_DIR/venv/bin/pip" install --upgrade -r "$PROJECT_DIR/requirements.txt"
else
  echo "==> venv вже існує, requirements.txt без змін — пропускаю pip install"
fi

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
# webui.service редагує цей файл через сторінку /settings - потрібен
# запис для RUN_USER, під яким сервіс і працює.
chown -R "$RUN_USER:$RUN_USER" /etc/starlink-monitor
chmod 600 /etc/starlink-monitor/env

echo "==> Налаштовую обмежені sudo-права для сервісного користувача ($RUN_USER)"
# ВАЖЛИВО: надаємо право виконувати ЛИШЕ конкретні команди без пароля,
# необхідні для рестарту сервісів, reboot dish і reboot/shutdown самого Pi.
# Це навмисно вузько — НЕ blanket "ALL=(ALL) NOPASSWD: ALL".
cat > /etc/sudoers.d/starlink-monitor <<EOF
$RUN_USER ALL=(root) NOPASSWD: /bin/systemctl restart starlink-monitor.service
$RUN_USER ALL=(root) NOPASSWD: /bin/systemctl restart starlink-webui.service
$RUN_USER ALL=(root) NOPASSWD: /bin/systemctl reboot
$RUN_USER ALL=(root) NOPASSWD: /bin/systemctl poweroff
EOF
chmod 0440 /etc/sudoers.d/starlink-monitor
visudo -c -f /etc/sudoers.d/starlink-monitor

echo "==> Встановлюю/оновлюю systemd unit-файли (підстановка користувача $RUN_USER)"
UNITS_UPDATED=0
for svc in starlink-monitor.service starlink-webui.service starlink-grpc-fetch.service starlink-shutdown-button.service; do
  NEW_UNIT="$(sed "s/__RUN_USER__/$RUN_USER/g" "$PROJECT_DIR/systemd/$svc")"
  DEST="/etc/systemd/system/$svc"
  if [[ ! -f "$DEST" ]] || ! diff -q <(echo "$NEW_UNIT") "$DEST" >/dev/null 2>&1; then
    echo "$NEW_UNIT" > "$DEST"
    UNITS_UPDATED=1
  fi
done

if [[ "$UNITS_UPDATED" -eq 1 ]]; then
  systemctl daemon-reload
fi

systemctl enable --now starlink-monitor.service
systemctl enable --now starlink-webui.service
systemctl enable --now starlink-shutdown-button.service
systemctl enable starlink-grpc-fetch.service

if [[ "$MODE" == "update" ]]; then
  if [[ "$CHANGED_FILES" -gt 0 || "$REQ_CHANGED" -eq 1 || "$UNITS_UPDATED" -eq 1 ]]; then
    echo "==> Виявлено зміни — перезапускаю сервіси"
    systemctl restart starlink-monitor.service
    systemctl restart starlink-webui.service
    systemctl restart starlink-shutdown-button.service
  else
    echo "==> Змін не виявлено — сервіси не перезапускаю"
  fi
else
  systemctl start starlink-grpc-fetch.service &
fi

echo ""
echo "======================================================================"
echo " Налаштування мережі (опційно)"
echo "======================================================================"
echo ""
echo " Типова топологія: USB-Ethernet — доступ у домашню мережу/інтернет,"
echo " WiFi (wlan0) — підключення до Starlink Mini (моніторинг + reboot dish)."
echo " За замовчуванням обидва інтерфейси отримують адресу по DHCP, що може"
echo " спричиняти конфлікти маршрутів (dish/router стають недоступні, якщо"
echo " домашня мережа отримує вищий пріоритет за замовчуванням)."
echo ""
read -r -p " Налаштувати статичні IP для USB-Ethernet і WiFi зараз? [т/N]: " SETUP_NET
if [[ "$SETUP_NET" =~ ^[TtYyТт] ]]; then
  ETH_IFACE="eth0"
  WLAN_IFACE="wlan0"
  ETH_CONN="$(nmcli -t -f NAME,DEVICE connection show | awk -F: -v d="$ETH_IFACE" '$2==d{print $1; exit}')"
  WLAN_CONN="$(nmcli -t -f NAME,DEVICE connection show | awk -F: -v d="$WLAN_IFACE" '$2==d{print $1; exit}')"

  if [[ -z "$ETH_CONN" || -z "$WLAN_CONN" ]]; then
    echo " !! Не вдалося знайти профілі NetworkManager для $ETH_IFACE і/або $WLAN_IFACE."
    echo "    Перевірте підключення обох інтерфейсів (nmcli connection show) і повторіть пізніше:"
    echo "      sudo bash scripts/install.sh"
  else
    echo " Знайдено з'єднання: eth0=\"$ETH_CONN\", wlan0=\"$WLAN_CONN\""
    echo ""
    echo " Значення за замовчуванням (Enter, щоб прийняти):"

    read -r -p "   IP для $ETH_IFACE [192.168.0.95/24]: " ETH_IP
    ETH_IP="${ETH_IP:-192.168.0.95/24}"
    read -r -p "   Gateway для $ETH_IFACE [192.168.0.1]: " ETH_GW
    ETH_GW="${ETH_GW:-192.168.0.1}"

    read -r -p "   IP для $WLAN_IFACE (Starlink WiFi) [192.168.1.95/24]: " WLAN_IP
    WLAN_IP="${WLAN_IP:-192.168.1.95/24}"
    read -r -p "   Gateway для $WLAN_IFACE (Starlink router) [192.168.1.1]: " WLAN_GW
    WLAN_GW="${WLAN_GW:-192.168.1.1}"

    # wlan0 з нижчим metric (вищий пріоритет) - трафік до dish/router
    # Starlink (окрема підмережа 192.168.100.0/24, недосяжна інакше, ніж
    # через дефолтний маршрут) завжди повинен йти через WiFi, не через
    # домашню мережу.
    nmcli connection modify "$ETH_CONN" \
      ipv4.method manual ipv4.addresses "$ETH_IP" ipv4.gateway "$ETH_GW" \
      ipv4.dns "$ETH_GW,8.8.8.8" ipv4.route-metric 1002
    nmcli connection modify "$WLAN_CONN" \
      ipv4.method manual ipv4.addresses "$WLAN_IP" ipv4.gateway "$WLAN_GW" \
      ipv4.dns "1.1.1.1,8.8.8.8" ipv4.route-metric 50

    # dhcpcd конфліктує з NetworkManager (перевидає власні DHCP-лізинги й
    # маршрути незалежно від профілів nmcli, ігноруючи ipv4.method=manual).
    if systemctl list-unit-files dhcpcd.service >/dev/null 2>&1; then
      echo " ==> Вимикаю dhcpcd (конфліктує з NetworkManager)"
      systemctl disable --now dhcpcd 2>/dev/null || true
      systemctl mask dhcpcd 2>/dev/null || true
    fi

    echo " ==> Перезапускаю з'єднання..."
    nmcli connection down "$ETH_CONN" 2>/dev/null || true
    nmcli connection down "$WLAN_CONN" 2>/dev/null || true
    ip addr flush dev "$ETH_IFACE" 2>/dev/null || true
    ip addr flush dev "$WLAN_IFACE" 2>/dev/null || true
    nmcli connection up "$ETH_CONN" || echo " !! Не вдалося підняти $ETH_CONN"
    nmcli connection up "$WLAN_CONN" || echo " !! Не вдалося підняти $WLAN_CONN"

    echo ""
    echo " ==> Готово. Перевірка:"
    ip -4 addr show "$ETH_IFACE" | grep inet || true
    ip -4 addr show "$WLAN_IFACE" | grep inet || true
    echo ""
    echo " Якщо працюєте віддалено по SSH через $ETH_IFACE — з'єднання могло"
    echo " щойно розірватись через зміну IP. Перепідключіться на нову адресу:"
    echo "   ssh ${RUN_USER}@${ETH_IP%%/*}"
  fi
else
  echo " Пропущено. Налаштувати мережу пізніше вручну можна через nmcli"
  echo " (див. секцію \"Важливо розуміти про мережу\" в README.md)."
fi

echo ""
echo "======================================================================"
if [[ "$MODE" == "update" ]]; then
  echo " Оновлення завершено."
else
  echo " Базове встановлення завершено."
fi
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
echo ""
echo " Для наступних оновлень: покладіть новий starlink-monitor.tar.gz у"
echo " домашній каталог і виконайте:"
echo "      sudo bash scripts/update.sh"
echo "======================================================================"
