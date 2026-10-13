"""Декораторы для SOPDS."""

import base64
import binascii
from functools import wraps

from django.contrib import auth
from django.http import HttpResponse

from opds_catalog.sopds_config import sopds_cfg as config


def sopds_auth_validate(view_function):
    """Декоратор для проверки и аутентификации пользователей."""

    @wraps(view_function)
    def wrap(*args, **kwargs):
        if not config.SOPDS_AUTH:
            return view_function(*args, **kwargs)

        def _unauthed():
            response = HttpResponse(
                """<html><title>Auth required</title><body>
                                    <h1>Authorization Required</h1></body></html>""",
                content_type="text/html",
            )
            response["WWW-Authenticate"] = 'Basic realm="OPDS"'
            response.status_code = 401
            return response

        if (
            args
            and hasattr(args[0], "__class__")
            and hasattr(args[0], view_function.__name__)
        ):
            request = args[1]
        else:
            request = args[0]

        header = "HTTP_AUTHORIZATION"
        if request.user.is_authenticated:
            return view_function(*args, **kwargs)

        try:
            authentication = request.META[header]
        except KeyError:
            return _unauthed()
        try:
            (auth_meth, auth_data) = authentication.split(" ", 1)
        except ValueError:
            return _unauthed()

        if "basic" != auth_meth.lower():
            return _unauthed()
        # Мусор в заголовке (не base64, не UTF-8, без ":") — это 401, не 500.
        try:
            auth_data = base64.b64decode(auth_data.strip(), validate=True).decode("utf-8")
            username, password = auth_data.split(":", 1)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            return _unauthed()

        # Сначала — пароль для читалок; основной пароль сайта — только если
        # это не запрещено настройкой (SOPDS_OPDS_APP_PASSWORDS_ONLY).
        from sopds_web_backend import opds_passwords

        record = opds_passwords.authenticate(username, password)
        if record is not None:
            user, mark = record.user, opds_passwords.session_mark(record.token_hash)
        elif not config.SOPDS_OPDS_APP_PASSWORDS_ONLY:
            user, mark = auth.authenticate(request, username=username, password=password), opds_passwords.MARK_MAIN
        else:
            user = None
        if user and user.is_active:
            # Сессия нужна, чтобы не проверять пароль на каждый запрос читалки
            # (обложки, страницы фида), но действует только для /opds/ —
            # иначе вход через читалку обходил бы код входа администратора
            # на сайте (OpdsOnlySessionMiddleware).
            auth.login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            request.session[opds_passwords.SESSION_KEY] = mark
            request.user = user
            return view_function(*args, **kwargs)

        return _unauthed()

    return wrap
