#!/usr/bin/env bash
# Watchdog для самого watchdog-механізму: якщо starlink-monitor.service
# зависає (deadlock/livelock, не crash) - процес технічно живий,
# systemd's Restart=always цього не бачить і не втручається, бо сам
# процес не завершився. /healthz (starlink-webui.service, окремий
# процес) вже перевіряє свіжість метрик у БД - якщо watchdog не пише
# нових даних довше 3 циклів опитування, /healthz поверне 503.
#
# Тут - примусовий `systemctl restart starlink-monitor.service` у
# відповідь на таку деградацію. Запускається через systemd timer
# (starlink-monitor-healthcheck.timer, раз/хв) - рідше за поріг
# застарілості /healthz (POLL_INTERVAL_SEC*3, типово 30с), щоб не
# реагувати на короткі транзієнтні затримки.
set -euo pipefail

WEBUI_PORT="${STARLINK_WEBUI_PORT:-8080}"

HTTP_CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 \
  "http://127.0.0.1:${WEBUI_PORT}/healthz" 2>/dev/null || echo "000")"

if [[ "$HTTP_CODE" != "200" ]]; then
  echo "==> /healthz повернув $HTTP_CODE (очікувалось 200) - примусовий перезапуск starlink-monitor.service"
  systemctl restart starlink-monitor.service
fi
