const REFRESH_MS = 5000;

const el = (id) => document.getElementById(id);

function fmtUptime(seconds) {
  if (!seconds && seconds !== 0) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 24) {
    const d = Math.floor(h / 24);
    return `${d}д ${h % 24}г`;
  }
  return `${h}г ${m}хв`;
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function setValueClass(node, value, warnAt, critAt, higherIsBad = true) {
  node.classList.remove('warn', 'crit');
  if (value === null || value === undefined) return;
  if (higherIsBad) {
    if (value >= critAt) node.classList.add('crit');
    else if (value >= warnAt) node.classList.add('warn');
  } else {
    if (value <= critAt) node.classList.add('crit');
    else if (value <= warnAt) node.classList.add('warn');
  }
}

let throughputChart, latencyChart;

function initCharts() {
  const commonOpts = {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 300 },
    interaction: { mode: 'index', intersect: false },
    scales: {
      x: {
        ticks: { color: '#5b6b8c', font: { family: 'JetBrains Mono', size: 10 }, maxTicksLimit: 8 },
        grid: { color: '#1b2740' },
      },
      y: {
        ticks: { color: '#5b6b8c', font: { family: 'JetBrains Mono', size: 10 } },
        grid: { color: '#1b2740' },
        beginAtZero: true,
      },
    },
    plugins: {
      legend: {
        labels: { color: '#93a4c3', font: { family: 'Space Grotesk', size: 11 }, boxWidth: 12, usePointStyle: true },
      },
    },
  };

  throughputChart = new Chart(el('throughputChart'), {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'Downlink Мбіт/с', data: [], borderColor: '#5ee6c4', backgroundColor: 'rgba(94,230,196,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
        { label: 'Uplink Мбіт/с', data: [], borderColor: '#7aa2ff', backgroundColor: 'rgba(122,162,255,0.06)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
      ],
    },
    options: commonOpts,
  });

  latencyChart = new Chart(el('latencyChart'), {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'Затримка мс', data: [], borderColor: '#ffb454', backgroundColor: 'rgba(255,180,84,0.06)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y' },
        { label: 'Втрати %', data: [], borderColor: '#ff6b6b', backgroundColor: 'transparent', fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y1' },
      ],
    },
    options: {
      ...commonOpts,
      scales: {
        ...commonOpts.scales,
        y1: {
          position: 'right',
          ticks: { color: '#5b6b8c', font: { family: 'JetBrains Mono', size: 10 } },
          grid: { display: false },
          beginAtZero: true,
        },
      },
    },
  });
}

async function refreshStatus() {
  try {
    const res = await fetch('/api/status');
    const data = await res.json();
    const latest = data.latest;
    const pill = el('statusPill');
    const ring = el('ring');

    if (!latest) return;

    if (latest.online) {
      pill.classList.add('online');
      pill.classList.remove('offline');
      el('statusText').textContent = 'ONLINE';
      ring.style.setProperty('--ring-pct', '100%');
    } else {
      pill.classList.add('offline');
      pill.classList.remove('online');
      el('statusText').textContent = 'OFFLINE';
      ring.style.setProperty('--ring-pct', '15%');
    }

    el('mState').textContent = latest.state || '—';
    el('mDown').innerHTML = `${latest.downlink_mbps ?? '—'}<span class="unit">Мбіт/с</span>`;
    el('mUp').innerHTML = `${latest.uplink_mbps ?? '—'}<span class="unit">Мбіт/с</span>`;
    el('mPing').innerHTML = `${latest.ping_latency_ms ?? '—'}<span class="unit">мс</span>`;

    const dropPct = latest.ping_drop_ratio != null ? (latest.ping_drop_ratio * 100).toFixed(1) : null;
    el('mDrop').innerHTML = `${dropPct ?? '—'}<span class="unit">%</span>`;
    setValueClass(el('mDrop'), dropPct, 2, 10);

    const obsPct = latest.obstruction_fraction != null ? (latest.obstruction_fraction * 100).toFixed(1) : null;
    el('mObs').innerHTML = `${obsPct ?? '—'}<span class="unit">%</span>`;
    setValueClass(el('mObs'), obsPct, 2, 5);

    el('mUptime').textContent = fmtUptime(latest.uptime_s);
    el('mAvail').innerHTML = `${data.uptime_24h_pct ?? '—'}<span class="unit">%</span>`;
    setValueClass(el('mAvail'), data.uptime_24h_pct, 99, 95, false);
  } catch (e) {
    console.error('status refresh failed', e);
  }
}

async function refreshHistory() {
  try {
    const res = await fetch('/api/history?limit=120');
    const rows = await res.json();
    const labels = rows.map(r => fmtTime(r.ts));

    throughputChart.data.labels = labels;
    throughputChart.data.datasets[0].data = rows.map(r => r.downlink_mbps);
    throughputChart.data.datasets[1].data = rows.map(r => r.uplink_mbps);
    throughputChart.update('none');

    latencyChart.data.labels = labels;
    latencyChart.data.datasets[0].data = rows.map(r => r.ping_latency_ms);
    latencyChart.data.datasets[1].data = rows.map(r => (r.ping_drop_ratio || 0) * 100);
    latencyChart.update('none');
  } catch (e) {
    console.error('history refresh failed', e);
  }
}

async function refreshEvents() {
  try {
    const res = await fetch('/api/events?limit=30');
    const events = await res.json();
    const log = el('eventLog');
    if (!events.length) {
      log.innerHTML = '<div class="log-row"><span class="time">—</span><span class="kind">—</span><span>Подій ще немає</span></div>';
      return;
    }
    log.innerHTML = events.map(ev => `
      <div class="log-row ${ev.success ? 'ok' : 'fail'}">
        <span class="time">${fmtTime(ev.ts)}</span>
        <span class="kind">${ev.kind.replace('_', ' ')}</span>
        <span>${ev.message || ''}</span>
      </div>
    `).join('');
  } catch (e) {
    console.error('events refresh failed', e);
  }
}

async function handleReboot() {
  const btn = el('rebootBtn');
  const hint = el('rebootHint');
  if (!confirm('Перезавантажити Starlink dish зараз? Зв\'язок буде втрачено на ~1-2 хвилини.')) return;

  btn.disabled = true;
  hint.textContent = 'Надсилаю команду reboot...';
  try {
    const res = await fetch('/api/reboot-dish', { method: 'POST' });
    const data = await res.json();
    hint.textContent = data.success ? 'Команда reboot надіслана успішно' : `Помилка: ${data.message}`;
    refreshEvents();
  } catch (e) {
    hint.textContent = 'Помилка мережі при надсиланні команди';
  } finally {
    setTimeout(() => { btn.disabled = false; }, 5000);
  }
}

function tick() {
  refreshStatus();
  refreshHistory();
  refreshEvents();
}

document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  el('rebootBtn').addEventListener('click', handleReboot);
  tick();
  setInterval(tick, REFRESH_MS);
});
