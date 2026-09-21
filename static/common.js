// Спільні функції для dashboard.js та stats.js. Підключати ПЕРЕД
// ними в HTML - обидва покладаються на fmtTime()/fmtAgo() як глобальні.

// window.I18N/window.LANG вбудовуються сервером у кожну сторінку
// (Jinja2, app/i18n.py) - t() читає звідти, fallback на сам ключ,
// якщо сторінка з якоїсь причини не встигла отримати I18N (напр.
// цей файл підключено на сторінці без цієї ін'єкції) - видно
// відсутній переклад одразу, не порожній рядок. vars - опційна
// підстановка плейсхолдерів {name} у перекладеному рядку (той самий
// принцип, що Python-сторона i18n.t()'s **kwargs).
function t(key, vars) {
  let text = (window.I18N && window.I18N[key]) || key;
  if (vars) {
    for (const k in vars) text = text.replace(`{${k}}`, vars[k]);
  }
  return text;
}

// window.LANG визначає locale для toLocaleTimeString/
// toLocaleDateString - 'uk-UA' чи 'en-US', дефолт 'uk-UA' якщо
// сторінка ще не встигла отримати LANG (та сама причина, що в t()).
function _locale() {
  return window.LANG === 'en' ? 'en-US' : 'uk-UA';
}

function fmtTime(ts) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(_locale(), { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// Заголовок дня для групування журналу подій - "Сьогодні"/"Вчора"
// для двох найближчих днів (найчастіший випадок при регулярному
// перегляді), повна дата для решти. Порівняння за календарним днем
// у ЛОКАЛЬНОМУ часі браузера (не UTC) - як і решта дашборду.
function fmtDateHeader(ts) {
  const d = new Date(ts * 1000);
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  const isSameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (isSameDay(d, today)) return t('date_today');
  if (isSameDay(d, yesterday)) return t('date_yesterday');
  return d.toLocaleDateString(_locale(), { day: 'numeric', month: 'long', year: 'numeric' });
}

// ts - Unix-timestamp у СЕКУНДАХ (як усюди в проєкті, не мілісекунди
// Date.now()). Той самий формат, що app/telegram_bot.py._fmt_ago() -
// консистентність між Telegram-повідомленнями й веб-UI.
function fmtAgo(ts) {
  if (!ts) return t('ago_unknown');
  const delta = (Date.now() / 1000) - ts;
  if (delta < 60) return t('ago_just_now');
  if (delta < 3600) return `${Math.floor(delta / 60)} ${t('ago_min')}`;
  if (delta < 86400) return `${Math.floor(delta / 3600)} ${t('ago_hour')}`;
  return `${Math.floor(delta / 86400)} ${t('ago_day')}`;
}

// Екранування тексту перед вставкою в innerHTML - без цього довільний
// текст із зовнішніх джерел (WiFi-client hostname, який будь-який
// пристрій сам оголошує при підключенні до WiFi Starlink; невідомий
// alert-код від Starlink API; error/event-повідомлення від
// subprocess-команд) міг би виконати довільний JS у браузері того,
// хто відкриває дашборд (stored XSS) - реальний attack vector, що не
// потребує доступу до самого дашборду, лише знання WiFi-паролю.
// Ручна map-заміна (не document.createElement().textContent) - той
// самий принцип, що решта проєкту (offline-надійність, без зовнішніх
// залежностей): не покладається на DOM API, легко тестується напряму.
const _HTML_ESCAPE_MAP = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
function escapeHtml(text) {
  return String(text ?? '').replace(/[&<>"']/g, (ch) => _HTML_ESCAPE_MAP[ch]);
}
