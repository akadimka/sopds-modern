"""Журнал действий пользователей (docs/activity-log-design.md).

Одна точка записи — `record()`: категорию берёт из списка действий,
канал, устройство и IP — из запроса. Сбой записи не ломает само действие:
исключение — в лог, запрос продолжается. IP пишется только у событий входа
и безопасности; в подробности не попадают пароли, коды, токены и cookie
(ключи с такими словами отбрасываются).

Журнал видит только суперпользователь (вкладка «Журнал» в настройках);
каждый пользователь — только свои входы (блок в профиле).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from django.utils import timezone
from django.utils.translation import gettext_lazy as _lazy

_log = logging.getLogger(__name__)

SECURITY = "security"
READING = "reading"
ACCOUNTS = "accounts"
SITE = "site"
SYSTEM = "system"

CATEGORIES = {
    SECURITY: ("🔒", _lazy("Sign-in and security")),
    READING: ("📖", _lazy("Reading and downloads")),
    ACCOUNTS: ("👤", _lazy("Accounts and rights")),
    SITE: ("⚙", _lazy("Site and library")),
    SYSTEM: ("🔄", _lazy("System")),
}

CHANNELS = {
    "web": ("🌐", _lazy("site")),
    "pwa": ("📱", _lazy("app")),
    "opds": ("📚", "OPDS"),
    "telegram": ("✈", "Telegram"),
    "fb2parser": ("⚙", "fb2parser"),
    "system": ("⚙", _lazy("system")),
    "cli": ("⌨", _lazy("server console")),
}

# Cookie, которую ставит страница, открытая в установленном приложении
# (display-mode: standalone): сервер по запросу приложение от сайта не отличит.
APP_COOKIE = "sopds_app"


@dataclass(frozen=True)
class Action:
    category: str
    label: object


ACTIONS = {
    "login": Action(SECURITY, _lazy("Signed in")),
    "login_failed": Action(SECURITY, _lazy("Failed sign-in")),
    "login_refused": Action(SECURITY, _lazy("Sign-in refused")),
    "locked_out": Action(SECURITY, _lazy("Locked out after failed sign-ins")),
    "logout": Action(SECURITY, _lazy("Signed out")),
    "code_wrong": Action(SECURITY, _lazy("Wrong second-factor code")),
    "code_locked": Action(SECURITY, _lazy("Locked out by wrong codes")),
    "device_new": Action(SECURITY, _lazy("New device")),
    "device_deleted": Action(SECURITY, _lazy("Device removed")),
    "password_changed": Action(SECURITY, _lazy("Changed the password")),
    "password_set": Action(SECURITY, _lazy("Password set by a superuser")),
    "opds_password_created": Action(SECURITY, _lazy("Created a password for reader apps")),
    "opds_password_revoked": Action(SECURITY, _lazy("Password for reader apps revoked")),
    "opds_password_first_use": Action(SECURITY, _lazy("Reader app signed in with its password for the first time")),
    "telegram_linked": Action(SECURITY, _lazy("Linked Telegram")),
    "telegram_unlinked": Action(SECURITY, _lazy("Telegram unlinked")),
    "totp_enabled": Action(SECURITY, _lazy("Connected an authenticator app")),
    "totp_disabled": Action(SECURITY, _lazy("Turned off the authenticator app")),
    "totp_reset": Action(SECURITY, _lazy("Authenticator app reset on the server")),
}

# Значения подробностей, которые показываются текстом, а не кодом.
DETAIL_TEXTS = {
    "method": {
        "password": _lazy("password"),
        "totp": _lazy("password and authenticator app"),
        "telegram": _lazy("password and code from Telegram"),
        "app_password": _lazy("password for reader apps"),
    },
    "reason": {
        "device_limit": _lazy("device limit reached"),
        "code_locked": _lazy("too many wrong codes"),
        "code_not_delivered": _lazy("the code could not be sent to Telegram"),
        "inactive": _lazy("the account is disabled"),
        "unknown_user": _lazy("no such user"),
        "wrong_password": _lazy("wrong password"),
        "password_change": _lazy("the password was changed"),
        "device_removed": _lazy("the device was removed"),
        "main_password_not_for_readers": _lazy("reader apps do not accept the main password of this account "
                                               "(second factor or a required password change)"),
    },
}
DETAIL_LABELS = {
    "method": _lazy("How"),
    "reason": _lazy("Reason"),
    "entered": _lazy("Entered name"),
    "device": _lazy("Device"),
    "required": _lazy("Required change"),
    "telegram_name": _lazy("Name in Telegram"),
}

_SECRET_WORDS = ("password", "passwd", "token", "code", "secret", "cookie", "session", "key")


def _clean(details: dict) -> dict:
    """Подробности без секретов: ключи с «password», «code», «token»… —
    отбрасываются, значения — строки до 300 символов (или числа и флаги)."""
    clean = {}
    for key, value in details.items():
        if value is None or value == "" or any(w in key.lower() for w in _SECRET_WORDS):
            continue
        if not isinstance(value, (bool, int, float)):
            value = str(value)[:300]
        clean[key] = value
    return clean


def channel_of(request) -> str:
    from django.urls import reverse
    if request.path_info.startswith(reverse("opds:main")):
        return "opds"
    if request.COOKIES.get(APP_COOKIE) == "1":
        return "pwa"
    return "web"


_REQUEST_USER = object()


def record(request, action: str, *, user=_REQUEST_USER, username: str = "", actor=None, actor_name: str = "",
           ok: bool = True, channel: Optional[str] = None, device=None, obj=None, **details):
    """Записать событие. user — чьё действие (по умолчанию — вошедший по
    запросу; None — неизвестный, тогда `username` — введённое имя); actor —
    кто сделал, если не сам пользователь (суперпользователь), `actor_name` —
    подпись без учётной записи («система»); obj — (тип, id, подпись).
    Ошибка записи не выходит наружу."""
    try:
        return _record(request, action, user, username, actor, actor_name, ok, channel, device, obj, details)
    except Exception:  # noqa: BLE001 — журнал не должен ломать действие
        _log.exception("activity: событие %s не записано", action)
        return None


def _record(request, action, user, username, actor, actor_name, ok, channel, device, obj, details):
    from .models import ActivityEvent
    category = ACTIONS[action].category
    if user is _REQUEST_USER:
        user = request.user if request is not None and request.user.is_authenticated else None
    if channel is None:
        channel = channel_of(request) if request is not None else "system"
    if device is None and request is not None and user is not None and channel in ("web", "pwa"):
        from .devices import current_device
        device = current_device(request, user)
    if device is not None:
        details.setdefault("device", device.name or device.label)
    ip = None
    if category == SECURITY and request is not None:
        from sopds.client_ip import client_ip
        ip = client_ip(request) or None
    if actor is not None and user is not None and actor.pk == user.pk:
        actor = None
    object_type, object_id, object_label = obj or ("", "", "")
    return ActivityEvent.objects.create(
        at=timezone.now(), user=user, username=user.username if user is not None else (username or "")[:150],
        actor=actor, actor_name=actor.username if actor is not None else actor_name,
        category=category, action=action, channel=channel, ok=ok,
        object_type=object_type, object_id=str(object_id), object_label=str(object_label)[:300],
        device=device, ip=ip, details=_clean(details))


# ── Показ ────────────────────────────────────────────────────────────────────

def action_label(code: str):
    action = ACTIONS.get(code)
    return action.label if action is not None else code


def detail_rows(event) -> list:
    """Подробности для раскрывающейся строки: [(подпись, значение)]."""
    rows = []
    for key, value in (event.details or {}).items():
        if isinstance(value, bool):
            value = _lazy("yes") if value else _lazy("no")
        value = DETAIL_TEXTS.get(key, {}).get(value, value)
        rows.append((DETAIL_LABELS.get(key, key), value))
    if event.ip:
        rows.append(("IP", event.ip))
    return rows


def decorate(event):
    """Подписи события для шаблонов (журнал и профиль)."""
    event.action_text = action_label(event.action)
    event.category_icon = CATEGORIES.get(event.category, ("", ""))[0]
    event.channel_icon, event.channel_text = CHANNELS.get(event.channel, ("", event.channel))
    event.detail_rows = detail_rows(event)
    return event


PERIODS = {"today": _lazy("Today"), "7": _lazy("7 days"), "30": _lazy("30 days"), "all": _lazy("All time")}
DEFAULT_PERIOD = "7"
PAGE_SIZE = 50


def start_of_today() -> datetime:
    return timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)


def filters_from(params) -> dict:
    """Фильтры журнала из параметров запроса (неизвестные значения — «все»)."""
    period = params.get("period", DEFAULT_PERIOD)
    user = params.get("user", "")
    category = params.get("category", "")
    channel = params.get("channel", "")
    try:
        page = max(1, int(params.get("page", 1)))
    except ValueError:
        page = 1
    return {
        "period": period if period in PERIODS else DEFAULT_PERIOD,
        "user": user if user.isdigit() else "",
        "category": category if category in CATEGORIES else "",
        "channel": channel if channel in CHANNELS else "",
        "failed": params.get("failed") == "1",
        "page": page,
    }


def events(filters: dict):
    from .models import ActivityEvent
    qs = ActivityEvent.objects.all()
    if filters["period"] == "today":
        qs = qs.filter(at__gte=start_of_today())
    elif filters["period"] != "all":
        qs = qs.filter(at__gte=timezone.now() - timedelta(days=int(filters["period"])))
    if filters["user"]:
        qs = qs.filter(user_id=int(filters["user"]))
    if filters["category"]:
        qs = qs.filter(category=filters["category"])
    if filters["channel"]:
        qs = qs.filter(channel=filters["channel"])
    if filters["failed"]:
        qs = qs.filter(ok=False)
    return qs.select_related("device")


def page_of(filters: dict, after_day: str = ""):
    """Строки страницы журнала с заголовками дней и признак «есть ещё».
    after_day — день последней строки предыдущей страницы: его заголовок не
    повторяется."""
    start = (filters["page"] - 1) * PAGE_SIZE
    rows = list(events(filters)[start:start + PAGE_SIZE + 1])
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    prev_day = after_day
    for event in rows:
        decorate(event)
        day = timezone.localtime(event.at).date().isoformat()
        event.day_header = day != prev_day
        prev_day = day
    return rows, more, prev_day


def summary_today() -> dict:
    """Сводка «За сегодня»: входы, неудачные попытки и блокировки, активные
    пользователи."""
    from .models import ActivityEvent
    today = ActivityEvent.objects.filter(at__gte=start_of_today())
    return {
        "logins": today.filter(action="login").count(),
        "failed": today.filter(action__in=("login_failed", "code_wrong", "login_refused")).count(),
        "locks": today.filter(action__in=("locked_out", "code_locked")).count(),
        "active_users": today.exclude(user=None).values("user").distinct().count(),
    }


def own_sign_ins(user, limit: int = 20) -> list:
    """Последние события входа и безопасности самого пользователя — для
    профиля: только его, без чужих имён."""
    from .models import ActivityEvent
    return [decorate(e) for e in
            ActivityEvent.objects.filter(user=user, category=SECURITY).select_related("device")[:limit]]


# ── Хранение во времени ──────────────────────────────────────────────────────

def keep_days() -> dict:
    """Сроки хранения из настроек сайта: {"security": дни, "reading": дни};
    0 — бессрочно. «security» — всё, кроме чтения и скачивания."""
    from opds_catalog.sopds_config import sopds_cfg as config

    def _days(value, default):
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return default
    return {"security": _days(config.SOPDS_ACTIVITY_KEEP_SECURITY_DAYS, 365),
            "reading": _days(config.SOPDS_ACTIVITY_KEEP_READING_DAYS, 90)}


def cleanup(dry_run: bool = False, now: Optional[datetime] = None) -> dict:
    """Удалить записи старше срока хранения. Срок 0 — категория не чистится.
    Возвращает {"security": N, "reading": N} — сколько удалено (или было бы)."""
    from .models import ActivityEvent
    now = now or timezone.now()
    result = {}
    for group, days in keep_days().items():
        if not days:
            result[group] = 0
            continue
        qs = ActivityEvent.objects.filter(at__lt=now - timedelta(days=days))
        qs = qs.filter(category=READING) if group == "reading" else qs.exclude(category=READING)
        result[group] = qs.count() if dry_run else qs.delete()[0]
    return result
