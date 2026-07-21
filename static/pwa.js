// Реєстрація service worker для installability дашборду як PWA.
// Підключається на обох сторінках (index.html, settings.html) - окремий
// файл замість дублювання inline-скрипта в кожному шаблоні.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/static/sw.js").catch((err) => {
      console.warn("Service worker registration failed:", err);
    });
  });
}
