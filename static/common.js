// Спільні функції для dashboard.js та stats.js. Підключати ПЕРЕД
// ними в HTML - обидва покладаються на fmtTime()/fmtAgo() як глобальні.

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('uk-UA', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// ts - Unix-timestamp у СЕКУНДАХ (як усюди в проєкті, не мілісекунди
// Date.now()). Той самий формат, що app/telegram_bot.py._fmt_ago() -
// консистентність між Telegram-повідомленнями й веб-UI.
function fmtAgo(ts) {
  if (!ts) return 'невідомо';
  const delta = (Date.now() / 1000) - ts;
  if (delta < 60) return 'щойно';
  if (delta < 3600) return `${Math.floor(delta / 60)} хв тому`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} год тому`;
  return `${Math.floor(delta / 86400)} дн тому`;
}

// Малий, самодостатній line-chart на голому Canvas API - БЕЗ
// сторонньої бібліотеки (Chart.js тощо): дашборд про мережу має
// лишатись робочим навіть без інтернету, коли dish саме offline
// (CDN-залежність тоді не завантажилась б). Кожна серія масштабується
// під свій власний min/max незалежно (без потреби dual-axis для
// різних одиниць вимірювання). Використовується на / (throughputChart)
// і на /stats (усі графіки трендів).
// options.beginAtZero - для метрик, що не бувають від'ємними (Mbps,
// ms, %) шкала від 0 дає чесніше уявлення про реальний масштаб
// коливань (без цього дрібні природні флуктуації "роздуваються" на
// всю висоту графіка, виглядаючи як драматичні піки). Числові
// підписи max-значення (кольором лінії) - без них графік показує
// лише форму, без жодної конкретної величини (неінформативно).
function _percentile(sortedArr, p) {
  const idx = (sortedArr.length - 1) * p;
  const lower = Math.floor(idx);
  const upper = Math.ceil(idx);
  if (lower === upper) return sortedArr[lower];
  return sortedArr[lower] + (sortedArr[upper] - sortedArr[lower]) * (idx - lower);
}

function drawLineChart(canvas, series, options = {}) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = rect.width, h = rect.height;
  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);

  const pad = 4;
  const chartH = h - 2 * pad;

  // Легкі горизонтальні grid-лінії (25/50/75%) - орієнтир для ока,
  // без чисел на них (щоб не захаращувати вузький графік).
  ctx.strokeStyle = 'rgba(147,164,195,0.12)';
  ctx.lineWidth = 1;
  for (const frac of [0.25, 0.5, 0.75]) {
    const y = pad + chartH * frac;
    ctx.beginPath();
    ctx.moveTo(pad, y);
    ctx.lineTo(w - pad, y);
    ctx.stroke();
  }

  let labelY = 12;
  for (const s of series) {
    const pts = s.data.filter(v => v != null);
    if (pts.length < 2) continue;
    let min = Math.min(...pts);
    const trueMax = Math.max(...pts);
    if (options.beginAtZero) min = Math.min(min, 0);

    // Один різкий викид (напр. початковий сплеск при підключенні)
    // стискає всю реально цікаву варіацію решти даних у тонку смужку
    // біля низу графіка - типова проблема raw-min/max масштабування.
    // Якщо реальний max суттєво (>1.5x) перевищує 95-й процентиль -
    // використовуємо процентиль як стелю шкали (той самий підхід, що
    // й у Grafana для "outlier-resistant" осі Y), а викид малюємо
    // "притиснутим" до верху графіка, не спотворюючи решту шкали.
    // Числовий підпис max - і далі РЕАЛЬНЕ значення (не обрізане).
    const sorted = [...pts].sort((a, b) => a - b);
    const p95 = _percentile(sorted, 0.95);
    const scaleMax = (p95 > 0 && trueMax > p95 * 1.5) ? p95 : trueMax;
    const range = (scaleMax - min) || 1;

    ctx.strokeStyle = s.color;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    let started = false;
    s.data.forEach((v, i) => {
      if (v == null) { started = false; return; }
      const clamped = Math.min(v, scaleMax);
      const x = (i / (s.data.length - 1)) * (w - 2 * pad) + pad;
      const y = h - pad - ((clamped - min) / range) * chartH;
      if (!started) { ctx.moveTo(x, y); started = true; }
      else { ctx.lineTo(x, y); }
    });
    ctx.stroke();

    // Максимальне значення серії - без цього графік показує лише
    // форму, без жодної конкретної величини.
    ctx.fillStyle = s.color;
    ctx.font = '10px monospace';
    ctx.textAlign = 'right';
    ctx.fillText(`макс ${trueMax.toFixed(1)}`, w - pad, labelY);
    labelY += 12;
  }
}
