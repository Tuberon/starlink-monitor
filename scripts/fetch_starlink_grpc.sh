#!/usr/bin/env bash
# Завантажує starlink_grpc.py з upstream community-репозиторію
# sparky8512/starlink-grpc-tools (https://github.com/sparky8512/starlink-grpc-tools).
#
# ПРИМІТКА: окрема генерація protobuf/grpc python-модулів через grpc_tools.protoc
# більше НЕ потрібна — get_status() використовує вбудований механізм reflection
# бібліотеки starlink_grpc, а reboot_dish() викликає grpcurl напряму як subprocess.
# Досить лише файлу starlink_grpc.py.
#
# Вимагає доступу до dish (192.168.100.1:9200) в момент першого запуску
# opel-скриптів (starlink_grpc сам користується reflection при кожному виклику),
# тому запускати ПІСЛЯ підключення Pi до WiFi Starlink Mini.
set -euo pipefail

PROJECT_DIR="${STARLINK_PROJECT_DIR:-/opt/starlink-monitor}"
VENDOR_DIR="$PROJECT_DIR/app/vendor"

mkdir -p "$VENDOR_DIR"
touch "$VENDOR_DIR/__init__.py"

echo "==> Завантажую starlink_grpc.py"
curl -fsSL \
  "https://raw.githubusercontent.com/sparky8512/starlink-grpc-tools/main/starlink_grpc.py" \
  -o "$VENDOR_DIR/starlink_grpc.py"

echo "==> Готово: $VENDOR_DIR/starlink_grpc.py"
echo "==> Перевірка (потребує активного WiFi-з'єднання зі Starlink Mini)"
if command -v grpcurl >/dev/null 2>&1; then
  DISH_ADDR="${STARLINK_DISH_ADDR:-192.168.100.1:9200}"
  if grpcurl -plaintext -d '{}' "$DISH_ADDR" SpaceX.API.Device.Device/Handle >/dev/null 2>&1; then
    echo "==> dish відповідає на $DISH_ADDR - все готово"
  else
    echo "!! dish не відповів на $DISH_ADDR. Перевірте WiFi-з'єднання і повторіть пізніше."
  fi
else
  echo "!! grpcurl не знайдено в PATH - reboot dish не працюватиме, поки його не встановлено (див. install.sh)"
fi
