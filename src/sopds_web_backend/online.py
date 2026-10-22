"""Кто сейчас на сайте — для шапки («Сейчас на сайте: (N)» со списком).

«На сайте» — был хоть один запрос за последние WINDOW: через сайт (и PWA,
fb2parser, /admin/) или через читалку (OPDS). Telegram не считается.
Считаются люди, а не устройства.

Отметки — в общем кэше (memcached на сервере — общий для всех процессов
gunicorn): по ключу на пользователя и канал, время жизни — WINDOW, так что
старые отметки исчезают сами; в базу ничего не пишется. Опрос самой шапки
(обновление числа раз в минуту) активностью не считается — иначе открытая и
забытая вкладка держала бы пользователя «на сайте» вечно."""
import time

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import Resolver404, resolve

WINDOW = 5 * 60                          # секунд
CHANNELS = ("web", "opds")
_PREFIX = "sopds-online:"
_NOT_ACTIVITY = {("web", "online")}      # (пространство имён, имя адреса)


def _key(user_id, channel):
    return f"{_PREFIX}{user_id}:{channel}"


def mark(user_id, channel):
    cache.set(_key(user_id, channel), time.time(), WINDOW)


def forget(user_id, channel):
    cache.delete(_key(user_id, channel))


def online_users():
    """[{username, channels, minutes_ago}] — последние активные первыми."""
    users = dict(get_user_model().objects.filter(is_active=True).values_list("pk", "username"))
    seen = cache.get_many([_key(pk, ch) for pk in users for ch in CHANNELS])
    now = time.time()
    result = []
    for pk, username in users.items():
        marks = {ch: seen[_key(pk, ch)] for ch in CHANNELS
                 if _key(pk, ch) in seen and now - seen[_key(pk, ch)] < WINDOW}
        if marks:
            last = max(marks.values())
            result.append({"username": username, "channels": [ch for ch in CHANNELS if ch in marks],
                           "minutes_ago": int((now - last) // 60), "_last": last})
    result.sort(key=lambda u: (-u["_last"], u["username"].casefold()))
    for u in result:
        del u["_last"]
    return result


def _mark_request(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return False
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return False
    if (match.namespace, match.url_name) in _NOT_ACTIVITY:
        return False
    # Канал — по способу входа, не по адресу: обложки на страницах сайта
    # тоже идут с /opds/. Сессию читалки помечает вход по Basic Auth.
    session = getattr(request, "session", None)
    from .opds_passwords import SESSION_KEY
    mark(user.pk, "opds" if session is not None and session.get(SESSION_KEY) is not None else "web")
    return True


class OnlineMiddleware:
    """Отмечает вошедшего пользователя до ответа — страница уже показывает
    его самого; читалка (Basic Auth) входит в самом представлении — её
    отмечаем после."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        marked = _mark_request(request)
        response = self.get_response(request)
        if not marked and response.status_code < 400:
            _mark_request(request)
        return response
