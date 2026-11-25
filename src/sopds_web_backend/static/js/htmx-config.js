// htmx.ajax без source: htmx 1.9 берёт источником <body> и вешает на него
// htmx-request — и на странице загораются ВСЕ .htmx-indicator (при «Обзор» в
// fb2parser появлялось «Starting...» у «Сканировать жанры», 2026-11-24).
// Подставляем общий невидимый источник вне форм: данные запроса и очередь
// запросов те же, что с <body>, но индикаторы страницы не трогаются.
(function () {
    var ajax = htmx.ajax;
    function ajaxSource() {
        var el = document.getElementById('htmx-ajax-source');
        if (!el) {
            el = document.createElement('span');
            el.id = 'htmx-ajax-source';
            el.hidden = true;
            document.body.appendChild(el);
        }
        return el;
    }
    htmx.ajax = function (verb, path, context) {
        if (context && typeof context === 'object' && !(context instanceof Element) && !context.source) {
            context = Object.assign({}, context, {source: ajaxSource()});
        }
        return ajax.call(this, verb, path, context);
    };
})();

// htmx Django CSRF integration
document.body.addEventListener('htmx:configRequest', function(evt) {
    var csrfToken = document.querySelector('[name=csrfmiddlewaretoken]');
    if (csrfToken) {
        evt.detail.headers['X-CSRFToken'] = csrfToken.value;
    }
});

// Reinitialize Foundation plugins after htmx content swap
document.body.addEventListener('htmx:afterSwap', function(evt) {
    if (typeof jQuery !== 'undefined' && typeof jQuery().foundation === 'function') {
        jQuery(evt.detail.target).foundation();
    }
});
