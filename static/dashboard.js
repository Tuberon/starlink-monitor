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

const UPDATE_STATE_LABELS = {
  'SOFTWARE_UPDATE_STATE_UNKNOWN': 'невідомо',
  'IDLE': 'немає оновлень',
  'FETCHING': 'завантаження',
  'PRE_CHECK': 'перевірка перед встановленням',
  'WRITING': 'встановлення',
  'POST_CHECK': 'перевірка після встановлення',
  'REBOOT_REQUIRED': 'очікує перезавантаження',
  'DISABLED': 'вимкнено',
  'FAULTED': 'помилка оновлення',
};

const ALERT_LABELS = {
  'motors_stuck': 'двигуни заклинило',
  'thermal_shutdown': 'аварійне вимкнення через перегрів',
  'thermal_throttle': 'обмеження через перегрів',
  'unexpected_location': 'неочікуване розташування',
  'mast_not_near_vertical': 'мачта не вертикальна',
  'slow_ethernet_speeds': 'низька швидкість Ethernet',
  'roaming': 'роумінг',
  'install_pending': 'очікує встановлення',
  'is_heating': 'обігрів увімкнено',
  'power_supply_thermal_throttle': 'обмеження блока живлення через перегрів',
  'is_power_save_idle': 'режим енергозбереження',
  'dbf_telem_stale': 'застарілі дані телеметрії',
  'low_motor_current': 'низький струм двигунів',
  'lower_signal_than_predicted': 'сигнал слабший за прогнозований',
  'slow_ethernet_speeds_100': 'швидкість Ethernet нижче 100 Мбіт/с',
  'obstruction_map_reset': 'карта перешкод скинута',
  'dish_water_detected': 'виявлено воду на dish',
  'router_water_detected': 'виявлено воду на роутері',
  'upsu_router_port_slow': 'повільний порт роутера UPSU',
  'no_ethernet_link': 'немає з\'єднання Ethernet',
};

const ROUTER_UPDATE_STATE_LABELS = {
  'NOT_RUN': 'немає оновлень',
  'GETTING_TARGET_VERSION': 'перевірка наявності оновлення',
  'DOWNLOADING_UPDATE_IMAGE': 'завантаження оновлення',
  'FLASHING': 'встановлення оновлення',
  'NO_UPDATE_REQUIRED': 'оновлення не потрібне',
  'REBOOT_PENDING': 'очікує перезавантаження',
  'GETTING_TARGET_VERSION_FAILED': 'помилка перевірки оновлення',
  'GETTING_TARGET_VERSION_EXHAUSTED': 'не вдалося перевірити оновлення',
  'NO_VALID_ARTIFACT': 'відсутній коректний файл оновлення',
  'ILLEGAL_ARTIFACT': 'некоректний файл оновлення',
  'DOWNLOADING_UPDATE_IMAGE_FAILED': 'помилка завантаження оновлення',
  'DOWNLOADING_UPDATE_IMAGE_EXHAUSTED': 'не вдалося завантажити оновлення',
  'FLASHING_FAILED': 'помилка встановлення оновлення',
};

const ROUTER_ALERT_LABELS = {
  'thermal_throttle': 'обмеження через перегрів',
  'install_pending': 'очікує встановлення',
  'freshly_fused': 'щойно активовано',
  'lan_eth_slow_link_10': 'повільне LAN Ethernet (10 Мбіт/с)',
  'lan_eth_slow_link_100': 'повільне LAN Ethernet (100 Мбіт/с)',
  'wan_eth_poor_connection': 'погане WAN Ethernet з\'єднання',
  'mesh_topology_changing_often': 'топологія mesh часто змінюється',
  'mesh_unreliable_backhaul': 'ненадійний mesh-канал',
  'radius_missing_process': 'відсутній процес RADIUS',
  'eth_switch_error': 'помилка Ethernet-комутатора',
  'poe_on_dish_unreachable': 'PoE на dish недоступне',
  'poe_fuse_blown': 'перегорів запобіжник PoE',
  'poe_router_overcurrent': 'перевищення струму PoE роутера',
  'poe_off_current_nominal': 'PoE вимкнено (номінальний струм)',
  'poe_vin_overvoltage': 'перевищення напруги живлення PoE',
  'poe_vin_undervoltage': 'занижена напруга живлення PoE',
  'high_cable_ping_drop_rate': 'високі втрати пакетів на кабелі',
  'sandbox_disabled': 'sandbox вимкнено',
  'only_overflight_blocked': 'заблоковано лише прольотний режим',
  'offline_networks_disabled': 'офлайн-мережі вимкнено',
  'wired_mesh_not_using_wan_iface': 'дротовий mesh не використовує WAN',
};

let throughputChart;

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

    const stateLabel = UPDATE_STATE_LABELS[latest.update_state] || latest.update_state || latest.state || '—';
    el('mState').textContent = stateLabel;
    el('mDown').innerHTML = `${latest.downlink_mbps ?? '—'}<span class="unit">Мбіт/с</span>`;
    el('mUp').innerHTML = `${latest.uplink_mbps ?? '—'}<span class="unit">Мбіт/с</span>`;
    el('mPing').innerHTML = `${latest.ping_latency_ms ?? '—'}<span class="unit">мс</span>`;
    el('stDishDownload').innerHTML = `${latest.downlink_mbps ?? '—'}<span class="unit">Мбіт/с</span>`;

    const dropPct = latest.ping_drop_ratio != null ? (latest.ping_drop_ratio * 100).toFixed(1) : null;
    el('mDrop').innerHTML = `${dropPct ?? '—'}<span class="unit">%</span>`;
    setValueClass(el('mDrop'), dropPct, 2, 10);

    const obsPct = latest.obstruction_fraction != null ? (latest.obstruction_fraction * 100).toFixed(1) : null;
    el('mObs').innerHTML = `${obsPct ?? '—'}<span class="unit">%</span>`;
    setValueClass(el('mObs'), obsPct, 2, 5);

    el('mUptime').textContent = fmtUptime(latest.uptime_s);

    const sw = latest.software_version || '—';
    const hw = latest.hardware_version || '—';
    el('mDishId').textContent = latest.dish_id || '—';
    el('mFirmwareDish').textContent = `ПЗ: ${sw}  ·  Апаратна версія: ${hw}`;

    renderUpdateStatus(latest);
    renderAlerts(latest);
  } catch (e) {
    console.error('status refresh failed', e);
  }
}

function renderUpdateStatus(latest) {
  const badge = el('updateStateBadge');
  const state = latest.update_state || 'SOFTWARE_UPDATE_STATE_UNKNOWN';
  badge.textContent = UPDATE_STATE_LABELS[state] || state;

  badge.classList.remove('state-idle', 'state-active', 'state-reboot');
  if (state === 'IDLE') {
    badge.classList.add('state-idle');
  } else if (state === 'REBOOT_REQUIRED' || state === 'FAULTED') {
    badge.classList.add('state-reboot');
  } else if (['FETCHING', 'PRE_CHECK', 'WRITING', 'POST_CHECK'].includes(state)) {
    badge.classList.add('state-active');
  }

  const progress = latest.update_progress_pct ?? 0;
  el('updateProgressFill').style.width = `${Math.max(0, Math.min(100, progress))}%`;
  el('updateProgressPct').textContent = `${progress.toFixed ? progress.toFixed(1) : progress}%`;

  const rebootFlag = el('updateRebootFlag');
  rebootFlag.hidden = !latest.update_requires_reboot;

  const installFlag = el('updateInstallFlag');
  installFlag.hidden = !latest.update_install_pending;
}

function renderAlerts(latest) {
  const body = el('alertsBody');
  const alerts = latest.active_alerts;
  if (!alerts || !alerts.length) {
    body.innerHTML = '<span class="alerts-none">активних попереджень немає</span>';
    return;
  }
  body.innerHTML = alerts
    .map(a => `<span class="alert-chip">${ALERT_LABELS[a] || a}</span>`)
    .join('');
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
  } catch (e) {
    console.error('system status refresh failed', e);
  }
}

async function refreshRouterStatus() {
  try {
    const res = await fetch('/api/router-status');
    const data = await res.json();
    const latest = data.latest;
    const el2 = el('mFirmwareRouter');
    if (!latest) {
      el2.textContent = 'ще не опитано';
      el('routerUpdateStateBadge').textContent = '—';
      el('routerAlertsBody').innerHTML = '<span class="alerts-none">ще не опитано</span>';
      renderRouterClients(null);
      return;
    }
    if (!latest.online) {
      el2.textContent = `недоступний (${latest.error || 'немає відповіді'})`;
      el('routerUpdateStateBadge').textContent = '—';
      renderRouterClients(latest.clients);
      return;
    }
    const sw = latest.software_version || '—';
    const hw = latest.hardware_version || '—';
    el2.textContent = `ПЗ: ${sw}  ·  Апаратна версія: ${hw}`;

    renderRouterUpdateStatus(latest);
    renderRouterAlerts(latest);
    renderRouterClients(latest.clients);
  } catch (e) {
    console.error('router status refresh failed', e);
  }
}

function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  if (h > 0) return `${h}г ${m}хв`;
  return `${m}хв`;
}

function signalClass(dbm) {
  if (dbm === null || dbm === undefined) return '';
  if (dbm >= -60) return 'good';
  if (dbm >= -75) return 'weak';
  return 'bad';
}

function renderRouterClients(clients) {
  const table = el('routerClientsTable');
  const countEl = el('routerClientsCount');
  const rows = (clients || []).map(c => `
    <div class="clients-row">
      <span class="client-name">${c.name || c.mac || '—'}</span>
      <span>${c.ip || '—'}</span>
      <span>${(c.iface || '').replace('WIFI_', '').replace('GHZ', ' ГГц') || '—'}</span>
      <span class="clients-signal ${signalClass(c.signal)}">${c.signal != null ? c.signal + ' дБм' : '—'}</span>
      <span>${fmtDuration(c.connected_s)}</span>
    </div>
  `).join('');

  const header = '<div class="clients-row clients-header"><span>Пристрій</span><span>IP</span><span>Діапазон</span><span>Сигнал</span><span>У мережі</span></div>';

  if (!clients) {
    countEl.textContent = '—';
    table.innerHTML = header + '<div class="clients-row"><span>ще не опитано</span></div>';
  } else if (clients.length === 0) {
    countEl.textContent = '0 підключено';
    table.innerHTML = header + '<div class="clients-row"><span>немає підключених клієнтів</span></div>';
  } else {
    countEl.textContent = `${clients.length} підключено`;
    table.innerHTML = header + rows;
  }
}

function renderRouterUpdateStatus(latest) {
  const badge = el('routerUpdateStateBadge');
  const state = latest.update_state || 'NOT_RUN';
  badge.textContent = ROUTER_UPDATE_STATE_LABELS[state] || state;

  badge.classList.remove('state-idle', 'state-active', 'state-reboot');
  if (state === 'NOT_RUN' || state === 'NO_UPDATE_REQUIRED') {
    badge.classList.add('state-idle');
  } else if (state === 'REBOOT_PENDING' || state.includes('FAILED') || state.includes('ILLEGAL')) {
    badge.classList.add('state-reboot');
  } else if (['GETTING_TARGET_VERSION', 'DOWNLOADING_UPDATE_IMAGE', 'FLASHING'].includes(state)) {
    badge.classList.add('state-active');
  }

  const progress = latest.update_progress_pct ?? 0;
  el('routerUpdateProgressFill').style.width = `${Math.max(0, Math.min(100, progress))}%`;
  el('routerUpdateProgressPct').textContent = `${progress.toFixed ? progress.toFixed(1) : progress}%`;

  const installFlag = el('routerUpdateInstallFlag');
  installFlag.hidden = !latest.update_install_pending;
}

function renderRouterAlerts(latest) {
  const body = el('routerAlertsBody');
  const alerts = latest.active_alerts;
  if (!alerts || !alerts.length) {
    body.innerHTML = '<span class="alerts-none">активних попереджень немає</span>';
    return;
  }
  body.innerHTML = alerts
    .map(a => `<span class="alert-chip">${ROUTER_ALERT_LABELS[a] || a}</span>`)
    .join('');
}

let eventsClearedLocally = false;

async function refreshEvents() {
  if (eventsClearedLocally) return;
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
        <span class="kind">${ev.kind.replace(/_/g, ' ')}${ev.count > 1 ? ` ×${ev.count}` : ''}</span>
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

async function handlePiReboot() {
  const btn = el('piRebootBtn');
  const hint = el('piControlHint');
  if (!confirm("Перезавантажити Raspberry Pi зараз? Дашборд стане недоступний на 1-2 хвилини.")) return;

  btn.disabled = true;
  hint.textContent = 'Надсилаю команду перезавантаження...';
  try {
    const res = await fetch('/api/system-reboot', { method: 'POST' });
    const data = await res.json();
    hint.textContent = data.success ? 'Pi перезавантажується...' : `Помилка: ${data.message}`;
  } catch (e) {
    hint.textContent = 'Команду надіслано (з\'єднання розірвано)';
  }
}

async function handlePiShutdown() {
  const btn = el('piShutdownBtn');
  const hint = el('piControlHint');
  if (!confirm("Вимкнути Raspberry Pi зараз? Для повторного увімкнення знадобиться фізичний доступ до пристрою (від'єднати й підключити живлення).")) return;
  if (!confirm("Підтвердіть ще раз: дашборд стане повністю недоступний до ручного увімкнення Pi.")) return;

  btn.disabled = true;
  hint.textContent = 'Надсилаю команду вимкнення...';
  try {
    const res = await fetch('/api/system-shutdown', { method: 'POST' });
    const data = await res.json();
    hint.textContent = data.success ? 'Pi вимикається...' : `Помилка: ${data.message}`;
  } catch (e) {
    hint.textContent = 'Команду надіслано (з\'єднання розірвано)';
  }
}

function handleClearEvents() {
  eventsClearedLocally = true;
  el('eventLog').innerHTML = '<div class="log-row"><span class="time">—</span><span class="kind">—</span><span>Журнал очищено на екрані</span></div>';
}

async function handleCheckUpdates() {
  const btn = el('checkUpdatesBtn');
  const hint = el('checkUpdatesHint');

  btn.disabled = true;
  btn.classList.add('spinning');
  hint.textContent = 'Опитую dish і роутер...';
  try {
    const res = await fetch('/api/check-updates', { method: 'POST' });
    const data = await res.json();
    if (data.success) {
      const dishState = UPDATE_STATE_LABELS[data.dish.update_state] || data.dish.update_state || 'н/д';
      const routerState = ROUTER_UPDATE_STATE_LABELS[data.router.update_state] || data.router.update_state || 'н/д';
      hint.textContent = `Готово. Dish: ${dishState}  ·  Роутер: ${routerState}`;
    } else {
      hint.textContent = 'Помилка перевірки';
    }
    tick();
  } catch (e) {
    hint.textContent = 'Помилка мережі при перевірці';
    console.error('check updates failed', e);
  } finally {
    btn.disabled = false;
    btn.classList.remove('spinning');
  }
}

async function loadConfigFlags() {
  try {
    const res = await fetch('/api/config');
    const cfg = await res.json();
    updateAutoRebootUI(cfg.auto_reboot_on_update_ready);
  } catch (e) {
    console.error('config load failed', e);
  }
}

function updateAutoRebootUI(enabled) {
  const toggle = el('autoRebootToggle');
  const badge = el('autoRebootStatusBadge');
  toggle.checked = enabled;
  badge.textContent = enabled ? 'увімкнено' : 'вимкнено';
  badge.classList.remove('state-idle', 'state-reboot');
  badge.classList.add(enabled ? 'state-idle' : 'state-reboot');
}

async function handleAutoRebootToggle(e) {
  const toggle = e.target;
  const enabled = toggle.checked;
  toggle.disabled = true;
  try {
    const res = await fetch('/api/auto-reboot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    const data = await res.json();
    updateAutoRebootUI(data.enabled);
    refreshEvents();
  } catch (e) {
    console.error('auto-reboot toggle failed', e);
    toggle.checked = !enabled;
  } finally {
    toggle.disabled = false;
  }
}

let historyTickCounter = 0;
const HISTORY_REFRESH_EVERY_N_TICKS = 5; // з REFRESH_MS=1000 це кожні ~5с -
// достатньо часто для плавних графіків, але без зайвих запитів тих самих
// 120 рядків між реальними новими опитуваннями dish (POLL_INTERVAL_SEC=10с)

async function loadSpeedtestHistory() {
  try {
    const res = await fetch('/api/speedtest-history?limit=20');
    const data = await res.json();
    renderSpeedtest(data);
  } catch (e) {
    console.error('speedtest history load failed', e);
  }
}

function renderSpeedtest(data) {
  const latest = data.latest;
  el('stDownload').innerHTML = `${latest ? latest.download_mbps : '—'}<span class="unit">Мбіт/с</span>`;
  el('stUpload').innerHTML = `${latest ? latest.upload_mbps : '—'}<span class="unit">Мбіт/с</span>`;
  el('stPing').innerHTML = `${latest ? latest.ping_ms : '—'}<span class="unit">мс</span>`;

  const sub = el('speedtestSub');
  if (!data.enabled) {
    sub.textContent = 'вимкнено (увімкнути на сторінці Налаштування)';
  } else if (latest) {
    sub.textContent = `останній тест: ${fmtTime(latest.ts)}, сервер: ${latest.server_name || '—'}`;
  } else {
    sub.textContent = 'ще не запускався';
  }

  const log = el('speedtestLog');
  const rows = (data.results || []).slice(0, 10);
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

async function handleSpeedtestRun() {
  const btn = el('speedtestRunBtn');
  const hint = el('speedtestHint');
  btn.disabled = true;
  hint.textContent = 'Виконую тест (10-30с)...';
  try {
    const res = await fetch('/api/speedtest-run', { method: 'POST' });
    const data = await res.json();
    hint.textContent = data.success
      ? `Готово: ⬇ ${data.download_mbps} Мбіт/с, ⬆ ${data.upload_mbps} Мбіт/с`
      : `Помилка: ${data.error || 'невідома'}`;
    loadSpeedtestHistory();
  } catch (e) {
    hint.textContent = 'Помилка мережі при запуску тесту';
    console.error('speedtest run failed', e);
  } finally {
    btn.disabled = false;
  }
}

let speedtestTickCounter = 0;
const SPEEDTEST_REFRESH_EVERY_N_TICKS = 60; // раз на ~60с - дані оновлюються рідко (типово раз на 30хв)

function tick() {
  refreshStatus();
  historyTickCounter++;
  if (historyTickCounter % HISTORY_REFRESH_EVERY_N_TICKS === 0) {
    refreshHistory();
  }
  refreshSystemStatus();
  refreshRouterStatus();
  refreshEvents();
  speedtestTickCounter++;
  if (speedtestTickCounter % SPEEDTEST_REFRESH_EVERY_N_TICKS === 0) {
    loadSpeedtestHistory();
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  el('rebootBtn').addEventListener('click', handleReboot);
  el('piRebootBtn').addEventListener('click', handlePiReboot);
  el('piShutdownBtn').addEventListener('click', handlePiShutdown);
  el('clearEventsBtn').addEventListener('click', handleClearEvents);
  el('checkUpdatesBtn').addEventListener('click', handleCheckUpdates);
  el('autoRebootToggle').addEventListener('change', handleAutoRebootToggle);
  el('speedtestRunBtn').addEventListener('click', handleSpeedtestRun);
  loadConfigFlags();
  loadSpeedtestHistory();
  tick();
  setInterval(tick, REFRESH_MS);
});
