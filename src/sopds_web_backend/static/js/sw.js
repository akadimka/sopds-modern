const CACHE_VERSION = 'sopds-v14';
const STATIC_CACHE = CACHE_VERSION + '-static';
const PAGE_CACHE   = CACHE_VERSION + '-pages';
const OFFLINE_URL  = '/web/offline/';

const STATIC_ASSETS = [
  '/static/css/app.css',
  '/static/css/theme.css',
  '/static/js/vendor/jquery.min.js',
  '/static/js/vendor/foundation.min.js',
  '/static/js/vendor/htmx.min.js',
  '/static/js/app.js',
  '/static/images/sopds-ng-logo.png',
  '/static/images/sopds-ng-nocover.png',
  '/static/images/nocover-big.jpg',
  OFFLINE_URL,
];

// Установка — кэшируем статику и офлайн-страницу
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// Активация — удаляем старые кэши
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(k => k.startsWith('sopds-') && k !== STATIC_CACHE && k !== PAGE_CACHE)
          .map(k => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // Не трогаем: opds API, fb2parser, admin, i18n, htmx-partial запросы
  if (
    url.pathname.startsWith('/opds/') ||
    url.pathname.startsWith('/fb2parser/') ||
    url.pathname.startsWith('/admin/') ||
    url.pathname.startsWith('/i18n/') ||
    event.request.headers.get('HX-Request')
  ) {
    return;
  }

  // Статика — Cache First
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          if (response.ok) {
            const clone = response.clone();
            caches.open(STATIC_CACHE).then(c => c.put(event.request, clone));
          }
          return response;
        });
      })
    );
    return;
  }

  // Страницы /web/ — Network First с офлайн-fallback
  if (url.pathname.startsWith('/web/')) {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (response.ok && event.request.method === 'GET') {
            const clone = response.clone();
            caches.open(PAGE_CACHE).then(c => c.put(event.request, clone));
          }
          return response;
        })
        .catch(() =>
          caches.match(event.request)
            .then(cached => cached || caches.match(OFFLINE_URL))
        )
    );
    return;
  }
});
