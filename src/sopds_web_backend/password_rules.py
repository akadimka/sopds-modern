"""Требования к паролям и смена пароля при первом входе.

Пароль: не короче 12 символов у суперпользователя и 8 у пользователя, есть
строчная и заглавная латинская буква, цифра и спецсимвол (любой символ, кроме
букв, цифр и пробелов). Буквы — только латинские: пароль с кириллицей и
другими алфавитами не принимается. Проверка — валидатор Django
(`ComplexityValidator` в AUTH_PASSWORD_VALIDATORS), рядом со стандартными
«не похож на имя / не из частых / не только цифры».

«Нужно сменить пароль» (`UserProfile.must_change_password`): у созданного
суперпользователем пользователя, после сброса пароля суперпользователем и
при входе с паролем, который не отвечает требованиям (заведён до них или
пользователь стал суперпользователем). Пока не сменён — сайт пускает только
на страницу смены пароля (`PasswordChangeRequiredMiddleware`), читалки
(OPDS) не пускают с основным паролем.
"""
import string

from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import Resolver404, resolve, reverse
from django.utils.translation import gettext as _

MIN_LENGTH_USER = 8
MIN_LENGTH_SUPERUSER = 12


def min_length(user) -> int:
    return MIN_LENGTH_SUPERUSER if getattr(user, "is_superuser", False) else MIN_LENGTH_USER


_LOWER = set(string.ascii_lowercase)
_UPPER = set(string.ascii_uppercase)
_DIGITS = set(string.digits)


def _is_special(c: str) -> bool:
    return not c.isalnum() and not c.isspace()


def problems(password: str, user=None) -> list:
    """Чего не хватает паролю (тексты для пользователя); [] — подходит."""
    result = []
    if any(c.isalpha() and c not in _LOWER and c not in _UPPER for c in password):
        result.append(_("Latin letters only (no Cyrillic or other alphabets)"))
    need = min_length(user)
    if len(password) < need:
        result.append(_("at least %(n)d characters") % {"n": need})
    if not any(c in _LOWER for c in password):
        result.append(_("a lowercase Latin letter"))
    if not any(c in _UPPER for c in password):
        result.append(_("an uppercase Latin letter"))
    if not any(c in _DIGITS for c in password):
        result.append(_("a digit"))
    if not any(_is_special(c) for c in password):
        result.append(_("a special character (!, #, %, -…)"))
    return result


def rules_text(user=None) -> str:
    return _("Password: at least %(n)d characters, with a lowercase and an uppercase Latin letter, a digit and "
             "a special character; letters — Latin only.") % {"n": min_length(user)}


class ComplexityValidator:
    """Валидатор Django: длина по роли (`user.is_superuser`) и четыре группы символов."""

    def validate(self, password, user=None):
        missing = problems(password, user)
        if missing:
            raise ValidationError(_("The password must contain: %(list)s.") % {"list": ", ".join(missing)},
                                  code="password_too_weak")

    def get_help_text(self):
        return rules_text()


def must_change(user) -> bool:
    profile = getattr(user, "profile", None)
    return bool(profile is not None and profile.must_change_password)


def set_must_change(user, value: bool) -> None:
    from .models import UserProfile
    UserProfile.objects.update_or_create(user=user, defaults={"must_change_password": value})
    if hasattr(user, "profile"):
        user.profile.must_change_password = value


def check_on_sign_in(user, password: str) -> None:
    """Вход с паролем, который не отвечает требованиям (заведён до них или
    человек стал суперпользователем), — потребовать смену."""
    if problems(password, user) and not must_change(user):
        set_must_change(user, True)


# Адреса, доступные, пока пароль не сменён: сама смена, выход, вход и
# служебные страницы (без них не загрузится и страница смены).
_ALLOWED = {("web", "password_change_required"), ("web", "logout"), ("web", "login"), ("web", "login_code"),
            ("web", "offline"), ("", "set_language"), ("", "service_worker")}


class PasswordChangeRequiredMiddleware:
    """Вошедшего на сайт с отметкой «нужно сменить пароль» — на страницу смены."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and must_change(user):
            try:
                match = resolve(request.path_info)
                key = (match.namespace, match.url_name)
            except Resolver404:
                key = None
            if key not in _ALLOWED and key is not None and match.namespace != "opds":
                if request.headers.get("HX-Request"):          # htmx — перейти всей страницей
                    reply = HttpResponse(status=204)
                    reply["HX-Redirect"] = reverse("web:password_change_required")
                    return reply
                return redirect(reverse("web:password_change_required"))
        return self.get_response(request)
