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
