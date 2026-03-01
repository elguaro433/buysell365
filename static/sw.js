// BuySell 365 — Service Worker v4.0
const CACHE_NAME = 'buysell365-v4';
const ASSETS = [
  '/static/bull-logo-192.png',
  '/static/bull-logo-512.png',
  '/manifest.json'
];

self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', e => {
  // Network first for API calls
  if (e.request.url.includes('/api/')) {
    e.respondWith(
      fetch(e.request).catch(() => new Response('{"ok":false,"error":"Sin conexión"}',
        {headers: {'Content-Type': 'application/json'}}))
    );
    return;
  }
  // Network first for HTML navigation (always get fresh page)
  if (e.request.mode === 'navigate' || e.request.url.endsWith('/')) {
    e.respondWith(
      fetch(e.request)
        .then(r => {
          const clone = r.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(e.request, clone));
          return r;
        })
        .catch(() => caches.match(e.request).then(r => r || caches.match('/')))
    );
    return;
  }
  // Cache first for static assets only (images, icons)
  e.respondWith(
    caches.match(e.request).then(r => r || fetch(e.request))
  );
});

// ===== PUSH NOTIFICATIONS =====
self.addEventListener('push', event => {
  let data = { title: 'BuySell 365', body: 'Nueva señal disponible', url: '/' };

  try {
    if (event.data) {
      data = event.data.json();
    }
  } catch (e) {
    data.body = event.data ? event.data.text() : 'Nueva señal disponible';
  }

  const options = {
    body: data.body,
    icon: data.icon || '/static/bull-logo-192.png',
    badge: data.badge || '/static/bull-logo-192.png',
    vibrate: [200, 100, 200],
    tag: 'buysell365-signal',
    renotify: true,
    data: { url: data.url || '/' },
    actions: [
      { action: 'open', title: 'Ver Señal' },
      { action: 'close', title: 'Cerrar' }
    ]
  };

  event.waitUntil(
    self.registration.showNotification(data.title || 'BuySell 365', options)
  );
});

// Handle notification click
self.addEventListener('notificationclick', event => {
  event.notification.close();

  if (event.action === 'close') return;

  const url = event.notification.data?.url || '/';

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(windowClients => {
        // Focus existing window if open
        for (const client of windowClients) {
          if (client.url.includes(self.location.origin) && 'focus' in client) {
            return client.focus();
          }
        }
        // Open new window
        if (clients.openWindow) {
          return clients.openWindow(url);
        }
      })
  );
});
