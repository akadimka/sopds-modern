"""«Новости» на главной (docs/news-design.md): поступления по дням и новости
суперпользователя.

Поступления — одна новость на день добавления (Book.registerdate, день — по
TIME_ZONE), только видимые пользователю книги: больше DETAIL_MAX за день —
число книг по жанрам со ссылками и «показать все», иначе список «Автор -
Название» с жанром. Жанр — папка верхнего уровня (баг №166), как в блоке
«Жанры» и в сообщениях Telegram (то же DETAIL_MAX). Новый сборник —
обычная книга дня. Новости суперпользователя (models.News) идут вперемешку
с поступлениями по времени, закреплённые — первыми.
"""
from __future__ import annotations

import html
import os
import re
from datetime import date, timedelta
from types import SimpleNamespace
from urllib.parse import urlencode

from django.core.paginator import Paginator
from django.db.models import Count, Max, Value
from django.db.models.functions import Concat, StrIndex, Substr, TruncDate
from django.urls import reverse
from django.utils import timezone

from fb2parser_core.telegram_notify import DIGEST_DETAIL_MAX as DETAIL_MAX
from opds_catalog.services.book_services import added_on

MAIN_PAGE_ITEMS = 5
PAGE_SIZE = 20
EDIT_HOURS = 24
SEARCH_BY_DAY = "n"          # opds_catalog.services.SearchType.ADDED_ON


def genre_folder(path: str) -> str:
    """Жанр книги — первая часть пути («Фантастика/Автор/…» → «Фантастика»);
    книга в корне библиотеки — без жанра."""
    first = re.split(r"[\\/]", path or "")[0]
    return "" if first in ("", ".") else first


def genres_by_folder(books, keep_root: bool = False) -> list:
    """Число книг по папкам верхнего уровня (больше — выше). Без keep_root
    книги из корня библиотеки не считаются (блок «Жанры» на главной)."""
    sep = Value(os.sep)
    folder = Substr("path", 1, StrIndex(Concat("path", sep), sep) - 1)
    rows = (books.order_by().annotate(genre_folder=folder)
            .values("genre_folder").annotate(cnt=Count("id")).order_by("-cnt", "genre_folder"))
    result = []
    for r in rows:
        name = "" if r["genre_folder"] in ("", ".") else r["genre_folder"]
        if name or keep_root:
            result.append(SimpleNamespace(name=name, cnt=r["cnt"]))
    return result


def day_books_url(day: date, folder: str | None = None) -> str:
    params = {"searchtype": SEARCH_BY_DAY, "searchterms": day.isoformat()}
    if folder:
        params["searchterms0"] = folder
    return f"{reverse('web:searchbooks')}?{urlencode(params)}"


def _days(visible):
    return (visible.order_by().annotate(day=TruncDate("registerdate"))
            .values("day").annotate(cnt=Count("id"), last=Max("registerdate")).order_by("-day"))


def _feed(visible):
    """Все новости без подробностей: закреплённые — первыми, дальше по времени."""
    from .models import News

    posts = [SimpleNamespace(kind="post", when=n.created, news=n, pinned=n.pinned)
             for n in News.objects.filter(hidden=False)]
    days = [SimpleNamespace(kind="books", when=d["last"], day=d["day"], total=d["cnt"], pinned=False)
            for d in _days(visible)]
    entries = sorted(posts + days, key=lambda e: e.when, reverse=True)
    return [e for e in entries if e.pinned] + [e for e in entries if not e.pinned]


def _expand(entries, visible):
    """Подробности поступлений — только для показываемых новостей."""
    for e in entries:
        if e.kind != "books":
            continue
        books = visible.filter(added_on(e.day))
        e.all_url = day_books_url(e.day)
        if e.total > DETAIL_MAX:
            e.genres = [SimpleNamespace(name=g.name, cnt=g.cnt, url=day_books_url(e.day, g.name) if g.name else "")
                        for g in genres_by_folder(books, keep_root=True)]
        else:
            e.books = [SimpleNamespace(id=b.id, title=b.display_title, genre=genre_folder(b.path),
                                       url=f"{reverse('web:searchbooks')}?{urlencode({'searchtype': 'i', 'searchterms': b.id})}")
                       for b in books.order_by("search_title").only("id", "filename", "title", "path")]
    return entries


def _mark_new(entries, user):
    """Метка «новое» — у новостей после прошлого визита; визит запоминается.
    Первый визит — без меток (иначе новым было бы всё)."""
    if not getattr(user, "is_authenticated", False):
        return
    from .models import UserProfile

    seen = UserProfile.objects.filter(user=user).values_list("news_seen", flat=True).first()
    for e in entries:
        e.is_new = seen is not None and e.when > seen
    UserProfile.objects.update_or_create(user=user, defaults={"news_seen": timezone.now()})


def main_page_news(user, visible) -> list:
    entries = _expand(_feed(visible)[:MAIN_PAGE_ITEMS], visible)
    _mark_new(entries, user)
    return entries


def news_page(user, visible, page_number):
    page = Paginator(_feed(visible), PAGE_SIZE).get_page(page_number)
    _expand(page.object_list, visible)
    _mark_new(page.object_list, user)
    return page


def can_edit(news) -> bool:
    """Править можно только последнюю новость и только EDIT_HOURS после публикации."""
    from .models import News

    latest = News.objects.order_by("-created").values_list("pk", flat=True).first()
    return news.pk == latest and timezone.now() - news.created < timedelta(hours=EDIT_HOURS)


def telegram_text(news) -> str:
    """Новость для канала Telegram (разметка HTML — текст экранирован)."""
    title, text = html.escape(news.title, quote=False), html.escape(news.text, quote=False)
    return f"📰 <b>{title}</b>" + (f"\n\n{text}" if text else "")
