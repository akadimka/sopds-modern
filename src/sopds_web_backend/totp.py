"""Приложение-аутентификатор (TOTP, RFC 6238) — второй фактор входа на сайт.

Google Authenticator, Яндекс Ключ, Aegis, 1Password и др.: секрет один раз
переносится на телефон через QR-код (otpauth://), дальше приложение само
считает 6-значный код каждые 30 секунд — без сети, ничего не отправляя.

Код принимается в окне ±1 шаг (расхождение часов телефона и сервера до 30 с)
и только если его шаг новее последнего принятого — подсмотренный или уже
введённый код второй раз не сработает.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

from django.db import transaction
from django.utils import timezone

from .models import TotpDevice

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1          # шагов в каждую сторону
ISSUER = "SOPDS"


def new_secret() -> str:
    """160 бит случайности (как рекомендует RFC 4226), base32 без '='."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    secret = secret.upper().replace(" ", "")
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def code_at(secret: str, step: int) -> str:
    """HOTP (RFC 4226) для номера шага — код приложения в момент step×30 с."""
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: Optional[float] = None) -> int:
    return int((time.time() if now is None else now) // STEP_SECONDS)


def matching_step(secret: str, code: str, after_step: int = 0, now: Optional[float] = None) -> Optional[int]:
    """Номер шага, которому соответствует код (в окне ±WINDOW и новее
    after_step), или None."""
    code = "".join(c for c in (code or "") if c.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(now)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if candidate > after_step and hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret: str, username: str) -> str:
    """otpauth://-ссылка для QR-кода (формат Google Authenticator)."""
    label = quote(f"{ISSUER}:{username}", safe=":@")
    return f"otpauth://totp/{label}?secret={secret}&issuer={ISSUER}&digits={DIGITS}&period={STEP_SECONDS}"


def device_for(user) -> Optional[TotpDevice]:
    """Подключённое (подтверждённое) приложение пользователя."""
    return TotpDevice.objects.filter(user=user, confirmed=True).first()


def begin(user) -> TotpDevice:
    """Начать подключение: новый секрет, ещё не подтверждённый. Уже
    подключённое приложение так не заменить — сначала отключить."""
    if device_for(user) is not None:
        raise ValueError("already enabled")
    device, _created = TotpDevice.objects.update_or_create(
        user=user, defaults={"secret": new_secret(), "confirmed": False, "created": timezone.now(), "last_step": 0})
    return device


def pending_for(user) -> Optional[TotpDevice]:
    return TotpDevice.objects.filter(user=user, confirmed=False).first()


def confirm(user, code: str) -> bool:
    """Подтвердить подключение первым кодом из приложения."""
    device = pending_for(user)
    if device is None:
        return False
    step = matching_step(device.secret, code)
    if step is None:
        return False
    device.confirmed, device.last_step, device.created = True, step, timezone.now()
    device.save(update_fields=["confirmed", "last_step", "created"])
    return True


def verify(user, code: str) -> bool:
    """Проверить код при входе (или при отключении) и запомнить его шаг.
    Под блокировкой строки — два одновременных запроса с одним кодом не
    пройдут оба."""
    with transaction.atomic():
        device = TotpDevice.objects.select_for_update().filter(user=user, confirmed=True).first()
        if device is None:
            return False
        step = matching_step(device.secret, code, after_step=device.last_step)
        if step is None:
            return False
        device.last_step = step
        device.save(update_fields=["last_step"])
        return True


def disable(user) -> None:
    TotpDevice.objects.filter(user=user).delete()
