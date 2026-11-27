"""Ограничение выдачи книг: защита от выкачивания библиотеки
(docs/download-limits-design.md).

Книга «взята», когда её текст ушёл пользователю: скачивание (сайт и OPDS),
конвертация, читалка (сайт и OPDS) и отправка в Telegram. Считаются разные
книги за месяц (`BookTake`): повторно та же книга в этом месяце, в любом
формате и любым путём, лимит не тратит.

- Лимиты на сутки и на месяц — личные (`UserProfile.downloads_per_day/
  _per_month`, в таблице пользователей), пусто — по умолчанию из настроек
  (`downloads_per_day/_per_month`), 0 — без ограничений.
- Скорость: не больше RATE_BOOKS новых книг за RATE_WINDOW — против
  скриптов, которые укладываются в суточный лимит.
- Суперпользователь и выключенный вход (SOPDS_AUTH, анонимно) — без
  ограничений.

Отказ — в журнал (`download_limit_reached`, раз в день на вид лимита, в
«тревогах» суперпользователя); упирается REPEAT_ALERT_DAYS дней подряд —
сообщение суперпользователям в Telegram (раз в день).

Каждый путь выдачи книги: `refusal()` до подготовки файла, `note()` — после
того, как он отдан. Новый путь — так же (CLAUDE.md, «Видимость книг»).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from django.utils import timezone

_log = logging.getLogger(__name__)

RATE_BOOKS = 10
RATE_WINDOW = timedelta(minutes=5)
REPEAT_ALERT_DAYS = 3

KIND_DAY = "day"
KIND_MONTH = "month"
KIND_RATE = "rate"


@dataclass(frozen=True)
class Refusal:
    kind: str
    limit: int
    retry_at: datetime

    def message(self) -> str:
        """Текст отказа на текущем языке."""
        from django.utils.formats import date_format
        from django.utils.translation import gettext as _
        local = timezone.localtime(self.retry_at)
        if self.kind == KIND_RATE:
            minutes = max(1, -(-int((self.retry_at - timezone.now()).total_seconds()) // 60))
            return _("Too many new books in a row: no more than %(books)d new books in %(window)d minutes. "
                     "Try again in %(minutes)d min.") % {
                "books": self.limit, "window": int(RATE_WINDOW.total_seconds() // 60), "minutes": minutes}
        if self.kind == KIND_MONTH:
            return _("The limit for this month is reached (new books a month: %(limit)d). New books will be "
                     "available from %(when)s. Books you have already taken this month can be downloaded and "
                     "read as before.") % {"limit": self.limit, "when": date_format(local, "j E")}
        return _("The limit for today is reached (new books a day: %(limit)d). New books will be available "
                 "from %(when)s. Books you have already taken this month can be downloaded and read "
                 "as before.") % {"limit": self.limit, "when": date_format(local, "j E, H:i")}

    def message_ru(self) -> str:
        """Текст для бота: его сообщения — по-русски."""
        from django.utils import translation
        with translation.override("ru"):
            return self.message()


def applies(user) -> bool:
    return bool(user is not None and getattr(user, "is_authenticated", False) and not user.is_superuser)


def _config_limit(name: str) -> int:
    from opds_catalog.sopds_config import sopds_cfg as config
    try:
        return max(0, int(getattr(config, name) or 0))
    except (TypeError, ValueError):
        return 0


def default_limits() -> Tuple[int, int]:
    """Лимиты по умолчанию из настроек (сутки, месяц); 0 — без ограничений."""
    return _config_limit("SOPDS_DOWNLOADS_PER_DAY"), _config_limit("SOPDS_DOWNLOADS_PER_MONTH")


def limits(user) -> Tuple[Optional[int], Optional[int]]:
    """Действующие лимиты пользователя (сутки, месяц); None — без ограничений."""
    if not applies(user):
        return None, None
    from .models import UserProfile
    profile = UserProfile.objects.filter(user=user).first()
    day, month = default_limits()
    if profile is not None and profile.downloads_per_day is not None:
        day = profile.downloads_per_day
    if profile is not None and profile.downloads_per_month is not None:
        month = profile.downloads_per_month
    return day or None, month or None


def _month_start(moment: Optional[datetime] = None) -> date:
    return timezone.localtime(moment).date().replace(day=1)


def _start_of_today() -> datetime:
    return timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)


def _next_month(moment: datetime) -> datetime:
    local = timezone.localtime(moment).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return local.replace(year=local.year + 1, month=1) if local.month == 12 else local.replace(month=local.month + 1)


def usage(user) -> dict:
    """Сколько разных книг взято сегодня и в этом месяце."""
    from .models import BookTake
    takes = BookTake.objects.filter(user=user, month=_month_start())
    return {"day": takes.filter(taken_at__gte=_start_of_today()).count(), "month": takes.count()}


def refusal(user, book) -> Optional[Refusal]:
    """Можно ли пользователю взять книгу сейчас: None — можно (в том числе
    уже взятую в этом месяце), иначе — почему нельзя. Ничего не записывает."""
    if not applies(user) or book is None:
        return None
    from .models import BookTake
    takes = BookTake.objects.filter(user=user, month=_month_start())
    if takes.filter(book_id=book.pk).exists():
        return None
    now = timezone.now()
    day_limit, month_limit = limits(user)
    if month_limit and takes.count() >= month_limit:
        return Refusal(KIND_MONTH, month_limit, _next_month(now))
    if day_limit and takes.filter(taken_at__gte=_start_of_today()).count() >= day_limit:
        return Refusal(KIND_DAY, day_limit, _start_of_today() + timedelta(days=1))
    recent = list(takes.filter(taken_at__gt=now - RATE_WINDOW).order_by("-taken_at")
                  .values_list("taken_at", flat=True)[:RATE_BOOKS])
    if len(recent) >= RATE_BOOKS:
        return Refusal(KIND_RATE, RATE_BOOKS, recent[-1] + RATE_WINDOW)
    return None


def note(user, book) -> None:
    """Книга отдана пользователю — отметить её взятой в этом месяце."""
    if not applies(user) or book is None:
        return
    from django.db import IntegrityError

    from .models import BookTake
    now = timezone.now()
    try:
        BookTake.objects.get_or_create(user=user, book_id=book.pk, month=_month_start(now),
                                       defaults={"taken_at": now})
    except IntegrityError:          # та же книга в параллельном запросе — уже отмечена
        pass


def report(request, user, refused: Refusal, *, channel: Optional[str] = None) -> None:
    """Отказ — в журнал (раз в день на вид лимита) и, если пользователь
    упирается несколько дней подряд, — суперпользователям в Telegram."""
    from . import activity
    event = activity.record(request, "download_limit_reached", user=user, ok=False, channel=channel,
                            obj=("limit", refused.kind, ""), once_per_day=True,
                            limit_kind=refused.kind, download_limit=refused.limit)
    if event is not None:
        _maybe_alert(user)


def _days_in_a_row(user) -> int:
    from .models import ActivityEvent
    since = _start_of_today() - timedelta(days=REPEAT_ALERT_DAYS - 1)
    days = {timezone.localtime(at).date() for at in ActivityEvent.objects.filter(
        user=user, action="download_limit_reached", at__gte=since).values_list("at", flat=True)}
    today = timezone.localdate()
    count = 0
    while today - timedelta(days=count) in days:
        count += 1
    return count


def _maybe_alert(user) -> None:
    from django.core.cache import cache
    if _days_in_a_row(user) < REPEAT_ALERT_DAYS:
        return
    if not cache.add(f"sopds-download-limit-alert:{user.pk}:{timezone.localdate().isoformat()}", 1, 26 * 3600):
        return                                   # сегодня уже сообщали
    import html
    text = (f"📚 «{html.escape(user.username)}» {REPEAT_ALERT_DAYS} дня подряд упирается в лимит книг. "
            "Проверьте журнал: возможно, кто-то выкачивает библиотеку с этой учётной записи.")
    _start_alert(text)


def _start_alert(text: str) -> None:
    """В фоне: Telegram не должен задерживать ответ пользователю."""
    import threading
    threading.Thread(target=send_alert, args=(text,), daemon=True).start()


def alert_chats(cfg) -> list:
    """Куда сообщать суперпользователям: их привязанные Telegram и чат
    суперпользователя из настроек."""
    from .models import TelegramLink
    chats = [str(t) for t in TelegramLink.objects.filter(
        telegram_id__isnull=False, user__is_superuser=True, user__is_active=True).values_list("telegram_id", flat=True)]
    admin_chat = (cfg.get("telegram_admin_chat") or "").strip()
    if admin_chat and admin_chat not in chats:
        chats.append(admin_chat)
    return chats


def send_alert(text: str) -> int:
    """Отправить сообщение суперпользователям; сколько чатов приняли."""
    from django.db import close_old_connections, connection
    close_old_connections()
    try:
        from fb2parser_core.telegram_notify import TelegramError, client_for

        from .login_codes import _settings
        cfg = _settings()
        if not cfg.get("telegram_token"):
            return 0
        client = client_for(cfg, timeout=8.0)
        sent = 0
        for chat in alert_chats(cfg):
            try:
                client.send_message(chat, text)
                sent += 1
            except (TelegramError, OSError) as e:
                _log.warning("download limits: alert not sent to %s: %s", chat, e)
        return sent
    except Exception:  # noqa: BLE001 — оповещение не должно ломать выдачу книги
        _log.exception("download limits: alert failed")
        return 0
    finally:
        import threading
        if threading.current_thread() is not threading.main_thread():
            connection.close()


# ── Ответы сайта и OPDS ──────────────────────────────────────────────────────

def refused_response(request, refused: Refusal):
    """Ответ на отказ: на сайте — страница с объяснением (обычно до неё не
    доходит: кнопки на сайте сначала спрашивают `web:download_check` и
    показывают окно), читалке OPDS — текст с кодом 429."""
    from django.http import HttpResponse
    from django.shortcuts import render

    from .activity import channel_of
    message = refused.message()
    retry = max(1, int((refused.retry_at - timezone.now()).total_seconds()))
    if channel_of(request) in ("web", "pwa"):
        response = render(request, "sopds_download_limit.html", {"message": message}, status=429)
    else:
        response = HttpResponse(message, status=429, content_type="text/plain; charset=utf-8")
    response["Retry-After"] = str(retry)
    return response


def gate(request, book):
    """Для адресов выдачи книги: None — можно отдавать, иначе ответ-отказ
    (и запись в журнал)."""
    refused = refusal(request.user, book)
    if refused is None:
        return None
    report(request, request.user, refused)
    return refused_response(request, refused)
