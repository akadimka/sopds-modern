"""Сессии: не стирать cookie по «устаревшему» запросу.

При смене ключа сессии (смена своего пароля — `update_session_auth_hash`,
вход) Django удаляет старую сессию, браузер получает новую cookie. Запрос,
отправленный ещё со старой cookie (фоновый опрос htmx, вторая вкладка),
не находит сессию, и стандартный SessionMiddleware отвечает командой
удалить cookie — а она приходит позже и стирает уже НОВУЮ: человека
разлогинивает сразу после смены пароля (сервер, 2026-10-23).

Здесь такой ответ cookie не трогает. Старая сессия на сервере всё равно
уничтожена — по старой cookie никто не войдёт. Удаление при выходе
(`logout` → `flush`, сессия изменена в этом запросе) — как обычно.
"""
from django.conf import settings
from django.contrib.sessions.middleware import (
    SessionMiddleware as DjangoSessionMiddleware,
)
from django.utils.cache import patch_vary_headers


class SessionMiddleware(DjangoSessionMiddleware):
    def process_response(self, request, response):
        session = getattr(request, "session", None)
        stale_cookie = (session is not None and settings.SESSION_COOKIE_NAME in request.COOKIES
                        and session.is_empty() and not session.modified)
        if stale_cookie:
            if session.accessed:
                patch_vary_headers(response, ("Cookie",))
            return response
        return super().process_response(request, response)


REFRESH_KEY = "_sopds_refreshed"
REFRESH_EVERY = 24 * 3600


class SessionRefreshMiddleware:
    """Вход продлевается, пока им пользуются: срок сессии (SESSION_COOKIE_AGE,
    2 недели) Django отсчитывает от последнего сохранения сессии, а сохраняет
    её только при изменении — человек, открывающий приложение каждый день,
    всё равно выходил через 2 недели после входа. Раз в сутки сессия
    помечается изменённой: срок и cookie продлеваются, запись в базу — одна в
    сутки, а не на каждый запрос (SESSION_SAVE_EVERY_REQUEST). Две недели
    без входа — по-прежнему выход."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import time

        session = getattr(request, "session", None)
        user = getattr(request, "user", None)
        if session is not None and user is not None and user.is_authenticated:
            now = int(time.time())
            if now - session.get(REFRESH_KEY, 0) >= REFRESH_EVERY:
                session[REFRESH_KEY] = now
        return self.get_response(request)
