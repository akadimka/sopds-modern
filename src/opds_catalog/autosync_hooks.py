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

        sent = notify(service, result, link=catalog_link(service))
    except Exception:
        log.exception("Autosync notification failed")
        return {}
    if sent:
        log.info("Autosync notifications: %s", sent)
    return sent


def summary(autosync: Optional[Tuple[Any, Any]]) -> Optional[Dict[str, Any]]:
    """Итог для строки статуса ручного скана; None — автосинхронизация выключена."""
    if autosync is None:
        return {"status": "error", "error": "см. лог сервера"}
    _service, r = autosync
    if r.status == "off":
        return None
    return {"status": r.status, "mode": r.mode, "moved": len(r.moved), "pending": r.pending,
            "kept": r.kept, "removed": r.removed, "would_move": r.would_move, "error": r.error}


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
        if item["kind"] == "series":
            series = find_series(item.get("series") or "", item.get("author") or "", item.get("path") or "")
            if series is None:
                return None
            if bot:
                return f"https://t.me/{bot}?start=s{series.id}"
            return f"{search}?{urlencode({'searchtype': 's', 'searchterms': series.id})}"
        book = find_book(item.get("path") or "", item.get("title") or "", item.get("author") or "")
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
        from fb2parser_core.telegram_notify import TelegramClient
        me = TelegramClient(cfg["telegram_token"], cfg.get("telegram_proxy", "")).call("getMe") or {}
        username = me.get("username", "")
    except Exception:
        _log.warning("telegram: имя бота для ссылок не получено", exc_info=True)
        return ""
    if username:
        journal.set_state("tg_bot_username", username)
    return username


def find_book(library_path: str, title: str, author: str):
    """Книга каталога по пути в библиотеке (каталог SOPDS обычно смотрит в
    ту же библиотеку), иначе по названию и автору."""
    import os

    from opds_catalog.models import Book
    books = Book.objects.exclude(avail=0)
    if library_path:
        found = books.filter(path=os.path.dirname(library_path), filename=os.path.basename(library_path)).first()
        if found is not None:
            return found
    if title:
        query = books.filter(title=title)
        return (query.filter(authors__full_name=author).first() if author else None) or query.first()
    return None


def find_series(name: str, author: str, library_path: str = ""):
    """Серия каталога: через её книгу по пути, иначе по имени (и автору —
    одноимённые серии у разных авторов не путаем)."""
    from opds_catalog.models import Series, bseries
    book = find_book(library_path, "", "") if library_path else None
    if book is not None:
        link = bseries.objects.filter(book=book).select_related("ser").first()
        if link is not None:
            return link.ser
    if not name:
        return None
    query = Series.objects.filter(ser=name)
    return (query.filter(book__authors__full_name=author).first() if author else None) or query.first()
