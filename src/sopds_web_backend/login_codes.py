"""Второй шаг входа на сайт после пароля: код из приложения или из Telegram.

1. Подключено приложение-аутентификатор (профиль → «Приложение-
   аутентификатор», totp.py) — спрашивается его код. У любого пользователя,
   который его подключил, независимо от настроек.
2. Иначе — для суперпользователей (is_superuser), если в настройках включён
   «второй фактор для суперпользователей»: код (6 цифр, 5 минут) бот
   отправляет по двум каналам сразу — в личный чат суперпользователя с ботом
   (Telegram, привязанный в профиле) и в чат суперпользователя из настроек.
   Не доставлен ни в один — вход не выполняется (иначе второй шаг ничего
   бы не защищал).

5 неверных кодов — заново пароль. Неверные коды считаются и на весь
аккаунт: ACCOUNT_MAX_FAILURES за час (при любых повторных входах с верным
паролем и в профиле — подключение/отключение приложения) — код на час не
принимается и не отправляется (иначе знающий пароль перебирал бы код,
входя заново). Выход из тупика — на сервере:
`manage.py totp_reset <логин>` (потерян телефон с приложением),
`manage.py admin_login_code off` (не доходит Telegram-код).

Пока код не введён, в сессии лежит только «ожидание» (id пользователя,
способ, хеш Telegram-кода, срок, попытки) — сама сессия анонимная; полный
вход — после кода.
"""
from __future__ import annotations

import html
import logging
import secrets
import time
from typing import List, Optional, Tuple

from django.utils.crypto import constant_time_compare, salted_hmac

_log = logging.getLogger(__name__)

CODE_DIGITS = 6
CODE_TTL_SECONDS = 5 * 60
MAX_ATTEMPTS = 5
SEND_TIMEOUT = 8.0
SESSION_KEY = "pending_login_code"

METHOD_TOTP = "totp"
METHOD_TELEGRAM = "telegram"

ERR_EXPIRED = "expired"
ERR_WRONG = "wrong"
ERR_TOO_MANY = "too_many"
ERR_LOCKED = "locked"

ACCOUNT_MAX_FAILURES = 10
ACCOUNT_LOCK_SECONDS = 60 * 60


def _failures_key(user) -> str:
    return f"sopds-2fa-failures:{user.pk}"


def account_locked(user) -> bool:
    from django.core.cache import cache
    return (cache.get(_failures_key(user)) or 0) >= ACCOUNT_MAX_FAILURES


def note_failure(user) -> None:
    from django.core.cache import cache
    key = _failures_key(user)
    cache.add(key, 0, ACCOUNT_LOCK_SECONDS)
    try:
        cache.incr(key)
    except ValueError:              # ключ успел истечь
        cache.set(key, 1, ACCOUNT_LOCK_SECONDS)


def clear_failures(user) -> None:
    from django.core.cache import cache
    cache.delete(_failures_key(user))


def locked_message() -> str:
    from django.utils.translation import gettext as _
    return _("Too many wrong codes for this account — try again in an hour.")


def _settings():
    from fb2parser_core.settings_manager import SettingsManager
    from fb2parser_web.fb2parser_bridge import _config_path
    return SettingsManager(_config_path()).get_autosync_settings()


def enabled() -> bool:
    return bool(_settings().get("admin_login_code"))


def method_for(user) -> Optional[str]:
    """Какой второй шаг нужен этому пользователю (None — не нужен)."""
    from .totp import device_for
    if device_for(user) is not None:
        return METHOD_TOTP
    if user.is_superuser and enabled():
        return METHOD_TELEGRAM
    return None


def _code_hash(code: str) -> str:
    return salted_hmac("sopds-admin-login-code", code).hexdigest()


def channels(user, cfg=None) -> List[str]:
    """Куда можно отправить код этому пользователю (chat id без повторов)."""
    from .models import TelegramLink
    cfg = cfg or _settings()
    if not cfg.get("telegram_token"):
        return []
    result = []
    link = TelegramLink.objects.filter(user=user, telegram_id__isnull=False).first()
    if link is not None:
        result.append(str(link.telegram_id))
    admin_chat = (cfg.get("telegram_admin_chat") or "").strip()
    if admin_chat and admin_chat not in result:
        result.append(admin_chat)
    return result


def _deliver(user, code: str, ip: str) -> int:
    """Отправить код по всем каналам. Сколько каналов приняли сообщение."""
    from fb2parser_core.telegram_notify import TelegramError, client_for
    cfg = _settings()
    targets = channels(user, cfg)
    if not targets:
        return 0
    client = client_for(cfg, timeout=SEND_TIMEOUT)
    text = (f"🔐 Код входа в библиотеку для «{html.escape(user.username)}»: <b>{code}</b>\n"
            f"Действует {CODE_TTL_SECONDS // 60} минут. IP: {html.escape(ip or '?')}\n"
            "Если это не вы — смените пароль: кто-то его знает.")
    delivered = 0
    for chat_id in targets:
        try:
            client.send_message(chat_id, text)
            delivered += 1
        except (TelegramError, OSError) as e:
            _log.warning("login code for %s not sent to %s: %s", user.username, chat_id, e)
    return delivered


def start(request, user, method: str, backend: str, next_url: str) -> bool:
    """Запомнить ожидание второго шага в сессии (для Telegram — сначала
    отправить код). False — Telegram-код не доставлен ни в один канал."""
    from sopds.client_ip import client_ip
    state = {"user_id": user.pk, "method": method, "hash": "", "expires": time.time() + CODE_TTL_SECONDS,
             "attempts": 0, "backend": backend, "next": next_url}
    if method == METHOD_TELEGRAM:
        code = "".join(secrets.choice("0123456789") for _ in range(CODE_DIGITS))
        if not _deliver(user, code, client_ip(request)):
            request.session.pop(SESSION_KEY, None)
            return False
        state["hash"] = _code_hash(code)
    request.session[SESSION_KEY] = state
    return True


def pending(request) -> Optional[dict]:
    return request.session.get(SESSION_KEY)


def verify(request, code: str) -> Tuple[Optional[object], str, str]:
    """(пользователь или None, ошибка, next). Неверный код — минус попытка;
    просрочен или попытки кончились — ожидание сбрасывается (заново пароль)."""
    from django.contrib.auth import get_user_model
    state = pending(request)
    if not state:
        return None, ERR_EXPIRED, ""
    if time.time() > state["expires"]:
        request.session.pop(SESSION_KEY, None)
        return None, ERR_EXPIRED, ""
    from . import totp
    user = get_user_model().objects.filter(pk=state["user_id"], is_active=True).first()
    if user is None:
        request.session.pop(SESSION_KEY, None)
        return None, ERR_EXPIRED, ""
    if account_locked(user):
        request.session.pop(SESSION_KEY, None)
        return None, ERR_LOCKED, ""
    code = "".join(c for c in (code or "") if c.isdigit())
    if state.get("method") == METHOD_TOTP:
        ok = totp.verify(user, code)
    else:
        ok = constant_time_compare(_code_hash(code), state["hash"])
    if not ok:
        note_failure(user)
        if account_locked(user):
            request.session.pop(SESSION_KEY, None)
            return None, ERR_LOCKED, ""
        state["attempts"] += 1
        if state["attempts"] >= MAX_ATTEMPTS:
            request.session.pop(SESSION_KEY, None)
            return None, ERR_TOO_MANY, ""
        request.session[SESSION_KEY] = state
        return None, ERR_WRONG, ""
    request.session.pop(SESSION_KEY, None)
    clear_failures(user)
    user.backend = state["backend"]
    return user, "", state["next"]
