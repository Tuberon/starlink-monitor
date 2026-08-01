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
