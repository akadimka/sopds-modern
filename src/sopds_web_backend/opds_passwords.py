"""Пароли для читалок (OPDS): отдельные от основного пароля сайта.

Читалка (OPDS-клиент) входит по HTTP Basic и хранит пароль у себя. С
основным паролем утечка из её настроек давала бы вход на сайт — а у
суперпользователя и доступ к файлам сервера. Пароль для читалки подходит
только к /opds/ (сессия, созданная по нему, помечается «только OPDS», см.
opds_catalog.middleware.OpdsOnlySessionMiddleware), отзывается без смены
основного пароля и показывается один раз — на сервере хранится SHA-256.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Optional

from django.utils import timezone

from .models import OpdsPassword

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без похожих 0/O, 1/I
GROUPS, GROUP_LEN = 4, 5                       # 20 символов × 5 бит = 100 бит
LAST_USED_STEP = timedelta(hours=1)            # не писать в БД на каждый запрос читалки

# Метка в сессии, созданной OPDS-входом: «main» — по основному паролю,
# иначе — начало хеша пароля для читалки (сессия гаснет, если его сменили
# или отозвали).
SESSION_KEY = "opds_only"
MARK_MAIN = "main"


def _normalize(password: str) -> str:
    return "".join(c for c in (password or "").upper() if c in ALPHABET)


def _hash(password: str) -> str:
    return hashlib.sha256(_normalize(password).encode("ascii")).hexdigest()


def session_mark(token_hash: str) -> str:
    return token_hash[:16]


def generate(user) -> str:
    """Новый пароль для читалок (старый перестаёт действовать). Возвращает
    его в открытом виде — показать пользователю один раз."""
    raw = "".join(secrets.choice(ALPHABET) for _ in range(GROUPS * GROUP_LEN))
    password = "-".join(raw[i:i + GROUP_LEN] for i in range(0, len(raw), GROUP_LEN))
    OpdsPassword.objects.update_or_create(
        user=user, defaults={"token_hash": _hash(password), "created": timezone.now(), "last_used": None})
    return password


def revoke(user) -> None:
    OpdsPassword.objects.filter(user=user).delete()


def authenticate(username: str, password: str) -> Optional[OpdsPassword]:
    """Запись пароля для читалок, если логин и пароль подходят (аккаунт активен)."""
    if len(_normalize(password)) != GROUPS * GROUP_LEN:
        return None
    record = (OpdsPassword.objects.select_related("user")
              .filter(user__username=username, user__is_active=True).first())
    if record is None or not hmac.compare_digest(record.token_hash, _hash(password)):
        return None
    now = timezone.now()
    if record.last_used is None or now - record.last_used > LAST_USED_STEP:
        record.last_used = now
        record.save(update_fields=["last_used"])
    return record


def session_still_valid(user, mark: str) -> bool:
    """Сессия по паролю для читалок действует, пока этот пароль не сменён/не отозван."""
    if mark == MARK_MAIN:
        return True  # смену основного пароля Django отслеживает сам (хеш сессии)
    record = OpdsPassword.objects.filter(user=user).first()
    return record is not None and hmac.compare_digest(session_mark(record.token_hash), mark)
