/* sw.js — Service Worker for RBP 防除スケジュール */
const CACHE_NAME = 'rbp-schedule-v1';

const ASSETS = [
  './index.html',
  './schedule_app.css',
  './qr-code.png',
  './framework/engine.js',
  './framework/mirror.js',
  './framework/rbp_core.js',
  './data/diseases.js',
  './data/pesticides.js',
  './data/eval_boxes.js',
  './rbp/eval_box_registry.js',
  './rbp/safety.js',
  './rbp/spec_matching.js',
  './rbp/spec_bridges.js',
  './rbp/prescription.js',
];

/* ── Install: pre-cache core assets ── */
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) =>
      cache.addAll(ASSETS).catch(() => {})
    )
  );
  self.skipWaiting();
});

/* ── Activate: stale-while-revalidate old caches ── */
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(
        names
          .filter((n) => n !== CACHE_NAME)
          .map((n) => caches.delete(n))
      )
    )
  );
  self.clients.claim();
});

/* ── Fetch: network-first, fall back to cache ── */
self.addEventListener('fetch', (event) => {
  /* Skip non-GET, non-resource URLs */
  if (event.request.method !== 'GET') return;
  if (/^https?:\/\/.*\.(svg|woff2?)$/i.test(event.request.url)) return;

  event.respondWith(
    fetch(event.request)
      .then((networkResponse) => {
        /* Cache the latest version */
        if (networkResponse && networkResponse.status === 200) {
          const clone = networkResponse.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        }
        return networkResponse;
      })
      .catch(() =>
        caches.match(event.request).then((cached) => cached || caches.match('./index.html'))
      )
  );
});

/* ── Push notifications (future) ── */
self.addEventListener('push', (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || 'RBP 防除スケジュール', {
      body: data.body || '新しいお知らせがあります',
      icon: 'icon-192.png',
    })
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow('./index.html'));
});
