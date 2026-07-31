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
  initCharts();
});

// ---- Графіки трендів (без сторонніх бібліотек - Starlink-дашборд
// має працювати навіть без інтернету, коли dish саме офлайн, тому
// CDN-залежність (напр. Chart.js) тут навмисно уникнена) ----

function drawLineChart(canvas, series) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = rect.width, h = rect.height;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const pad = 4;
  for (const s of series) {
    const pts = s.data.filter(v => v != null);
    if (pts.length < 2) continue;
    const min = Math.min(...pts);
    const max = Math.max(...pts);
    const range = (max - min) || 1;
    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    let started = false;
    s.data.forEach((v, i) => {
      if (v == null) { started = false; return; }
      const x = (i / (s.data.length - 1)) * (w - 2 * pad) + pad;
      const y = h - pad - ((v - min) / range) * (h - 2 * pad);
      if (!started) { ctx.moveTo(x, y); started = true; }
      else { ctx.lineTo(x, y); }
    });
    ctx.stroke();
  }
}

async function loadCharts(hours) {
  try {
    const res = await fetch(`/api/metrics-chart?hours=${hours}`);
    const data = await res.json();
    if (!data.length) return;

    drawLineChart(el('chartThroughput'), [
      { data: data.map(d => d.downlink_mbps), color: '#5ee6c4' },
      { data: data.map(d => d.uplink_mbps), color: '#ffb454' },
    ]);
    drawLineChart(el('chartPing'), [
      { data: data.map(d => d.ping_latency_ms), color: '#5ee6c4' },
      { data: data.map(d => d.ping_drop_ratio != null ? d.ping_drop_ratio * 100 : null), color: '#ff6b6b' },
    ]);
    drawLineChart(el('chartObstruction'), [
      { data: data.map(d => d.obstruction_fraction != null ? d.obstruction_fraction * 100 : null), color: '#ffb454' },
    ]);
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
