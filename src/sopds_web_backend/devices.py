"""Устройства учётной записи и их лимит (docs/devices-design.md).

Устройство — браузер (сайт, PWA, fb2parser): при первом входе он получает
метку — cookie `sopds_device` (случайные 32 байта, HttpOnly, 2 года); в базе
хранится SHA-256 метки (`UserDevice`). Читалки OPDS и Telegram устройствами
не считаются.

Лимит: `UserProfile.max_devices` (NULL — по умолчанию из настроек
`devices_per_user`, 0 — без ограничений); на суперпользователя не действует.
Лимит исчерпан — вход с нового устройства отклоняется (вытеснения нет).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import UserDevice, UserProfile

COOKIE = "sopds_device"
COOKIE_AGE = 2 * 365 * 24 * 3600
SESSION_KEY = "sopds_device_id"
TOUCH_EVERY = timedelta(minutes=10)   # last_seen — не на каждый запрос (запись в SQLite)
_NEW_TOKEN_ATTR = "_sopds_device_token"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii", "ignore")).hexdigest()


def device_limit(user) -> Optional[int]:
    """Сколько устройств можно пользователю; None — без ограничений."""
    if user.is_superuser:
        return None
    profile = UserProfile.objects.filter(user=user).first()
    limit = profile.max_devices if profile is not None and profile.max_devices is not None else None
    if limit is None:
        from opds_catalog.sopds_config import sopds_cfg as config
        try:
            limit = int(config.SOPDS_DEVICES_PER_USER or 0)
        except (TypeError, ValueError):
            limit = 0
    return limit or None


def current_device(request, user) -> Optional[UserDevice]:
    """Устройство этого браузера у пользователя (по метке в cookie)."""
    token = request.COOKIES.get(COOKIE) or getattr(request, _NEW_TOKEN_ATTR, None)
    if not token:
        return None
    return UserDevice.objects.filter(user=user, token_hash=_hash(token)).first()


def can_sign_in(request, user) -> bool:
    """Можно ли войти с этого браузера: устройство уже известно или лимит не
    исчерпан."""
    limit = device_limit(user)
    if limit is None or current_device(request, user) is not None:
        return True
    return UserDevice.objects.filter(user=user).count() < limit


def label_for(user_agent: str) -> str:
    """«Chrome, Windows» — по User-Agent, без внешних библиотек."""
    ua = user_agent or ""
    browser = next((name for pattern, name in (
        (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"YaBrowser", "Yandex"), (r"Firefox/", "Firefox"),
        (r"Chrome/|CriOS/", "Chrome"), (r"Safari/", "Safari")) if re.search(pattern, ua)), "")
    system = next((name for pattern, name in (
        (r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Android", "Android"), (r"Windows", "Windows"),
        (r"Mac OS X|Macintosh", "macOS"), (r"Linux", "Linux")) if re.search(pattern, ua)), "")
    return ", ".join(x for x in (browser, system) if x)


def _client_ip(request) -> Optional[str]:
    from sopds.client_ip import client_ip
    return client_ip(request) or None


def session_token(request) -> Optional[str]:
    """Метка для сессии без устройства, вычисляемая из ключа сессии: браузер
    шлёт несколько запросов сразу (страница + htmx), и каждый без метки
    завёл бы своё устройство; с одной меткой на сессию устройство одно."""
    key = request.session.session_key
    if not key:
        return None
    digest = hmac.new(settings.SECRET_KEY.encode(), f"sopds-device:{key}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def register(request, user, token: Optional[str] = None) -> Optional[UserDevice]:
    """Устройство этого браузера: известное — освежить, новое — завести
    (метка уходит в cookie ответа, DeviceMiddleware). `token` — метка для
    нового, если у браузера её нет (по умолчанию — случайная). None — лимит
    исчерпан (вход не допускается)."""
    device = current_device(request, user)
    if device is None and token and not request.COOKIES.get(COOKIE):
        # Та же вычисляемая метка у одновременного запроса этой сессии —
        # устройство уже есть, лимит ни при чём.
        device = UserDevice.objects.filter(user=user, token_hash=_hash(token)).first()
        if device is not None:
            setattr(request, _NEW_TOKEN_ATTR, token)
    now = timezone.now()
    ua = (request.META.get("HTTP_USER_AGENT") or "")[:512]
    if device is None:
        if not can_sign_in(request, user):
            return None
        token = request.COOKIES.get(COOKIE) or token or secrets.token_urlsafe(32)
        if not request.COOKIES.get(COOKIE):
            setattr(request, _NEW_TOKEN_ATTR, token)
        fields = {"user_agent": ua, "label": label_for(ua)[:100], "last_ip": _client_ip(request)}
        try:
            with transaction.atomic():
                device, _created = UserDevice.objects.get_or_create(user=user, token_hash=_hash(token),
                                                                    defaults=fields)
        except IntegrityError:   # одновременный запрос той же сессии успел создать
            device = UserDevice.objects.get(user=user, token_hash=_hash(token))
    else:
        UserDevice.objects.filter(pk=device.pk).update(last_seen=now, last_ip=_client_ip(request),
                                                       user_agent=ua, label=label_for(ua)[:100])
    request.session[SESSION_KEY] = device.pk
    return device


def touch(request, device: UserDevice) -> None:
    if timezone.now() - device.last_seen >= TOUCH_EVERY:
        UserDevice.objects.filter(pk=device.pk).update(last_seen=timezone.now(), last_ip=_client_ip(request))


def set_cookie(request, response):
    """Метка нового браузера — в ответ; известную продлеваем."""
    token = getattr(request, _NEW_TOKEN_ATTR, None) or request.COOKIES.get(COOKIE)
    if token and (getattr(request, _NEW_TOKEN_ATTR, None) or request.session.get(SESSION_KEY)):
        response.set_cookie(COOKIE, token, max_age=COOKIE_AGE, httponly=True, samesite="Lax",
                            secure=getattr(settings, "SESSION_COOKIE_SECURE", False))
    return response


NOTICE_KEY = "sopds_device_notice"


def limit_message(user) -> str:
    from django.utils.translation import gettext as _
    return _("The limit of connected devices has been reached (%(limit)s). Remove a device you no longer use "
             "in Settings on one of your devices, or contact a superuser.") % {"limit": device_limit(user)}


def removed_message() -> str:
    from django.utils.translation import gettext as _
    return _("This device was removed from your devices. Sign in again.")


def sign_out(request, notice: str) -> None:
    """Выход с причиной: страница входа покажет её (сессия после logout новая)."""
    from django.contrib.auth import logout
    logout(request)
    request.session[NOTICE_KEY] = notice


class DeviceMiddleware:
    """Сессия сайта привязана к устройству (docs/devices-design.md):
    устройство удалено — выход; сессия без устройства (открыта до появления
    функции или перенесена в другой браузер) — привязка, а при исчерпанном
    лимите — выход. Сессии читалок (OPDS, метка opds_only) не трогаются."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        self._check(request)
        return set_cookie(request, self.get_response(request))

    def _check(self, request):
        from .opds_passwords import SESSION_KEY as OPDS_ONLY
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated or request.session.get(OPDS_ONLY):
            return
        bound = request.session.get(SESSION_KEY)
        device = current_device(request, user)
        if device is not None and device.pk == bound:
            touch(request, device)
            return
        if bound and not UserDevice.objects.filter(pk=bound, user=user).exists():
            sign_out(request, removed_message())
            return
        if register(request, user, token=session_token(request)) is None:
            sign_out(request, limit_message(user))
