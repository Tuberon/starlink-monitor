// Реєстрація service worker для installability дашборду як PWA.
// Підключається на всіх сторінках (index.html, settings.html,
// stats.html) - окремий файл замість дублювання inline-скрипта.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/static/sw.js").catch((err) => {
      console.warn("Service worker registration failed:", err);
    });
  });
}
