"""Автосинхронизация вокруг скана библиотеки (docs/watch-folder-autosync-design.md).

Одно и то же для планового скана (sopds_scanner) и ручных кнопок полного
скана (SOPDS «Сканировать», fb2parser «Скан в каталог» по всей библиотеке):
1. до скана — разложить книги из папки наблюдения (`run_autosync`), чтобы
   скан их уже увидел;
2. после скана — Telegram (`notify_autosync`): новые книги уже в каталоге,
   на их серии можно дать ссылки.
Сбои автосинхронизации и Telegram скан не срывают — только лог.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional, Tuple

_log = logging.getLogger(__name__)


def run_autosync(logger: Optional[logging.Logger] = None) -> Optional[Tuple[Any, Any]]:
    """(сервис, результат) — или None, если автосинхронизация упала."""
    log = logger or _log
    try:
        from fb2parser_web.fb2parser_bridge import get_autosync_service

        service = get_autosync_service()
        result = service.run()
    except Exception:
        log.exception("Autosync failed")
        return None
    if result.status != "off":
        log.info(
            "Autosync (%s): %s, moved=%d, pending=%d, kept=%d, removed=%d%s",
            result.mode, result.status, len(result.moved), result.pending,
            result.kept, result.removed, f", error: {result.error}" if result.error else "",
        )
    return service, result


def notify_autosync(autosync: Optional[Tuple[Any, Any]], logger: Optional[logging.Logger] = None) -> Dict[str, str]:
    if autosync is None:
        return {}
    log = logger or _log
    service, result = autosync
    try:
        from fb2parser_core.autosync_service import notify

        sent = notify(service, result, link=catalog_link(service), include=public_book_filter())
    except Exception:
        log.exception("Autosync notification failed")
        sent = {}
    try:
        sent.update(announce_catalog_additions(service, result, log))
    except Exception:
        log.exception("Catalog additions announcement failed")
    if sent:
        log.info("Autosync notifications: %s", sent)
    return sent


CATALOG_MARK_KEY = "catalog_announced_book_id"


def announce_catalog_additions(service, result, logger: Optional[logging.Logger] = None) -> Dict[str, str]:
    """Новинки каталога в канал, когда автосинхронизация выключена (баг №179).

    На сервере-2 книги приходят ночным зеркалированием библиотеки, а не
    через автосинхронизацию — её журнал пуст, и канал молчал. Объявляем
    книги, появившиеся в каталоге после прошлого объявления (по id), только
    из открытых всем библиотек. Первый запуск лишь запоминает точку отсчёта —
    иначе в канал ушла бы вся библиотека. Где автосинхронизация включена,
    новинки объявляет она (без дублей)."""
    log = logger or _log
    cfg = service.cfg
    if result.status != "off" or not cfg.get("telegram_token") or not cfg.get("telegram_channel"):
        return {}
    import os

    from django.db.models import Max

    from opds_catalog import visibility
    from opds_catalog.models import Book

    journal = service.journal
    top = Book.objects.aggregate(m=Max("id"))["m"] or 0
    mark = journal.get_state(CATALOG_MARK_KEY)
    if mark is None:
        journal.set_state(CATALOG_MARK_KEY, str(top))
        log.info("Catalog announcements: starting point set at book id %s", top)
        return {}
    if top <= int(mark):
        return {}
    books = (visibility.visible_books(None).exclude(avail=0)
             .filter(id__gt=int(mark), id__lte=top)
             .prefetch_related("authors", "series").order_by("id"))
    rows = []
    for b in books:
        rows.append({
            "genre": re.split(r"[\\/]", b.path or "")[0] or "—",   # папка верхнего уровня (№166)
            "author": ", ".join(a.full_name for a in b.authors.all()) or "—",
            "series": next((s.ser for s in b.series.all()), ""),
            "title": b.title or "",
            "library_path": os.path.join(b.path or "", b.filename or ""),
        })
    if rows:
        from fb2parser_core.telegram_notify import (
            TelegramError,
            build_new_books_message,
            client_for,
            explain_error,
        )

        text = build_new_books_message(rows, catalog_link(service))
        try:
            client_for(cfg).send_message(cfg["telegram_channel"], text)
        except TelegramError as e:
            log.warning("Catalog announcements: not sent: %s", e.description)
            return {"catalog": explain_error(e)}   # точку не двигаем — повторим в следующий раз
    journal.set_state(CATALOG_MARK_KEY, str(top))
    return {"catalog": "ok"} if rows else {}


def summary(autosync: Optional[Tuple[Any, Any]]) -> Optional[Dict[str, Any]]:
    """Итог для строки статуса ручного скана; None — автосинхронизация выключена."""
    if autosync is None:
        return {"status": "error", "error": "см. лог сервера"}
    _service, r = autosync
    if r.status == "off":
        return None
    return {"status": r.status, "mode": r.mode, "moved": len(r.moved), "pending": r.pending,
            "kept": r.kept, "removed": r.removed, "would_move": r.would_move, "error": r.error}


def public_book_filter():
    """Новинка попадает в общий канал, только если её книга видна всем —
    лежит в открытой всем библиотеке (канал читают все привязанные
    пользователи). Книги нет в каталоге — не показываем."""
    from opds_catalog import visibility

    public = visibility.visible_books(None)
    return lambda row: find_book(row.get("library_path") or "", row.get("title") or "",
                                 row.get("author") or "", books=public) is not None


def catalog_link(service):
    """Ссылки сводки новинок: в бота библиотеки (t.me/<бот>?start=s<id серии> /
    b<id книги> — открывают серию/книгу с кнопками скачивания и работают без
    публичного адреса сайта), иначе на страницы сайта, если задан его адрес,
    иначе ссылок нет (docs/telegram-library-bot.md, этап D)."""
    from urllib.parse import urlencode

    bot = _bot_username(service)
    base = (service.cfg.get("public_url") or "").rstrip("/")
    if not bot and not base:
        return None
    if base:
        from django.urls import reverse
        search = base + reverse("web:searchbooks")

    def link(item):
        from opds_catalog import visibility

        public = visibility.visible_books(None)   # ссылки в общем канале — только на видимое всем
        if item["kind"] == "series":
            series = find_series(item.get("series") or "", item.get("author") or "", item.get("path") or "",
                                 books=public)
            if series is None:
                return None
            if bot:
                return f"https://t.me/{bot}?start=s{series.id}"
            return f"{search}?{urlencode({'searchtype': 's', 'searchterms': series.id})}"
        book = find_book(item.get("path") or "", item.get("title") or "", item.get("author") or "", books=public)
        if bot:
            return f"https://t.me/{bot}?start=b{book.id}" if book else None
        title = item.get("title")
        return f"{search}?{urlencode({'searchtype': 'm', 'searchterms': title})}" if title else None

    return link


def _bot_username(service) -> str:
    """Имя бота: из кеша журнала, иначе getMe (и в кеш). '' — бота нет/недоступен."""
    cfg, journal = service.cfg, service.journal
    if not cfg.get("telegram_token"):
        return ""
    cached = journal.get_state("tg_bot_username")
    if cached:
        return cached
    try:
        from fb2parser_core.telegram_notify import client_for
        me = client_for(cfg).call("getMe") or {}
        username = me.get("username", "")
    except Exception:
        _log.warning("telegram: имя бота для ссылок не получено", exc_info=True)
        return ""
    if username:
        journal.set_state("tg_bot_username", username)
    return username


def find_book(library_path: str, title: str, author: str, books=None):
    """Книга каталога по пути в библиотеке (каталог SOPDS обычно смотрит в
    ту же библиотеку), иначе по названию и автору; `books` — среди каких
    книг искать (по умолчанию — все существующие)."""
    import os

    from opds_catalog.models import Book
    books = books if books is not None else Book.objects.exclude(avail=0)
    if library_path:
        found = books.filter(path=os.path.dirname(library_path), filename=os.path.basename(library_path)).first()
        if found is not None:
            return found
    if title:
        query = books.filter(title=title)
        return (query.filter(authors__full_name=author).first() if author else None) or query.first()
    return None


def find_series(name: str, author: str, library_path: str = "", books=None):
    """Серия каталога: через её книгу по пути, иначе по имени (и автору —
    одноимённые серии у разных авторов не путаем); `books` — только серии,
    где есть книга из них."""
    from django.db.models import Exists, OuterRef

    from opds_catalog.models import Series, bseries
    book = find_book(library_path, "", "", books=books) if library_path else None
    if book is not None:
        link = bseries.objects.filter(book=book).select_related("ser").first()
        if link is not None:
            return link.ser
    if not name:
        return None
    query = Series.objects.filter(ser=name)
    if books is not None:
        query = query.filter(Exists(books.filter(series=OuterRef("pk"))))
    return (query.filter(book__authors__full_name=author).first() if author else None) or query.first()
