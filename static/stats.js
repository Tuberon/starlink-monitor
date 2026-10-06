function el(id) { return document.getElementById(id); }

let eventsClearedLocally = false;

async function refreshEvents() {
  if (eventsClearedLocally) return;
  try {
    const res = await fetch('/api/events?limit=300');
    const events = await res.json();
    const log = el('eventLog');
    if (!events.length) {
      log.innerHTML = `<div class="log-row"><span class="time">—</span><span class="kind">—</span><span>${t('no_events_yet')}</span></div>`;
      return;
    }
    // Події вже відсортовані найновіші-першими (БД: ORDER BY ts DESC) — групуємо за календарним
    // днем із заголовком дати при зміні дня. dateKey — за ЛОКАЛЬНОЮ датою (не ts/86400), щоб межа
    // групи збігалась із тим, як fmtTime() показує час (обидва спираються на часовий пояс
    // браузера).
    let html = '';
    let lastDateKey = null;
    for (const ev of events) {
      const d = new Date(ev.ts * 1000);
      const dateKey = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
      if (dateKey !== lastDateKey) {
        html += `<div class="log-date-header">${fmtDateHeader(ev.ts)}</div>`;
        lastDateKey = dateKey;
      }
      html += `
        <div class="log-row ${ev.success ? 'ok' : 'fail'}">
          <span class="time">${fmtTime(ev.ts)}</span>
          <span class="kind">${escapeHtml(ev.kind.replace(/_/g, ' '))}${ev.count > 1 ? ` ×${ev.count}` : ''}</span>
          <span>${escapeHtml(ev.message || '')}</span>
        </div>
      `;
    }
    log.innerHTML = html;
  } catch (e) {
    console.error('events refresh failed', e);
  }
}

function handleClearEvents() {
  eventsClearedLocally = true;
  el('eventLog').innerHTML = `<div class="log-row"><span class="time">—</span><span class="kind">—</span><span>${t('log_cleared_locally')}</span></div>`;
}

document.addEventListener('DOMContentLoaded', () => {
  el('clearEventsBtn').addEventListener('click', handleClearEvents);
  refreshEvents();
});
