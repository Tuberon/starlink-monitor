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

  let labelY = 6;
  const scaleInfo = [];  // {min, range, scaleMax} по кожній серії - потрібно і для hover-крапок (ті самі координати, що й для лінії)
  for (const s of series) {
    const pts = s.data.filter(v => v != null);
    if (pts.length < 2) { scaleInfo.push(null); continue; }
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
    scaleInfo.push({ min, range, scaleMax });

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
    // форму, без жодної конкретної величини. textBaseline='top' -
    // без цього замовчування ('alphabetic') не враховує descenders
    // українських літер (напр. "р", "ц"), через що підписи кількох
    // серій в одному графіку могли візуально зливатись один з одним.
    ctx.fillStyle = s.color;
    ctx.font = '10px monospace';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'top';
    ctx.fillText(`макс ${trueMax.toFixed(1)}`, w - pad, labelY);
    labelY += 14;
  }

  // Hover: вертикальна guideline + крапка на кожній серії в точці
  // наведення курсору. Координати - ТІ САМІ формули, що для лінії
  // вище (той самий scaleInfo), інакше крапка могла б не збігтися
  // з реальним положенням лінії на графіку.
  const hoverIndex = options.hoverIndex;
  if (hoverIndex != null && series[0] && series[0].data.length > 1) {
    const hoverX = (hoverIndex / (series[0].data.length - 1)) * (w - 2 * pad) + pad;
    ctx.strokeStyle = 'rgba(147,164,195,0.4)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(hoverX, pad);
    ctx.lineTo(hoverX, h - pad);
    ctx.stroke();

    series.forEach((s, si) => {
      const info = scaleInfo[si];
      const v = s.data[hoverIndex];
      if (!info || v == null) return;
      const clamped = Math.min(v, info.scaleMax);
      const y = h - pad - ((clamped - info.min) / info.range) * chartH;
      ctx.fillStyle = s.color;
      ctx.beginPath();
      ctx.arc(hoverX, y, 3, 0, Math.PI * 2);
      ctx.fill();
    });
  }

  // Зберігаємо стан рендерингу на самому canvas-елементі - hover-
  // обробник (приєднується нижче, ОДИН раз на canvas) читає це при
  // кожному русі миші, щоб перемалювати графік з guideline+крапками
  // без повторного fetch чи перерахунку percentile-логіки з нуля.
  canvas._chartState = { series, options, w, h, pad, chartH };
  _attachChartHover(canvas);
}

function _attachChartHover(canvas) {
  if (canvas._hoverAttached) return;
  canvas._hoverAttached = true;

  const tooltip = document.createElement('div');
  tooltip.className = 'chart-tooltip';
  canvas.parentElement.appendChild(tooltip);

  canvas.addEventListener('mousemove', (e) => {
    const state = canvas._chartState;
    if (!state || !state.series[0] || state.series[0].data.length < 2) return;

    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const dataLen = state.series[0].data.length;
    const frac = (mouseX - state.pad) / (state.w - 2 * state.pad);
    const idx = Math.max(0, Math.min(dataLen - 1, Math.round(frac * (dataLen - 1))));

    drawLineChart(canvas, state.series, { ...state.options, hoverIndex: idx });

    const rows = state.series.map(s => {
      const v = s.data[idx];
      const val = v == null ? '—' : v.toFixed(1);
      return `<div class="row"><span class="dot" style="background:${s.color}"></span>${val}</div>`;
    }).join('');
    const timeLabel = state.options.timestamps && state.options.timestamps[idx]
      ? fmtTime(state.options.timestamps[idx]) : '';
    tooltip.innerHTML = (timeLabel ? `<div class="row">${timeLabel}</div>` : '') + rows;
    tooltip.style.display = 'block';

    const tipX = Math.min(mouseX + 12, rect.width - 100);
    tooltip.style.left = `${Math.max(0, tipX)}px`;
    tooltip.style.top = '4px';
  });

  canvas.addEventListener('mouseleave', () => {
    const state = canvas._chartState;
    tooltip.style.display = 'none';
    if (state) {
      const { hoverIndex, ...rest } = state.options;
      drawLineChart(canvas, state.series, rest);
    }
  });
}
