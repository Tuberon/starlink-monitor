// Мінімальний service worker для installability дашборду (PWA) - кешує
// лише статичні файли (CSS/JS/іконки), НЕ API-відповіді (динамічні дані
// Starlink завжди мають бути свіжими, кешування статусу було б небезпечним
// і оманливим при втраті мережі).
const CACHE_NAME = "dish-watch-static-v1";
const STATIC_ASSETS = [
  "/static/style.css",
  "/static/dashboard.js",
  "/static/settings.js",
  "/static/logo.png",
  "/static/favicon.ico",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  // Кешуємо лише статику (/static/*) з стратегією "мережа спочатку,
  // кеш як fallback" - завжди намагаємось отримати свіжу версію файлу,
  // кеш рятує лише коли мережа справді недоступна (напр. дашборд
  // відкрито, поки Pi перезавантажується).
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          const clone = resp.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return resp;
        })
        .catch(() => caches.match(event.request))
    );
  }
  // Усі інші запити (/, /settings, /api/*) - завжди напряму через мережу,
  // без кешування чи offline-fallback: показувати застарілі метрики
  // Starlink чи стан дашборду офлайн було б оманливим для користувача.
});
