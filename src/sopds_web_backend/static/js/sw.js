const CACHE_VERSION = 'sopds-v33';  // поднимать при каждом изменении статики: она отдаётся из кэша (Cache First)
const STATIC_CACHE = CACHE_VERSION + '-static';
const PAGE_CACHE   = CACHE_VERSION + '-pages';
const OFFLINE_URL  = '/web/offline/';
// Книги, сохранённые для чтения без сети (кнопка «⤓» в читалке /web/read/<id>/).
// Без версии — переживают обновления service worker; стираются при «Выходе»
// и при смене пользователя (sopds_main.html, convert/fb2_to_html.py).
const BOOKS_CACHE  = 'sopds-books';

const STATIC_ASSETS = [
  '/static/css/app.css',
  '/static/css/theme.css',
  '/static/js/vendor/jquery.min.js',
  '/static/js/vendor/foundation.min.js',
  '/static/js/vendor/htmx.min.js',
  '/static/js/app.js',
  '/static/images/sopds-logo.svg',
  '/static/images/sopds-modern-nocover.png',
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
          .filter(k => k.startsWith('sopds-') && k !== STATIC_CACHE && k !== PAGE_CACHE && k !== BOOKS_CACHE)
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

  // Читалка /web/read/<id>/ — Network First. Книгу, которую пользователь сам
  // сохранил для чтения без сети, без сети отдаём из BOOKS_CACHE, а с сетью —
  // обновляем там свежей копией. Несохранённые в кэш не попадают (как и
  // остальные страницы с данными пользователя — см. ниже).
  if (url.pathname.startsWith('/web/read/') && event.request.method === 'GET') {
    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (response.ok && !response.redirected) {
            const copy = response.clone();
            caches.open(BOOKS_CACHE).then(c =>
              c.match(event.request).then(saved => saved && c.put(event.request, copy)));
          }
          return response;
        })
        .catch(() =>
          caches.open(BOOKS_CACHE)
            .then(c => c.match(event.request))
            .then(saved => saved || caches.match(OFFLINE_URL))
        )
    );
    return;
  }

  // Страницы /web/ — Network First с офлайн-fallback.
  // Кешируем HTML ТОЛЬКО заведомо публичных страниц (белый список) — весь
  // остальной /web/* требует авторизации на сервере (@sopds_login/@sopds_admin),
  // и если сохранить такой ответ в PAGE_CACHE, service worker при сбое сети
  // отдаст его в обход сервера — то есть в обход проверки авторизации — любому,
  // у кого просто есть доступ к браузеру/устройству.
  if (url.pathname.startsWith('/web/')) {
    const PUBLIC_CACHEABLE_PATHS = ['/web/login/', '/web/offline/'];
    const cacheable = PUBLIC_CACHEABLE_PATHS.some(p => url.pathname.startsWith(p));

    event.respondWith(
      fetch(event.request)
        .then(response => {
          if (
            response.ok &&
            !response.redirected &&
            event.request.method === 'GET' &&
            cacheable
          ) {
            const clone = response.clone();
            caches.open(PAGE_CACHE).then(c => c.put(event.request, clone));
          }
          return response;
        })
        .catch(() =>
          (cacheable ? caches.match(event.request) : Promise.resolve())
            .then(cached => cached || caches.match(OFFLINE_URL))
        )
    );
    return;
  }
});
