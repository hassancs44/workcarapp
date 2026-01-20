const CACHE_NAME = "workcar-v2";

const URLS_TO_CACHE = [
  "/",
  "/login",
  "/work",
  "/dashboard",
  "/static/manifest.json",
  "/static/7s.jpg",
  "/static/icons/android/android-launchericon-192-192.png",
  "/static/icons/android/android-launchericon-512-512.png"
];

self.addEventListener("install", event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(URLS_TO_CACHE))
  );
});

self.addEventListener("activate", event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.map(k => k !== CACHE_NAME && caches.delete(k)))
    )
  );
});

self.addEventListener("fetch", event => {
  event.respondWith(
    caches.match(event.request).then(r => r || fetch(event.request))
  );
});
