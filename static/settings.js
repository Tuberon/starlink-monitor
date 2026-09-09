function el(id) { return document.getElementById(id); }

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

async function loadTargetVersions() {
  try {
    const res = await fetch('/api/target-versions');
    const data = await res.json();
    const dishInput = el('dishTargetVersionInput');
    const routerInput = el('routerTargetVersionInput');
    // Першочергове введення - якщо target ще не задано, показуємо
    // ПОТОЧНУ відому версію як стартове значення (не порожнє поле),
    // щоб не змушувати вводити версію вручну з нуля.
    dishInput.value = data.dish_target || data.dish_current || '';
    routerInput.value = data.router_target || data.router_current || '';
  } catch (e) {
    console.error('target versions load failed', e);
  }
}

async function handleTargetVersionsSave() {
  const btn = el('targetVersionsSaveBtn');
  const hint = el('targetVersionsHint');
  const dishTarget = el('dishTargetVersionInput').value.trim();
  const routerTarget = el('routerTargetVersionInput').value.trim();

  btn.disabled = true;
  hint.textContent = 'Зберігаю...';
  try {
    const res = await fetch('/api/target-versions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dish_target: dishTarget, router_target: routerTarget }),
    });
    const data = await res.json();
    hint.textContent = data.message || (data.success ? 'Збережено' : 'Помилка збереження');
  } catch (e) {
    hint.textContent = 'Помилка мережі при збереженні';
    console.error('target versions save failed', e);
  } finally {
    btn.disabled = false;
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

async function handleSettingsBackup() {
  const hint = el('settingsBackupHint');
  try {
    const res = await fetch('/api/settings-backup');
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-');
    a.href = url;
    a.download = `starlink-monitor-settings-${ts}.json`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    hint.textContent = 'Backup завантажено';
  } catch (e) {
    hint.textContent = 'Помилка завантаження backup';
    console.error('settings backup failed', e);
  }
}

function handleSettingsRestoreClick() {
  el('settingsRestoreFile').click();
}

async function handleSettingsRestoreFile(e) {
  const hint = el('settingsBackupHint');
  const file = e.target.files[0];
  if (!file) return;

  if (!confirm('Відновити налаштування з цього файлу? Поточні Telegram-налаштування, перемикач auto-reboot і перевизначені параметри моніторингу будуть перезаписані.')) {
    e.target.value = '';
    return;
  }

  try {
    const text = await file.text();
    const payload = JSON.parse(text);
    const res = await fetch('/api/settings-restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    hint.textContent = data.message || (data.success ? 'Відновлено' : 'Помилка');
    if (data.success) {
      loadTelegramConfig();
      loadEnvConfig();
    }
  } catch (err) {
    hint.textContent = 'Некоректний файл backup';
    console.error('settings restore failed', err);
  } finally {
    e.target.value = '';
  }
}


async function loadEnvConfig() {
  const form = el('envParamsForm');
  try {
    const res = await fetch('/api/env-config');
    const data = await res.json();

    // Групуємо за category, зберігаючи порядок першої появи кожної
    // категорії в даних з бекенду (не сортуємо алфавітно - порядок
    // з config_editor.py вже логічний: monitoring, reliability,
    // telegram, gpio).
    const groups = new Map();
    for (const p of data.params) {
      const cat = p.category || 'інше';
      if (!groups.has(cat)) groups.set(cat, []);
      groups.get(cat).push(p);
    }

    form.innerHTML = Array.from(groups.entries()).map(([cat, params]) => {
      const label = (data.category_labels && data.category_labels[cat]) || cat;
      const fields = params.map(p => {
        const value = p.overridden ? p.current : '';
        const placeholder = `за замовчуванням: ${p.default}${p.overridden ? '' : ' (активне зараз)'}`;
        return `
          <label class="tg-label" for="env_${p.key}">${p.label}</label>
          <input class="tg-input" type="text" id="env_${p.key}" data-key="${p.key}"
                 value="${value}" placeholder="${placeholder}">
        `;
      }).join('');
      return `<h3 class="env-category-head">${label}</h3>${fields}`;
    }).join('');
  } catch (e) {
    form.innerHTML = '<span class="hint">Помилка завантаження параметрів</span>';
    console.error('env config load failed', e);
  }
}

function collectEnvValues() {
  const inputs = document.querySelectorAll('#envParamsForm input[data-key]');
  const values = {};
  inputs.forEach(input => {
    values[input.dataset.key] = input.value;
  });
  return values;
}

async function handleEnvConfigSave() {
  const btn = el('envConfigSaveBtn');
  const hint = el('envConfigHint');
  btn.disabled = true;
  hint.textContent = 'Зберігаю...';
  try {
    const res = await fetch('/api/env-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: collectEnvValues() }),
    });
    const data = await res.json();
    hint.textContent = data.success
      ? 'Збережено. Щоб застосувати — перезапустіть сервіси (кнопка поруч) або вручну на Pi.'
      : `Помилка: ${data.message}`;
    if (data.success) loadEnvConfig();
  } catch (e) {
    hint.textContent = 'Помилка мережі при збереженні';
    console.error('env config save failed', e);
  } finally {
    btn.disabled = false;
  }
}

async function handleEnvConfigRestart() {
  const btn = el('envConfigRestartBtn');
  const hint = el('envConfigHint');
  if (!confirm('Зберегти параметри і перезапустити сервіси моніторингу та веб-інтерфейсу? Дашборд буде недоступний кілька секунд.')) return;

  btn.disabled = true;
  hint.textContent = 'Зберігаю...';
  try {
    const saveRes = await fetch('/api/env-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ values: collectEnvValues() }),
    });
    const saveData = await saveRes.json();
    if (!saveData.success) {
      hint.textContent = `Помилка: ${saveData.message}`;
      btn.disabled = false;
      return;
    }
    hint.textContent = 'Перезапускаю сервіси...';
    await fetch('/api/env-config-restart', { method: 'POST' });
    hint.textContent = 'Сервіси перезапущено.';
  } catch (e) {
    hint.textContent = 'Команду надіслано (з\'єднання могло розірватись під час рестарту)';
    console.error('env config restart failed', e);
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener('DOMContentLoaded', () => {
  el('telegramEnabledToggle').addEventListener('change', handleTelegramToggle);
  el('telegramSaveBtn').addEventListener('click', handleTelegramSave);
  el('telegramTestBtn').addEventListener('click', handleTelegramTest);
  el('settingsBackupBtn').addEventListener('click', handleSettingsBackup);
  el('settingsRestoreBtn').addEventListener('click', handleSettingsRestoreClick);
  el('settingsRestoreFile').addEventListener('change', handleSettingsRestoreFile);
  el('envConfigSaveBtn').addEventListener('click', handleEnvConfigSave);
  el('envConfigRestartBtn').addEventListener('click', handleEnvConfigRestart);
  el('targetVersionsSaveBtn').addEventListener('click', handleTargetVersionsSave);
  loadTelegramConfig();
  loadEnvConfig();
  loadTargetVersions();
});
