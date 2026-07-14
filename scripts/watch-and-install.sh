#!/usr/bin/env bash
# Автоматично розпаковує starlink-monitor.tar.gz і запускає install.sh
# щойно файл архіву змінюється (новий/оновлений реліз проєкту).
#
# Призначений для запуску через systemd path-unit
# starlink-monitor-watch.path (реагує на PathChanged/PathModified для
# ARCHIVE_PATH), не через cron - інша подія спрацьовує миттєво, без
# затримки опитування.
#
# Обов'язково запускати з правами root (сам install.sh цього вимагає):
#   sudo bash watch-and-install.sh
set -euo pipefail

ARCHIVE_PATH="${STARLINK_ARCHIVE_PATH:?STARLINK_ARCHIVE_PATH не заданий (має підставлятись unit-файлом)}"
RUN_USER="${STARLINK_WATCH_USER:?STARLINK_WATCH_USER не заданий (має підставлятись unit-файлом)}"
EXTRACT_DIR="${STARLINK_WATCH_EXTRACT_DIR:-/tmp/starlink-monitor-install}"
STATE_FILE="/var/lib/starlink-monitor/last_archive_sha256"

if [[ $EUID -ne 0 ]]; then
  echo "Запустіть з sudo: sudo bash watch-and-install.sh"
  exit 1
fi

if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "==> Архів $ARCHIVE_PATH не знайдено, нічого робити"
  exit 0
fi

mkdir -p "$(dirname "$STATE_FILE")"

NEW_SHA="$(sha256sum "$ARCHIVE_PATH" | awk '{print $1}')"
OLD_SHA=""
if [[ -f "$STATE_FILE" ]]; then
  OLD_SHA="$(cat "$STATE_FILE")"
fi

if [[ "$NEW_SHA" == "$OLD_SHA" ]]; then
  echo "==> $ARCHIVE_PATH не змінився з останньої установки (sha256 збігається) — пропускаю"
  exit 0
fi

echo "==> Виявлено новий/змінений $ARCHIVE_PATH — запускаю встановлення"

rm -rf "$EXTRACT_DIR"
mkdir -p "$EXTRACT_DIR"
tar -xzf "$ARCHIVE_PATH" -C "$EXTRACT_DIR"

# Архів може містити верхній каталог (напр. starlink-monitor/) або
# файли проєкту напряму - шукаємо install.sh в обох варіантах.
INSTALL_SH="$(find "$EXTRACT_DIR" -maxdepth 3 -type f -name install.sh | head -n1)"
if [[ -z "$INSTALL_SH" ]]; then
  echo "!! У розпакованому архіві не знайдено scripts/install.sh — переривання"
  exit 1
fi

echo "==> Запускаю $INSTALL_SH від імені sudo (SUDO_USER=$RUN_USER)"
if sudo -u "$RUN_USER" true 2>/dev/null; then
  :
else
  echo "!! Користувача $RUN_USER не знайдено на цій системі — переривання"
  exit 1
fi

# install.sh сам вимагає sudo + непорожній $SUDO_USER != root - викликаємо
# його так само, як якби користувач $RUN_USER сам виконав
# "sudo bash install.sh". "|| INSTALL_EXIT=$?" потрібен тому, що
# set -e інакше обірве скрипт одразу на ненульовому коді виходу,
# не давши обробити помилку нижче.
INSTALL_EXIT=0
SUDO_USER="$RUN_USER" bash "$INSTALL_SH" || INSTALL_EXIT=$?

if [[ "$INSTALL_EXIT" -eq 0 ]]; then
  echo "$NEW_SHA" > "$STATE_FILE"
  echo "==> Встановлення завершено успішно, sha256 збережено"
else
  echo "!! install.sh завершився з помилкою (код $INSTALL_EXIT) — sha256 НЕ оновлено,"
  echo "   наступна перевірка спробує встановити знову"
fi

rm -rf "$EXTRACT_DIR"
exit "$INSTALL_EXIT"
