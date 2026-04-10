/**
 * HostAI Service Worker — PWA offline support + background sync
 * Caches static assets; falls through to network for API/dynamic routes.
 */

const CACHE_NAME = 'hostai-v1';
const STATIC_URLS = [
  '/static/css/',
  '/static/manifest.json',
];

// Cache static assets on install
self.addEventListener('install', function(event) {
  self.skipWaiting();
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return k !== CACHE_NAME; })
            .map(function(k) { return caches.delete(k); })
      );
    }).then(function() {
      return self.clients.claim();
    })
  );
});

// Network-first strategy for navigation, cache-first for static assets
self.addEventListener('fetch', function(event) {
  var url = new URL(event.request.url);

  // Always go to network for API, auth, webhooks, dynamic routes
  if (url.pathname.startsWith('/api/') ||
      url.pathname.startsWith('/webhook') ||
      url.pathname.startsWith('/billing') ||
      url.pathname.startsWith('/whatsapp') ||
      url.pathname.startsWith('/sms') ||
      url.pathname.startsWith('/email') ||
      event.request.method !== 'GET') {
    return; // Let browser handle it
  }

  // Cache-first for static CSS/JS/images
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then(function(cached) {
        if (cached) return cached;
        return fetch(event.request).then(function(response) {
          if (response && response.status === 200) {
            var clone = response.clone();
            caches.open(CACHE_NAME).then(function(cache) {
              cache.put(event.request, clone);
            });
          }
          return response;
        });
      })
    );
    return;
  }

  // Network-first for dashboard pages (with offline fallback)
  event.respondWith(
    fetch(event.request).catch(function() {
      return caches.match(event.request);
    })
  );
});

// Handle push notifications (for future use)
self.addEventListener('push', function(event) {
  if (!event.data) return;
  try {
    var data = event.data.json();
    var options = {
      body: data.body || 'New guest message',
      icon: '/static/img/icon-192.png',
      badge: '/static/img/icon-192.png',
      tag: data.tag || 'hostai-notification',
      data: { url: data.url || '/dashboard' },
      actions: [
        { action: 'open', title: 'View' },
        { action: 'dismiss', title: 'Dismiss' }
      ]
    };
    event.waitUntil(self.registration.showNotification(data.title || 'HostAI', options));
  } catch (e) {}
});

self.addEventListener('notificationclick', function(event) {
  event.notification.close();
  if (event.action === 'dismiss') return;
  var url = (event.notification.data && event.notification.data.url) || '/dashboard';
  event.waitUntil(clients.openWindow(url));
});
