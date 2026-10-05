#!/usr/bin/env bash
# ОПЦІЙНИЙ інструмент: оновлює app/vendor/starlink_grpc.py до
# найновішої версії з upstream community-репозиторію
# sparky8512/starlink-grpc-tools (https://github.com/sparky8512/starlink-grpc-tools).
#
# starlink_grpc.py вже включений у проєкт (app/vendor/) для
# відтворюваності збірки - install.sh НЕ викликає цей скрипт
# автоматично. Запускай вручну лише якщо свідомо хочеш оновити до
# найновішої upstream-версії.
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
# Можна запускати вручну, або через systemd-сервіс
# starlink-grpc-fetch.service (встановлюється, але НЕ enabled/started
# автоматично — лише `sudo systemctl start starlink-grpc-fetch.service`
# за бажанням) — див. scripts/install.sh.
#
# Опції:
#   --wait-for-dish   Чекати доступності dish замість негайного виходу з помилкою
#                      (з ретраями, для використання при старті системи, коли
#                      WiFi-з'єднання зі Starlink Mini ще не встановлене).
#   --restart-services Перезапустити starlink-monitor/starlink-webui після
#                      успішного завантаження (потребує sudo-прав, налаштованих
#                      install.sh).
#   --commit=<sha>     Завантажити конкретну upstream-ревізію (40 hex), а не
#                      останню. Типово: остання ревізія репозиторію.
#
# БЕЗПЕКА оновлення (раніше файл качався прямо поверх робочого: обірване
# з'єднання лишало обрізаний starlink_grpc.py -> SyntaxError -> монітор не
# стартував): завантаження у ТИМЧАСОВИЙ файл; перевірки (розмір, синтаксис,
# наявність ChannelContext і get_status - те, що використовує наш клієнт);
# лише тоді резервна копія старого як starlink_grpc.py.prev і атомарна заміна.
# Завантаження - за незмінним URL з SHA коміту (не з рухомої гілки main), а
# походження (коміт, дата, sha256) записується в app/vendor/PROVENANCE.
set -euo pipefail

PROJECT_DIR="${STARLINK_PROJECT_DIR:-/opt/starlink-monitor}"
VENDOR_DIR="$PROJECT_DIR/app/vendor"
DISH_ADDR="${STARLINK_DISH_ADDR:-192.168.100.1:9200}"
MAX_WAIT_ATTEMPTS="${STARLINK_FETCH_MAX_ATTEMPTS:-30}"   # 30 * 10с = 5 хв
WAIT_INTERVAL_SEC="${STARLINK_FETCH_WAIT_INTERVAL:-10}"
# Змінні нижче - для тестів (локальний сервер) і форків; типові значення - upstream.
RAW_BASE="${STARLINK_GRPC_RAW_BASE:-https://raw.githubusercontent.com/sparky8512/starlink-grpc-tools}"
FEED_URL="${STARLINK_GRPC_FEED_URL:-https://github.com/sparky8512/starlink-grpc-tools/commits/main.atom}"
SKIP_ETH0="${STARLINK_FETCH_SKIP_ETH0:-0}"
MIN_SIZE_BYTES=20000

WAIT_FOR_DISH=0
RESTART_SERVICES=0
PINNED_COMMIT=""
for arg in "$@"; do
  case "$arg" in
    --wait-for-dish) WAIT_FOR_DISH=1 ;;
    --restart-services) RESTART_SERVICES=1 ;;
    --commit=*) PINNED_COMMIT="${arg#--commit=}" ;;
  esac
done
if [[ -n "$PINNED_COMMIT" && ! "$PINNED_COMMIT" =~ ^[0-9a-f]{40}$ ]]; then
  echo "!! --commit має бути повним SHA (40 hex), отримано: $PINNED_COMMIT"
  exit 2
fi

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


# wlan0 (WiFi Starlink) має нижчий route-metric за eth0 (див. install.sh) - тобто
# дефолтний маршрут завжди йде через wlan0 першим. Якщо супутниковий канал
# Starlink недоступний (обслуговування, погода, dish щойно ввімкнувся),
# інтернету через wlan0 немає, попри те що eth0 (домашня мережа) може його
# мати. Тому спершу явна спроба через eth0, потім дефолтний маршрут.
fetch_to() {  # $1 = URL, $2 = файл призначення
  if [[ "$SKIP_ETH0" != "1" ]] && ip link show eth0 >/dev/null 2>&1 && ip addr show eth0 | grep -q "inet "; then
    if curl -fsSL --interface eth0 --connect-timeout 10 "$1" -o "$2" 2>/dev/null; then
      return 0
    fi
    echo "   (через eth0 не вдалося, пробую дефолтний маршрут)" >&2
  fi
  curl -fsSL --connect-timeout 10 "$1" -o "$2"
}

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/starlink-grpc-fetch.XXXXXX")"
trap 'rm -rf "$WORK_DIR"' EXIT
mkdir -p "$VENDOR_DIR"
touch "$VENDOR_DIR/__init__.py"
PYTHON_BIN="$PROJECT_DIR/venv/bin/python"
[[ -x "$PYTHON_BIN" ]] || PYTHON_BIN="python3"

# 1) Яку ревізію качати: вказану --commit=, інакше остання (з Atom-стрічки комітів).
COMMIT="$PINNED_COMMIT"
COMMIT_DATE="unknown"
COMMIT_SUBJECT="unknown"
if [[ -z "$COMMIT" ]]; then
  echo "==> Визначаю останню ревізію upstream"
  if fetch_to "$FEED_URL" "$WORK_DIR/feed.atom" 2>/dev/null; then
    PARSED="$("$PYTHON_BIN" - "$WORK_DIR/feed.atom" <<'PY' || true
import re, sys
m = re.search(r'<entry>.*?Grit::Commit/([0-9a-f]{40}).*?<updated>([^<]+)</updated>.*?<title>\s*([^<]*?)\s*</title>',
              open(sys.argv[1], encoding="utf-8").read(), re.S)
if m:
    print(m.group(1)); print(m.group(2)[:10]); print(m.group(3)[:120])
PY
)"
    COMMIT="$(sed -n 1p <<<"$PARSED")"
    COMMIT_DATE="$(sed -n 2p <<<"$PARSED")"
    COMMIT_SUBJECT="$(sed -n 3p <<<"$PARSED")"
  fi
  if [[ -z "$COMMIT" ]]; then
    echo "!! Не вдалося визначити коміт - качаю з гілки main (походження буде 'unknown')"
    COMMIT=""; COMMIT_DATE="unknown"; COMMIT_SUBJECT="unknown"
  fi
fi

# 2) Завантаження у тимчасовий файл (не поверх робочого).
echo "==> Завантажую starlink_grpc.py (${COMMIT:-main})"
GRPC_URL="$RAW_BASE/${COMMIT:-main}/starlink_grpc.py"
CANDIDATE="$WORK_DIR/starlink_grpc.py"
if ! fetch_to "$GRPC_URL" "$CANDIDATE"; then
  echo "!! Завантаження не вдалося ($GRPC_URL). Робочий файл НЕ змінено."
  exit 1
fi

# 3) Перевірки ДО заміни: розмір, синтаксис Python, контракт (те, що використовує наш клієнт).
reject() { echo "!! Завантажений файл відхилено: $1. Робочий файл НЕ змінено."; exit 1; }
SIZE="$(wc -c < "$CANDIDATE")"
[[ "$SIZE" -ge "$MIN_SIZE_BYTES" ]] || reject "замалий ($SIZE Б < $MIN_SIZE_BYTES Б - обірване завантаження?)"
"$PYTHON_BIN" -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$CANDIDATE" 2>/dev/null \
  || reject "не є коректним Python (SyntaxError)"
grep -q '^class ChannelContext' "$CANDIDATE" || reject "немає класу ChannelContext (його використовує наш клієнт)"
grep -qE '^def get_status\(' "$CANDIDATE" || reject "немає функції get_status (її використовує наш клієнт)"

NEW_SHA="$(sha256sum "$CANDIDATE" | cut -d' ' -f1)"
CURRENT="$VENDOR_DIR/starlink_grpc.py"
if [[ -f "$CURRENT" ]] && [[ "$(sha256sum "$CURRENT" | cut -d' ' -f1)" == "$NEW_SHA" ]]; then
  echo "==> Вже актуально (sha256 збігається) - нічого не змінено"
  exit 0
fi

# 4) Резервна копія й атомарна заміна (mv у межах однієї файлової системи).
if [[ -f "$CURRENT" ]]; then
  cp -p "$CURRENT" "$VENDOR_DIR/starlink_grpc.py.prev"
fi
STAGED="$VENDOR_DIR/.starlink_grpc.py.new"
cp "$CANDIDATE" "$STAGED"
chmod 644 "$STAGED"
mv -f "$STAGED" "$CURRENT"

# 5) Походження: коміт, дата, sha256 (перевіряється тестом).
PROV_TMP="$VENDOR_DIR/.PROVENANCE.new"
cat > "$PROV_TMP" <<EOF2
# Походження app/vendor/starlink_grpc.py - СТОРОННІЙ код, не наш (див.
# docs/decisions-log.md: «Процеси, systemd, привілеї і vendor-файл»).
# Формат: KEY=value (рядки з # - коментарі). Файл пише scripts/fetch_starlink_grpc.sh;
# тест перевіряє, що SHA256 нижче збігається з фактичним файлом.
UPSTREAM_REPO=https://github.com/sparky8512/starlink-grpc-tools
UPSTREAM_PATH=starlink_grpc.py
UPSTREAM_COMMIT=${COMMIT:-unknown}
UPSTREAM_COMMIT_DATE=$COMMIT_DATE
UPSTREAM_COMMIT_SUBJECT=$COMMIT_SUBJECT
SHA256=$NEW_SHA
LICENSE=Unlicense (суспільне надбання; https://github.com/sparky8512/starlink-grpc-tools/blob/main/LICENSE)
RECORDED=$(date -u +%F)
EOF2
mv -f "$PROV_TMP" "$VENDOR_DIR/PROVENANCE"
echo "==> Оновлено: $CURRENT (ревізія ${COMMIT:-unknown}); попередня версія - starlink_grpc.py.prev"

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
