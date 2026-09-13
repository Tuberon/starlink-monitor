function el(id) { return document.getElementById(id); }

let eventsClearedLocally = false;

async function refreshEvents() {
  if (eventsClearedLocally) return;
  try {
    const res = await fetch('/api/events?limit=300');
    const events = await res.json();
    const log = el('eventLog');
    if (!events.length) {
      log.innerHTML = '<div class="log-row"><span class="time">—</span><span class="kind">—</span><span>Подій ще немає</span></div>';
      return;
    }
    // Події вже відсортовані найновіші-першими (БД: ORDER BY ts DESC) -
    // групуємо за календарним днем, вставляючи заголовок дати при
    // кожній зміні дня. dateKey за локальною датою (не просто діленням
    // ts/86400) - інакше межа групи "поїхала" б відносно того, як
    // користувач реально бачить час у fmtTime() (обидва мають
    // спиратись на той самий, локальний часовий пояс браузера).
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
  el('eventLog').innerHTML = '<div class="log-row"><span class="time">—</span><span class="kind">—</span><span>Журнал очищено на екрані</span></div>';
}

document.addEventListener('DOMContentLoaded', () => {
  el('clearEventsBtn').addEventListener('click', handleClearEvents);
  refreshEvents();
});
