#!/usr/bin/env bash
# Ручне оновлення Starlink Monitor: розпаковує starlink-monitor.tar.gz
# і запускає install.sh з нього.
#
# Простий запуск (архів за замовчуванням ~/starlink-monitor.tar.gz):
#   sudo bash update.sh
#
# З іншим шляхом до архіву:
#   sudo bash update.sh /шлях/до/starlink-monitor.tar.gz
#
# Скрипт сам визначає sha256 архіву й пропускає повторне встановлення,
# якщо файл не змінився з останнього успішного запуску - тобто його
# можна безпечно запускати повторно "про всяк випадок".
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Запустіть з sudo: sudo bash update.sh [шлях-до-архіву]"
  exit 1
fi

if [[ -z "${SUDO_USER:-}" || "$SUDO_USER" == "root" ]]; then
  echo "Запустіть через sudo від імені звичайного користувача"
  echo "(не з-під прямого root-логіна): sudo bash update.sh [шлях-до-архіву]"
  exit 1
fi
RUN_USER="$SUDO_USER"

RUN_USER_HOME="$(getent passwd "$RUN_USER" | cut -d: -f6)"
if [[ -z "$RUN_USER_HOME" ]]; then
  echo "!! Не вдалося визначити домашній каталог користувача $RUN_USER"
  exit 1
fi

ARCHIVE_PATH="${1:-$RUN_USER_HOME/starlink-monitor.tar.gz}"
EXTRACT_DIR="/tmp/starlink-monitor-update"
STATE_FILE="/var/lib/starlink-monitor/last_archive_sha256"

echo "==> Користувач: $RUN_USER"
echo "==> Архів: $ARCHIVE_PATH"

if [[ ! -f "$ARCHIVE_PATH" ]]; then
  echo "!! Архів $ARCHIVE_PATH не знайдено"
  echo "   Покладіть новий starlink-monitor.tar.gz у $RUN_USER_HOME/"
  echo "   або вкажіть інший шлях: sudo bash update.sh /шлях/до/архіву.tar.gz"
  exit 1
fi

mkdir -p "$(dirname "$STATE_FILE")"

NEW_SHA="$(sha256sum "$ARCHIVE_PATH" | awk '{print $1}')"
OLD_SHA=""
if [[ -f "$STATE_FILE" ]]; then
  OLD_SHA="$(cat "$STATE_FILE")"
fi

if [[ "$NEW_SHA" == "$OLD_SHA" ]]; then
  echo "==> Цей архів вже було встановлено (sha256 збігається з попереднім) — пропускаю"
  echo "    Якщо хочете перевстановити примусово, видаліть $STATE_FILE і запустіть знову."
  exit 0
fi

echo "==> Розпаковую архів"
rm -rf "$EXTRACT_DIR"
mkdir -p "$EXTRACT_DIR"
tar -xzf "$ARCHIVE_PATH" -C "$EXTRACT_DIR"

# Архів може містити верхній каталог (напр. starlink-monitor/) або
# файли проєкту напряму - шукаємо install.sh в обох варіантах.
INSTALL_SH="$(find "$EXTRACT_DIR" -maxdepth 3 -type f -name install.sh | head -n1)"
if [[ -z "$INSTALL_SH" ]]; then
  echo "!! У розпакованому архіві не знайдено scripts/install.sh — переривання"
  rm -rf "$EXTRACT_DIR"
  exit 1
fi

echo "==> Запускаю встановлення з $INSTALL_SH"
INSTALL_EXIT=0
SUDO_USER="$RUN_USER" bash "$INSTALL_SH" || INSTALL_EXIT=$?

if [[ "$INSTALL_EXIT" -eq 0 ]]; then
  echo "$NEW_SHA" > "$STATE_FILE"
  echo ""
  echo "==> Оновлення завершено успішно."
else
  echo ""
  echo "!! install.sh завершився з помилкою (код $INSTALL_EXIT)."
  echo "   Стан не збережено — повторний запуск update.sh спробує ще раз."
fi

rm -rf "$EXTRACT_DIR"
exit "$INSTALL_EXIT"
