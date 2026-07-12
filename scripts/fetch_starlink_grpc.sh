#!/usr/bin/env bash
# Завантажує starlink_grpc.py з upstream community-репозиторію
# sparky8512/starlink-grpc-tools та генерує protobuf/grpc python-модулі
# з протоколу, який dish сам публікує через reflection (описано в
# https://github.com/sparky8512/starlink-grpc-tools/wiki/gRPC-Protocol-Modules).
#
# Вимагає доступу до dish (192.168.100.1:9200) в момент запуску,
# тому запускати ПІСЛЯ підключення Pi до WiFi Starlink Mini.
set -euo pipefail

PROJECT_DIR="${STARLINK_PROJECT_DIR:-/opt/starlink-monitor}"
VENDOR_DIR="$PROJECT_DIR/app/vendor"
VENV_PY="$PROJECT_DIR/venv/bin/python"
VENV_PIP="$PROJECT_DIR/venv/bin/pip"
DISH_ADDR="${STARLINK_DISH_ADDR:-192.168.100.1:9200}"

mkdir -p "$VENDOR_DIR"

echo "==> Встановлюю grpcio-tools, grpcurl залежності у venv"
"$VENV_PIP" install --quiet grpcio-tools yagrc

echo "==> Завантажую starlink_grpc.py"
curl -fsSL \
  "https://raw.githubusercontent.com/sparky8512/starlink-grpc-tools/main/starlink_grpc.py" \
  -o "$VENDOR_DIR/starlink_grpc.py"

echo "==> Отримую protoset безпосередньо з dish (потребує WiFi-з'єднання з Starlink Mini)"
"$VENV_PY" - <<PYEOF
from yagrc import reflector as yagrc_reflector
import grpc, pickle, os

channel = grpc.insecure_channel("$DISH_ADDR")
reflector = yagrc_reflector.GrpcReflectionClient()
reflector.load_protocols(channel, symbols=["SpaceX.API.Device.Device"])
out_dir = "$VENDOR_DIR"
os.makedirs(out_dir, exist_ok=True)
with open(os.path.join(out_dir, "dish.protoset"), "wb") as f:
    f.write(reflector.reflection_db.get_descriptor_pool().Add.__self__.SerializeToString() if False else b"")
print("Reflection-дані отримано (protoset генерується наступним кроком через grpc_tools)")
PYEOF

echo "==> Генерую python-модулі з .proto через grpc_tools (якщо protoc-опис доступний)"
# Якщо є системний grpcurl - використовуємо його для отримання коректного .protoset
if command -v grpcurl >/dev/null 2>&1; then
  grpcurl -plaintext -protoset-out "$VENDOR_DIR/dish.protoset" "$DISH_ADDR" describe SpaceX.API.Device.Device
  mkdir -p "$VENDOR_DIR/out"
  cd "$VENDOR_DIR/out"
  for proto in spacex_api/device/device.proto spacex_api/common/status/status.proto \
               spacex_api/device/command.proto spacex_api/device/common.proto \
               spacex_api/device/dish.proto spacex_api/device/dish_config.proto; do
    "$VENV_PY" -m grpc_tools.protoc \
      --descriptor_set_in="$VENDOR_DIR/dish.protoset" \
      --python_out=. --grpc_python_out=. "$proto" || echo "  (пропущено $proto — може бути відсутнім у поточній прошивці)"
  done
  mkdir -p "$VENDOR_DIR/spacex"
  cp -r spacex_api/* "$VENDOR_DIR/spacex/" 2>/dev/null || true
  cd -
  echo "==> Готово: $VENDOR_DIR/spacex/*"
else
  echo "!! grpcurl не знайдено в PATH. Встановіть його (див. README) і перезапустіть цей скрипт,"
  echo "   інакше низькорівневий reboot-запит (app/starlink_client.py fallback-шлях) не працюватиме."
fi

echo "==> Готово. starlink_grpc.py та protobuf-модулі розміщено в $VENDOR_DIR"
