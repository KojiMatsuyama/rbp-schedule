/* sw.js — Service Worker for RBP 防除スケジュール */
const CACHE_NAME = 'rbp-schedule-v2';

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

/* ── Fetch: cache-first for data/static, network-first for HTML ── */
self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  if (/^https?:\/\/.*\.(svg|woff2?)$/i.test(event.request.url)) return;

  const url = new URL(event.request.url, self.location.origin);
  const isDataOrStatic = url.pathname.endsWith('.js') ||
                         url.pathname.endsWith('.css') ||
                         url.pathname.endsWith('.png') ||
                         url.pathname.endsWith('.json') ||
                         url.pathname.endsWith('.ico');

  event.respondWith(
    (async () => {
      if (isDataOrStatic) {
        /* Data/static files: serve from cache immediately */
        const cached = await caches.match(event.request);
        if (cached) return cached;
        /* If not in cache, fetch and cache it */
        try {
          const resp = await fetch(event.request);
          if (resp.status === 200) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(event.request, resp.clone());
          }
          return resp;
        } catch {
          return new Response('', { status: 404 });
        }
      } else {
        /* HTML: network-first, fall back to cache */
        try {
          const netResp = await fetch(event.request);
          if (netResp.status === 200) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(event.request, netResp.clone());
          }
          return netResp;
        } catch {
          return caches.match('./index.html');
        }
      }
    })()
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
