function el(id) { return document.getElementById(id); }

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

let eventsClearedLocally = false;

async function refreshEvents() {
  if (eventsClearedLocally) return;
  try {
    const res = await fetch('/api/events?limit=500');
    const events = await res.json();
    const log = el('eventLog');
    if (!events.length) {
      log.innerHTML = '<div class="log-row"><span class="time">—</span><span class="kind">—</span><span>Подій ще немає</span></div>';
      return;
    }
    log.innerHTML = events.map(ev => `
      <div class="log-row ${ev.success ? 'ok' : 'fail'}">
        <span class="time">${fmtTime(ev.ts)}</span>
        <span class="kind">${ev.kind.replace(/_/g, ' ')}${ev.count > 1 ? ` ×${ev.count}` : ''}</span>
        <span>${ev.message || ''}</span>
      </div>
    `).join('');
  } catch (e) {
    console.error('events refresh failed', e);
  }
}

function handleClearEvents() {
  eventsClearedLocally = true;
  el('eventLog').innerHTML = '<div class="log-row"><span class="time">—</span><span class="kind">—</span><span>Журнал очищено на екрані</span></div>';
}

async function loadSpeedtestHistory() {
  try {
    const res = await fetch('/api/speedtest-history?limit=500');
    const data = await res.json();
    renderSpeedtest(data);
  } catch (e) {
    console.error('speedtest history load failed', e);
  }
}

function renderSpeedtest(data) {
  const latest = data.latest;
  const sub = el('speedtestSub');
  if (!data.enabled) {
    sub.textContent = 'вимкнено (увімкнути на сторінці Налаштування)';
  } else if (latest) {
    sub.textContent = `останній тест: ${fmtTime(latest.ts)}, сервер: ${latest.server_name || '—'}`;
  } else {
    sub.textContent = 'ще не запускався';
  }

  const log = el('speedtestLog');
  const rows = data.results || [];
  if (rows.length === 0) {
    log.innerHTML = '<div class="log-row"><span class="time">—</span><span class="kind">—</span><span>Ще немає результатів</span></div>';
    return;
  }
  log.innerHTML = rows.map(r => {
    if (!r.success) {
      return `<div class="log-row fail"><span class="time">${fmtTime(r.ts)}</span><span class="kind">помилка</span><span>${r.error || 'невідома помилка'}</span></div>`;
    }
    return `<div class="log-row ok"><span class="time">${fmtTime(r.ts)}</span><span class="kind">тест</span><span>⬇ ${r.download_mbps} · ⬆ ${r.upload_mbps} Мбіт/с · ping ${r.ping_ms}мс · ${r.server_name || ''}</span></div>`;
  }).join('');
}

document.addEventListener('DOMContentLoaded', () => {
  el('clearEventsBtn').addEventListener('click', handleClearEvents);
  refreshEvents();
  loadSpeedtestHistory();
});
