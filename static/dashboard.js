const REFRESH_MS = 1000;

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

let throughputChart, latencyChart, cpuTempChart, memChart, diskChart;

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

  cpuTempChart = new Chart(el('cpuTempChart'), {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'CPU %', data: [], borderColor: '#5ee6c4', backgroundColor: 'rgba(94,230,196,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y' },
        { label: 'Температура °C', data: [], borderColor: '#ff6b6b', backgroundColor: 'transparent', fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2, yAxisID: 'y1' },
      ],
    },
    options: {
      ...commonOpts,
      scales: {
        ...commonOpts.scales,
        y: { ...commonOpts.scales.y, max: 100 },
        y1: {
          position: 'right',
          ticks: { color: '#5b6b8c', font: { family: 'JetBrains Mono', size: 10 } },
          grid: { display: false },
          beginAtZero: true,
        },
      },
    },
  });

  memChart = new Chart(el('memChart'), {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'Зайнято МБ', data: [], borderColor: '#7aa2ff', backgroundColor: 'rgba(122,162,255,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
        { label: 'Вільно МБ', data: [], borderColor: '#5ee6c4', backgroundColor: 'transparent', fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
      ],
    },
    options: commonOpts,
  });

  diskChart = new Chart(el('diskChart'), {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        { label: 'Зайнято ГБ', data: [], borderColor: '#ffb454', backgroundColor: 'rgba(255,180,84,0.08)', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
        { label: 'Вільно ГБ', data: [], borderColor: '#5ee6c4', backgroundColor: 'transparent', fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
      ],
    },
    options: commonOpts,
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

async function refreshSystemStatus() {
  try {
    const res = await fetch('/api/system-status');
    const data = await res.json();
    const latest = data.latest;
    if (!latest) return;

    el('sUptime').textContent = fmtUptime(latest.uptime_s);

    const temp = latest.temp_c;
    el('sTemp').innerHTML = `${temp ?? '—'}<span class="unit">°C</span>`;
    setValueClass(el('sTemp'), temp, 70, 80);

    const cpu = latest.cpu_percent;
    el('sCpu').innerHTML = `${cpu ?? '—'}<span class="unit">%</span>`;
    setValueClass(el('sCpu'), cpu, 80, 95);

    const memPct = latest.mem_total_mb ? (latest.mem_used_mb / latest.mem_total_mb * 100).toFixed(1) : null;
    el('sMem').innerHTML = `${memPct ?? '—'}<span class="unit">%</span>`;
    setValueClass(el('sMem'), memPct, 80, 95);

    const diskPct = latest.disk_total_gb ? (latest.disk_used_gb / latest.disk_total_gb * 100).toFixed(1) : null;
    el('sDisk').innerHTML = `${diskPct ?? '—'}<span class="unit">%</span>`;
    setValueClass(el('sDisk'), diskPct, 80, 95);

    el('memSub').textContent = latest.mem_total_mb
      ? `${latest.mem_used_mb.toFixed(0)} МБ зайнято з ${latest.mem_total_mb.toFixed(0)} МБ`
      : '—';
    el('diskSub').textContent = latest.disk_total_gb
      ? `${latest.disk_used_gb.toFixed(1)} ГБ зайнято з ${latest.disk_total_gb.toFixed(1)} ГБ`
      : '—';
  } catch (e) {
    console.error('system status refresh failed', e);
  }
}

async function refreshSystemHistory() {
  try {
    const res = await fetch('/api/system-history?limit=120');
    const rows = await res.json();
    const labels = rows.map(r => fmtTime(r.ts));

    cpuTempChart.data.labels = labels;
    cpuTempChart.data.datasets[0].data = rows.map(r => r.cpu_percent);
    cpuTempChart.data.datasets[1].data = rows.map(r => r.temp_c);
    cpuTempChart.update('none');

    memChart.data.labels = labels;
    memChart.data.datasets[0].data = rows.map(r => r.mem_used_mb);
    memChart.data.datasets[1].data = rows.map(r => r.mem_free_mb);
    memChart.update('none');

    diskChart.data.labels = labels;
    diskChart.data.datasets[0].data = rows.map(r => r.disk_used_gb);
    diskChart.data.datasets[1].data = rows.map(r => r.disk_free_gb);
    diskChart.update('none');
  } catch (e) {
    console.error('system history refresh failed', e);
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

async function handleClearEvents() {
  const btn = el('clearEventsBtn');
  if (!confirm('Очистити весь журнал подій? Цю дію не можна скасувати.')) return;

  btn.disabled = true;
  try {
    await fetch('/api/events', { method: 'DELETE' });
    refreshEvents();
  } catch (e) {
    console.error('clear events failed', e);
  } finally {
    btn.disabled = false;
  }
}

function tick() {
  refreshStatus();
  refreshHistory();
  refreshSystemStatus();
  refreshSystemHistory();
  refreshEvents();
}

document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  el('rebootBtn').addEventListener('click', handleReboot);
  el('clearEventsBtn').addEventListener('click', handleClearEvents);
  tick();
  setInterval(tick, REFRESH_MS);
});
