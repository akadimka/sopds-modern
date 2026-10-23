"""Режим «На реконструкции»: сайт, OPDS и Telegram-бот закрыты для всех,
кроме суперпользователя на сайте (Настройки → Общие → «Обслуживание»).

Решение — по распознанному Django адресу (`resolve`), а не по тексту пути:
«?login», «/web/login/../main», «/web/login/x» и т. п. либо распознаются как
закрытая страница, либо не распознаются вовсе — и тогда тоже ремонт.
Открыты только страницы, без которых суперпользователь не войдёт (вход, код
второго фактора, обязательная смена пароля, выход) и служебные (язык,
сервис-воркер, «нет сети»). OPDS — закрыт и для суперпользователя: читалки
входят по Basic Auth в самом представлении, во время ремонта сайт — только
через браузер. Аварийный выход на сервере — `manage.py maintenance off`.
"""
from django.http import HttpResponse
from django.shortcuts import render
from django.urls import Resolver404, resolve
from django.utils.translation import gettext as _

RETRY_AFTER = "3600"

ALLOWED = {
    ("web", "login"), ("web", "login_code"), ("web", "logout"), ("web", "password_change_required"),
    ("web", "offline"), ("", "set_language"), ("", "service_worker"),
}


def is_on() -> bool:
    from opds_catalog.sopds_config import sopds_cfg as config
    return bool(config.SOPDS_MAINTENANCE)


def message() -> str:
    from opds_catalog.sopds_config import sopds_cfg as config
    return (config.SOPDS_MAINTENANCE_MESSAGE or "").strip() or _(
        "Maintenance is under way. The library will be back soon.")


def bot_notice():
    """Текст ответа бота во время ремонта; None — ремонта нет."""
    return f"🛠 {message()}" if is_on() else None


def _key(request):
    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None
    return match.namespace, match.url_name


def _closed(request, key):
    if request.headers.get("HX-Request"):              # htmx — перезагрузить страницу целиком
        response = HttpResponse(status=503)
        response["HX-Refresh"] = "true"
    elif key is not None and key[0] == "opds":           # читалки — простой текст
        response = HttpResponse(message(), status=503, content_type="text/plain; charset=utf-8")
    else:
        response = render(request, "sopds_maintenance.html", {"message": message()}, status=503)
    response["Retry-After"] = RETRY_AFTER
    return response


class MaintenanceMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not is_on():
            return self.get_response(request)
        key = _key(request)
        if key is not None and key[0] == "opds":
            return _closed(request, key)                  # OPDS — всем, и суперпользователю
        user = getattr(request, "user", None)
        if (user is not None and user.is_authenticated and user.is_superuser) or key in ALLOWED:
            return self.get_response(request)
        return _closed(request, key)
