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

        sent = notify(service, result, link=catalog_link(service.cfg.get("public_url", "")))
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


def catalog_link(public_url):
    """Ссылки сводки новинок на страницы сайта: серия — по id в каталоге,
    книга без серии — поиск по названию. Без адреса сайта ссылок нет."""
    base = (public_url or "").rstrip("/")
    if not base:
        return None
    from urllib.parse import urlencode

    from django.urls import reverse

    from opds_catalog.models import Series

    search = base + reverse("web:searchbooks")

    def link(item):
        if item["kind"] == "series":
            series = Series.objects.filter(ser=item["series"]).first()
            return f"{search}?{urlencode({'searchtype': 's', 'searchterms': series.id})}" if series else None
        title = item.get("title")
        return f"{search}?{urlencode({'searchtype': 'm', 'searchterms': title})}" if title else None

    return link
