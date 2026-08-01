// Перемикання світлої/темної теми. Синхронне застосування збереженого
// стану вже відбулось inline-скриптом у <head> (до завантаження CSS,
// щоб уникнути "спалаху" неправильної теми) - цей файл лише
// підключає toggle-перемикач на /settings (якщо він є на сторінці)
// і надає applyTheme() для самого перемикання.

// Кольори узгоджені з --sky-900 в static/style.css (dark/light) -
// статус-бар мобільного браузера має відповідати фактичному фону
// сторінки, інакше виглядає неузгоджено (темна смуга на світлій
// сторінці чи навпаки).
const THEME_COLOR_DARK = '#0b1220';
const THEME_COLOR_LIGHT = '#eef2f8';

function applyTheme(isLight) {
  if (isLight) {
    document.documentElement.setAttribute('data-theme', 'light');
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', isLight ? THEME_COLOR_LIGHT : THEME_COLOR_DARK);
  try {
    localStorage.setItem('theme', isLight ? 'light' : 'dark');
  } catch (e) {
    // Приватний режим/квота - тема застосується для поточного
    // перегляду сторінки, просто не збережеться між сесіями.
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.getElementById('lightThemeToggle');
  if (!toggle) return; // немає перемикача на цій сторінці (лише /settings)

  toggle.checked = document.documentElement.getAttribute('data-theme') === 'light';
  toggle.addEventListener('change', () => applyTheme(toggle.checked));
});
