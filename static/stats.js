function el(id) { return document.getElementById(id); }

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

document.addEventListener('DOMContentLoaded', () => {
  el('clearEventsBtn').addEventListener('click', handleClearEvents);
  refreshEvents();
  initCharts();
});

// ---- Графіки трендів (без сторонніх бібліотек - Starlink-дашборд
// має працювати навіть без інтернету, коли dish саме офлайн, тому
// CDN-залежність (напр. Chart.js) тут навмисно уникнена) ----

async function loadCharts(hours) {
  try {
    const res = await fetch(`/api/metrics-chart?hours=${hours}`);
    const data = await res.json();
    if (!data.length) return;

    const timestamps = data.map(d => d.bucket_ts);
    drawLineChart(el('chartPing'), [
      { data: data.map(d => d.ping_latency_ms), color: '#5ee6c4' },
      { data: data.map(d => d.ping_drop_ratio != null ? d.ping_drop_ratio * 100 : null), color: '#ff6b6b' },
    ], { timestamps });
    drawLineChart(el('chartObstruction'), [
      { data: data.map(d => d.obstruction_fraction != null ? d.obstruction_fraction * 100 : null), color: '#ffb454' },
    ], { beginAtZero: true, timestamps });
  } catch (e) {
    console.error('Помилка завантаження графіків:', e);
  }
}

function initCharts() {
  const buttons = document.querySelectorAll('#chartPeriodSelect button');
  buttons.forEach(btn => {
    btn.addEventListener('click', () => {
      buttons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      loadCharts(btn.dataset.hours);
    });
  });
  loadCharts(6);
}
