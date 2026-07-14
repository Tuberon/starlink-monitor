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

    const stateLabel = UPDATE_STATE_LABELS[latest.update_state] || latest.update_state || latest.state || '—';
    el('mState').textContent = stateLabel;
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
      return;
    }
    if (!latest.online) {
      el2.textContent = `недоступний (${latest.error || 'немає відповіді'})`;
      el('routerUpdateStateBadge').textContent = '—';
      return;
    }
    const sw = latest.software_version || '—';
    const hw = latest.hardware_version || '—';
    el2.textContent = `ПЗ: ${sw}  ·  Апаратна версія: ${hw}`;

    renderRouterUpdateStatus(latest);
    renderRouterAlerts(latest);
  } catch (e) {
    console.error('router status refresh failed', e);
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
        <span class="kind">${ev.kind.replace(/_/g, ' ')}</span>
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

async function loadTelegramConfig() {
  try {
    const res = await fetch('/api/telegram-config');
    const cfg = await res.json();
    updateTelegramUI(cfg.enabled);
    el('telegramChatIdsInput').value = (cfg.chat_ids || []).join(', ');
    if (cfg.token_set) {
      el('telegramTokenInput').placeholder = cfg.token_masked;
    }
  } catch (e) {
    console.error('telegram config load failed', e);
  }
}

function updateTelegramUI(enabled) {
  const toggle = el('telegramEnabledToggle');
  const badge = el('telegramStatusBadge');
  toggle.checked = enabled;
  badge.textContent = enabled ? 'увімкнено' : 'вимкнено';
  badge.classList.remove('state-idle', 'state-reboot');
  badge.classList.add(enabled ? 'state-idle' : 'state-reboot');
}

async function handleTelegramToggle(e) {
  const toggle = e.target;
  const enabled = toggle.checked;
  toggle.disabled = true;
  try {
    const res = await fetch('/api/telegram-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    const data = await res.json();
    if (data.success) {
      updateTelegramUI(enabled);
    } else {
      toggle.checked = !enabled;
    }
  } catch (e) {
    console.error('telegram toggle failed', e);
    toggle.checked = !enabled;
  } finally {
    toggle.disabled = false;
  }
}

async function handleTelegramSave() {
  const btn = el('telegramSaveBtn');
  const hint = el('telegramHint');
  const tokenInput = el('telegramTokenInput');
  const chatIdsInput = el('telegramChatIdsInput');

  const payload = { chat_ids: chatIdsInput.value };
  if (tokenInput.value.trim()) {
    payload.token = tokenInput.value.trim();
  }

  btn.disabled = true;
  hint.textContent = 'Зберігаю...';
  try {
    const res = await fetch('/api/telegram-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    hint.textContent = data.success ? 'Збережено' : 'Помилка збереження';
    if (data.success && tokenInput.value.trim()) {
      tokenInput.value = '';
      loadTelegramConfig();
    }
    refreshEvents();
  } catch (e) {
    hint.textContent = 'Помилка мережі при збереженні';
    console.error('telegram save failed', e);
  } finally {
    btn.disabled = false;
  }
}

async function handleTelegramTest() {
  const btn = el('telegramTestBtn');
  const hint = el('telegramHint');

  btn.disabled = true;
  hint.textContent = 'Надсилаю тестове повідомлення...';
  try {
    const res = await fetch('/api/telegram-test', { method: 'POST' });
    const data = await res.json();
    hint.textContent = data.message || (data.success ? 'Успішно' : 'Помилка');
  } catch (e) {
    hint.textContent = 'Помилка мережі при тестуванні';
    console.error('telegram test failed', e);
  } finally {
    btn.disabled = false;
  }
}

async function loadSignaturePhrases() {
  try {
    const res = await fetch('/api/signature-phrases');
    const data = await res.json();
    el('signaturePhrasesInput').value = data.text || '';
  } catch (e) {
    console.error('signature phrases load failed', e);
  }
}

async function handleSignaturePhrasesSave() {
  const btn = el('signaturePhrasesSaveBtn');
  const hint = el('signaturePhrasesHint');
  const text = el('signaturePhrasesInput').value;

  btn.disabled = true;
  hint.textContent = 'Зберігаю...';
  try {
    const res = await fetch('/api/signature-phrases', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await res.json();
    hint.textContent = data.message || (data.success ? 'Збережено' : 'Помилка');
    if (data.success) {
      loadSignaturePhrases();
    }
    refreshEvents();
  } catch (e) {
    hint.textContent = 'Помилка мережі при збереженні';
    console.error('signature phrases save failed', e);
  } finally {
    btn.disabled = false;
  }
}

function tick() {
  refreshStatus();
  refreshHistory();
  refreshSystemStatus();
  refreshRouterStatus();
  refreshEvents();
}

document.addEventListener('DOMContentLoaded', () => {
  initCharts();
  el('rebootBtn').addEventListener('click', handleReboot);
  el('clearEventsBtn').addEventListener('click', handleClearEvents);
  el('checkUpdatesBtn').addEventListener('click', handleCheckUpdates);
  el('autoRebootToggle').addEventListener('change', handleAutoRebootToggle);
  el('telegramEnabledToggle').addEventListener('change', handleTelegramToggle);
  el('telegramSaveBtn').addEventListener('click', handleTelegramSave);
  el('telegramTestBtn').addEventListener('click', handleTelegramTest);
  el('signaturePhrasesSaveBtn').addEventListener('click', handleSignaturePhrasesSave);
  loadConfigFlags();
  loadTelegramConfig();
  loadSignaturePhrases();
  tick();
  setInterval(tick, REFRESH_MS);
});
