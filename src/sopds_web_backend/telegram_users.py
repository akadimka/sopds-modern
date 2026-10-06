"""Привязка аккаунтов SOPDS к Telegram и доступ к каналу библиотеки.

Только для бота и канала — на вход на сайт не влияет. Читатели — люди,
которым администратор завёл аккаунт на сервере:
- код привязки берут в своём профиле на сайте (действует 24 часа) и
  отправляют боту — или просто открывают ссылку t.me/<бот>?start=link_<код>;
- в канал входят по ссылке «со вступлением по заявке»: бот одобряет заявки
  только привязанных активных пользователей, остальные отклоняет (утёкшая
  ссылка чужому не поможет);
- при отвязке, удалении или блокировке аккаунта бот удаляет человека из
  канала (ban + unban — без вечного бана).
"""
from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import Any, Dict, Optional, Tuple

from django.utils import timezone

from .models import TelegramLink

_log = logging.getLogger(__name__)

CODE_TTL = timedelta(hours=24)
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без похожих 0/O, 1/I
CODE_LENGTH = 8
LINK_PREFIX = "link_"

ERR_BAD_CODE = "bad_code"
ERR_TAKEN = "taken"

STATE_BOT_USERNAME = "tg_bot_username"
STATE_JOIN_LINK = "tg_channel_join_link"


def autosync_service():
    from fb2parser_web.fb2parser_bridge import get_autosync_service
    return get_autosync_service()


def telegram(service=None) -> Tuple[Optional[Any], Any]:
    """(клиент Bot API или None без токена, сервис автосинхронизации)."""
    from fb2parser_core.telegram_notify import TelegramClient
    service = service or autosync_service()
    cfg = service.cfg
    if not cfg["telegram_token"]:
        return None, service
    return TelegramClient(cfg["telegram_token"], cfg["telegram_proxy"]), service


# ---------- коды и привязка ----------
def new_link_code(user) -> TelegramLink:
    link, _created = TelegramLink.objects.get_or_create(user=user)
    link.code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    link.code_expires = timezone.now() + CODE_TTL
    link.save(update_fields=["code", "code_expires"])
    return link


def normalize_code(text: str) -> str:
    text = (text or "").strip()
    if text.lower().startswith(LINK_PREFIX):
        text = text[len(LINK_PREFIX):]
    return text.upper()


def looks_like_code(text: str) -> bool:
    code = normalize_code(text)
    return len(code) == CODE_LENGTH and all(c in CODE_ALPHABET for c in code)


def _tg_name(tg_user: Dict[str, Any]) -> str:
    name = " ".join(x for x in (tg_user.get("first_name"), tg_user.get("last_name")) if x)
    if tg_user.get("username"):
        name = f"{name} (@{tg_user['username']})".strip()
    return name[:200]


def link_by_code(code: str, tg_user: Dict[str, Any]) -> Tuple[Optional[TelegramLink], str]:
    """Привязать Telegram-пользователя по коду из профиля. (привязка, ошибка)."""
    code = normalize_code(code)
    link = (TelegramLink.objects.select_related("user")
            .filter(code=code, code_expires__gt=timezone.now(), user__is_active=True).first()) if code else None
    if link is None:
        return None, ERR_BAD_CODE
    tg_id = int(tg_user["id"])
    if TelegramLink.objects.filter(telegram_id=tg_id).exclude(pk=link.pk).exists():
        return None, ERR_TAKEN  # этот Telegram уже привязан к другому аккаунту
    link.telegram_id = tg_id
    link.telegram_name = _tg_name(tg_user)
    link.code, link.code_expires = "", None
    link.linked_at = timezone.now()
    link.save()
    return link, ""


def linked_user(tg_id) -> Optional[Any]:
    """Пользователь SOPDS, привязанный к этому Telegram, — если аккаунт активен."""
    try:
        tg_id = int(tg_id)
    except (TypeError, ValueError):
        return None
    link = TelegramLink.objects.select_related("user").filter(telegram_id=tg_id, user__is_active=True).first()
    return link.user if link else None


def unlink(link: TelegramLink) -> bool:
    """Отвязать и удалить из канала. False — удалить из канала не удалось."""
    ok = True
    if link.telegram_id is not None:
        ok = kick_from_channel(link.telegram_id)
    link.telegram_id = None
    link.telegram_name = ""
    link.in_channel = False
    link.linked_at = None
    link.save()
    return ok


# ---------- канал ----------
def bot_username(client, service) -> str:
    cached = service.journal.get_state(STATE_BOT_USERNAME)
    if cached:
        return cached
    username = (client.call("getMe") or {}).get("username", "")
    if username:
        service.journal.set_state(STATE_BOT_USERNAME, username)
    return username


def channel_join_link(client, service) -> str:
    """Ссылка в канал «по заявке» (создаётся один раз на канал)."""
    import json
    channel = service.cfg["telegram_channel"]
    if not channel:
        return ""
    stored = json.loads(service.journal.get_state(STATE_JOIN_LINK) or "{}")
    if stored.get("channel") == channel and stored.get("link"):
        return stored["link"]
    result = client.call("createChatInviteLink", chat_id=channel, name="SOPDS readers", creates_join_request=True)
    link = (result or {}).get("invite_link", "")
    if link:
        service.journal.set_state(STATE_JOIN_LINK, json.dumps({"channel": channel, "link": link}))
    return link


def handle_join_request(client, service, request: Dict[str, Any]) -> bool:
    """Заявка на вступление в канал: одобрить привязанного, отклонить чужого."""
    channel = service.cfg["telegram_channel"]
    chat = request.get("chat") or {}
    chat_id = chat.get("id")
    same_channel = str(chat_id) == str(channel) or (chat.get("username") and chat["username"] == channel.lstrip("@"))
    if not channel or not same_channel:
        return False
    tg_id = (request.get("from") or {}).get("id")
    if linked_user(tg_id) is not None:
        client.call("approveChatJoinRequest", chat_id=chat_id, user_id=tg_id)
        TelegramLink.objects.filter(telegram_id=tg_id).update(in_channel=True)
        return True
    client.call("declineChatJoinRequest", chat_id=chat_id, user_id=tg_id)
    return False


def kick_from_channel(tg_id) -> bool:
    """Удалить из канала (без вечного бана). Нет токена/канала — True (нечего делать)."""
    from fb2parser_core.telegram_notify import TelegramError
    client, service = telegram()
    channel = service.cfg["telegram_channel"]
    if client is None or not channel:
        return True
    try:
        client.call("banChatMember", chat_id=channel, user_id=int(tg_id))
        client.call("unbanChatMember", chat_id=channel, user_id=int(tg_id), only_if_banned=True)
        return True
    except TelegramError as e:
        _log.warning("telegram: не удалось удалить %s из канала: %s", tg_id, e.description)
        return False
