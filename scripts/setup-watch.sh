#!/usr/bin/env bash
# Одноразове встановлення механізму автоматичної установки: щойно
# starlink-monitor.tar.gz у домашньому каталозі користувача змінюється
# (напр. завантажили новий архів через scp/rsync), install.sh
# запускається автоматично - без ручного втручання надалі.
#
# Використання: sudo bash scripts/setup-watch.sh [шлях-до-архіву]
# Якщо шлях не вказано - за замовчуванням
# /home/<користувач-що-викликав-sudo>/starlink-monitor.tar.gz
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Запустіть з sudo: sudo bash scripts/setup-watch.sh"
  exit 1
fi

if [[ -z "${SUDO_USER:-}" || "$SUDO_USER" == "root" ]]; then
  echo "Запустіть через sudo від імені звичайного користувача"
  echo "(не з-під прямого root-логіна): sudo bash scripts/setup-watch.sh"
  exit 1
fi
RUN_USER="$SUDO_USER"

PROJECT_DIR="/opt/starlink-monitor"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -d "$PROJECT_DIR" ]]; then
  echo "!! $PROJECT_DIR не знайдено. Спочатку виконайте звичайне встановлення:"
  echo "   sudo bash scripts/install.sh"
  exit 1
fi

RUN_USER_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
if [[ -z "$RUN_USER_HOME" ]]; then
  echo "!! Не вдалося визначити домашній каталог користувача $RUN_USER"
  exit 1
fi

ARCHIVE_PATH="${1:-$RUN_USER_HOME/starlink-monitor.tar.gz}"

echo "==> Користувач: $RUN_USER"
echo "==> Відстежуваний архів: $ARCHIVE_PATH"

if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "!! Увага: $ARCHIVE_PATH поки не існує - watcher все одно"
  echo "   встановиться і спрацює, щойно файл з'явиться/зміниться."
fi

echo "==> Копіюю watch-and-install.sh у $PROJECT_DIR/scripts/"
cp "$SCRIPT_DIR/watch-and-install.sh" "$PROJECT_DIR/scripts/watch-and-install.sh"
chmod +x "$PROJECT_DIR/scripts/watch-and-install.sh"
chown "$RUN_USER:$RUN_USER" "$PROJECT_DIR/scripts/watch-and-install.sh"

echo "==> Встановлюю systemd path/service unit-файли (підстановка користувача та шляху архіву)"
sed \
  -e "s|__ARCHIVE_PATH__|$ARCHIVE_PATH|g" \
  "$PROJECT_DIR/systemd/starlink-monitor-watch.path" > /etc/systemd/system/starlink-monitor-watch.path
sed \
  -e "s|__ARCHIVE_PATH__|$ARCHIVE_PATH|g" \
  -e "s|__RUN_USER__|$RUN_USER|g" \
  "$PROJECT_DIR/systemd/starlink-monitor-watch.service" > /etc/systemd/system/starlink-monitor-watch.service

systemctl daemon-reload
systemctl enable --now starlink-monitor-watch.path

echo ""
echo "======================================================================"
echo " Автовідстеження увімкнено."
echo ""
echo " Тепер щоразу, коли $ARCHIVE_PATH зміниться, встановлення"
echo " запуститься автоматично."
echo ""
echo " Перевірити стан:  systemctl status starlink-monitor-watch.path"
echo " Переглянути лог:  journalctl -u starlink-monitor-watch.service -f"
echo "======================================================================"
