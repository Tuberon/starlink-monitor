#!/usr/bin/env bash
# Завантажує starlink_grpc.py з upstream community-репозиторію
# sparky8512/starlink-grpc-tools (https://github.com/sparky8512/starlink-grpc-tools).
#
# ПРИМІТКА: поточна версія starlink_grpc.py (гілка main) сама імпортує
# на верхньому рівні пакет yagrc (gRPC reflection client) - без нього
# отримуєте "ModuleNotFoundError: No module named 'yagrc'" при спробі
# import starlink_grpc. Пакет yagrc встановлюється через requirements.txt
# (install.sh), окремої генерації protobuf-модулів через grpc_tools.protoc
# не потрібно - yagrc сам створює потрібні класи на льоту через reflection
# API dish. reboot_dish() у нашому клієнті все одно викликає grpcurl
# напряму як subprocess, не залежить від starlink_grpc.
#
# Можна запускати вручну, або автоматично через systemd-сервіс
# starlink-grpc-fetch.service (чекає на WiFi-з'єднання зі Starlink Mini
# і рестартує основні сервіси після успішного завантаження) —
# див. scripts/install.sh.
#
# Опції:
#   --wait-for-dish   Чекати доступності dish замість негайного виходу з помилкою
#                      (з ретраями, для використання при старті системи, коли
#                      WiFi-з'єднання зі Starlink Mini ще не встановлене).
#   --restart-services Перезапустити starlink-monitor/starlink-webui після
#                      успішного завантаження (потребує sudo-прав, налаштованих
#                      install.sh).
set -euo pipefail

PROJECT_DIR="${STARLINK_PROJECT_DIR:-/opt/starlink-monitor}"
VENDOR_DIR="$PROJECT_DIR/app/vendor"
DISH_ADDR="${STARLINK_DISH_ADDR:-192.168.100.1:9200}"
MAX_WAIT_ATTEMPTS="${STARLINK_FETCH_MAX_ATTEMPTS:-30}"   # 30 * 10с = 5 хв
WAIT_INTERVAL_SEC="${STARLINK_FETCH_WAIT_INTERVAL:-10}"

WAIT_FOR_DISH=0
RESTART_SERVICES=0
for arg in "$@"; do
  case "$arg" in
    --wait-for-dish) WAIT_FOR_DISH=1 ;;
    --restart-services) RESTART_SERVICES=1 ;;
  esac
done

dish_reachable() {
  command -v grpcurl >/dev/null 2>&1 \
    && grpcurl -plaintext -d '{"get_status":{}}' "$DISH_ADDR" SpaceX.API.Device.Device/Handle >/dev/null 2>&1
}

mkdir -p "$VENDOR_DIR"
touch "$VENDOR_DIR/__init__.py"

if [[ "$WAIT_FOR_DISH" -eq 1 ]]; then
  echo "==> Очікую доступності dish на $DISH_ADDR (до $((MAX_WAIT_ATTEMPTS * WAIT_INTERVAL_SEC))с)"
  attempt=0
  until dish_reachable; do
    attempt=$((attempt + 1))
    if [[ "$attempt" -ge "$MAX_WAIT_ATTEMPTS" ]]; then
      echo "!! dish не з'явився на $DISH_ADDR за відведений час, завершую без завантаження"
      exit 1
    fi
    sleep "$WAIT_INTERVAL_SEC"
  done
  echo "==> dish доступний"
fi

echo "==> Завантажую starlink_grpc.py"
curl -fsSL \
  "https://raw.githubusercontent.com/sparky8512/starlink-grpc-tools/main/starlink_grpc.py" \
  -o "$VENDOR_DIR/starlink_grpc.py"

echo "==> Готово: $VENDOR_DIR/starlink_grpc.py"
echo "==> Перевірка (потребує активного WiFi-з'єднання зі Starlink Mini)"
if command -v grpcurl >/dev/null 2>&1; then
  if dish_reachable; then
    echo "==> dish відповідає на $DISH_ADDR - все готово"
  else
    echo "!! dish не відповів на $DISH_ADDR. Перевірте WiFi-з'єднання і повторіть пізніше."
  fi
else
  echo "!! grpcurl не знайдено в PATH - reboot dish не працюватиме, поки його не встановлено (див. install.sh)"
fi

if [[ "$RESTART_SERVICES" -eq 1 ]]; then
  echo "==> Перезапускаю сервіси, щоб підхопити зміни"
  # Окремі виклики, а не один з двома аргументами: sudoers NOPASSWD-правила
  # (див. install.sh) прописані по одному сервісу на рядок і мають збігатися
  # з командою ТОЧНО, тому "systemctl restart a.service b.service" одним
  # викликом не підійде під жодне з двох окремих правил.
  sudo systemctl restart starlink-monitor.service
  sudo systemctl restart starlink-webui.service
  echo "==> Сервіси перезапущено"
fi
