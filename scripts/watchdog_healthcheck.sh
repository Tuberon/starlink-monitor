#!/usr/bin/env bash
# Watchdog для самого watchdog: якщо starlink-monitor.service зависає (deadlock/livelock, не crash),
# процес живий і Restart=always цього не бачить. /healthz (starlink-webui.service, окремий процес)
# перевіряє свіжість метрик у БД: без нових даних довше 3 циклів опитування — 503. Тут — примусовий
# `systemctl restart starlink-monitor.service` у відповідь. Запускається таймером
# starlink-monitor-healthcheck.timer (раз/хв) — рідше за поріг /healthz (POLL_INTERVAL_SEC*3, типово
# 30 с), щоб не реагувати на короткі затримки.
set -euo pipefail

# Скрипт працює від root, а env-файл пише RUN_USER: не source-имо його й не підвантажуємо через
# EnvironmentFile, а беремо лише порт (1-5 цифр).
WEBUI_PORT="$(sed -n 's/^STARLINK_WEBUI_PORT=\([0-9]\{1,5\}\)$/\1/p' /etc/starlink-monitor/env 2>/dev/null | tail -1 || true)"
WEBUI_PORT="${WEBUI_PORT:-8080}"

HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
  "http://127.0.0.1:${WEBUI_PORT}/healthz" 2>/dev/null || echo "000")"

# Лише 503 (монітор завис, веб відповідає). 000 = веб-процес не відповідає: рестарт монітора не допоможе,
# а щохвилинні рестарти скидали б лічильник невдач і авто-reboot тарілки ніколи б не спрацював.
if [[ "$HTTP_CODE" == "503" ]]; then
  echo "==> /healthz повернув 503 - примусовий перезапуск starlink-monitor.service"
  systemctl restart starlink-monitor.service
fi
