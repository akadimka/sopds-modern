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
