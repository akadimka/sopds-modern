"""Поиск и навигация по каталогу в Telegram-боте (docs/telegram-library-bot.md, этап B).

Каждый «экран» — текст + inline-клавиатура; переходы — callback_data до
64 байт: `q:<запрос>:<стр>` результаты, `a:<автор>:<стр>:<запрос>` книги
автора, `s:<серия>:<стр>:<запрос>` тома серии, `b:<книга>:<запрос>`
карточка. Текст запроса хранится в кеше под коротким id (в callback_data
он не помещается).
"""
from __future__ import annotations

import html
import re
import secrets
from functools import reduce
from operator import and_, or_
from typing import Any, Dict, List, Optional, Tuple

from django.core.cache import cache
from django.db.models import Count, Q

from opds_catalog.models import Author, Book, Series, bseries

PAGE = 10
MAX_ITEMS = 200
QUERY_TTL = 3 * 24 * 3600
LABEL_MAX = 60

Screen = Tuple[str, Dict[str, Any]]


def _e(s: str) -> str:
    return html.escape(s or "", quote=False)


def _cut(s: str, n: int = LABEL_MAX) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def words(text: str) -> List[str]:
    return [w for w in re.findall(r"\w+", (text or "").upper()) if len(w) >= 2]


def _has(field: str, word: str) -> Q:
    """Слово в поле — с «ё» и с «е» (в каталоге и в запросе пишут по-разному)."""
    variants = {word, word.replace("Ё", "Е"), word.replace("Е", "Ё")}
    return reduce(or_, [Q(**{f"{field}__contains": v}) for v in variants])


# ---------- запросы ----------
def store_query(text: str) -> str:
    qid = secrets.token_hex(4)
    cache.set(f"tgq:{qid}", text, QUERY_TTL)
    return qid


def load_query(qid: str) -> Optional[str]:
    return cache.get(f"tgq:{qid}") if qid else None


def search(text: str) -> List[Tuple[str, int, str]]:
    """[(вид, id, подпись кнопки)] — авторы, серии, книги."""
    ws = words(text)
    if not ws:
        return []
    items: List[Tuple[str, int, str]] = []
    authors = (Author.objects.filter(reduce(and_, [_has("search_full_name", w) for w in ws]))
               .annotate(n=Count("book", filter=Q(book__avail__gt=0), distinct=True)).filter(n__gt=0)
               .order_by("-n", "search_full_name")[:MAX_ITEMS])
    items += [("a", a.id, f"👤 {_cut(a.full_name, 50)} ({a.n})") for a in authors]
    series = (Series.objects.filter(reduce(and_, [_has("search_ser", w) for w in ws]))
              .annotate(n=Count("book", filter=Q(book__avail__gt=0), distinct=True)).filter(n__gt=0)
              .order_by("-n", "search_ser")[:MAX_ITEMS])
    items += [("s", s.id, f"📚 {_cut(s.ser, 50)} ({s.n})") for s in series]
    # Каждое слово — в названии или у автора, и хотя бы одно — в названии:
    # «Лукьяненко Дозор» найдёт «Ночной Дозор», а «Лукьяненко» — автора, а не все его книги.
    per_word = [_has("search_title", w) | _has("authors__search_full_name", w) for w in ws]
    in_title = reduce(or_, [_has("search_title", w) for w in ws])
    books = (Book.objects.exclude(avail=0).filter(reduce(and_, per_word)).filter(in_title)
             .distinct().prefetch_related("authors").order_by("search_title", "-docdate")[:MAX_ITEMS])
    items += [("b", b.id, f"📖 {_cut(b.title + ' — ' + _short_authors(b), LABEL_MAX)}") for b in books]
    return items


def _short_authors(book: Book) -> str:
    names = [a.full_name for a in book.authors.all()]
    return names[0] + (" и др." if len(names) > 1 else "") if names else ""


# ---------- экраны ----------
def _nav(prefix: str, page: int, total: int, suffix: str = "") -> List[Dict[str, str]]:
    pages = (total + PAGE - 1) // PAGE
    if pages <= 1:
        return []
    row = []
    if page > 0:
        row.append({"text": "◀", "callback_data": f"{prefix}:{page - 1}{suffix}"})
    row.append({"text": f"{page + 1}/{pages}", "callback_data": f"{prefix}:{page}{suffix}"})
    if page + 1 < pages:
        row.append({"text": "▶", "callback_data": f"{prefix}:{page + 1}{suffix}"})
    return row


def _back(qid: str) -> List[List[Dict[str, str]]]:
    return [[{"text": "🔍 К результатам", "callback_data": f"q:{qid}:0"}]] if load_query(qid) else []


def results_screen(qid: str, page: int = 0) -> Optional[Screen]:
    text = load_query(qid)
    if text is None:
        return None
    items = search(text)
    if not items:
        return (f"Ничего не нашлось по запросу «{_e(text)}». Попробуйте короче — фамилию автора "
                "или слово из названия.", {"inline_keyboard": []})
    counts = {k: sum(1 for i in items if i[0] == k) for k in "asb"}
    head = [f"🔍 «{_e(text)}»",
            f"Авторы: {counts['a']} · Серии: {counts['s']} · Книги: {counts['b']}"
            + (" (показаны первые)" if len(items) >= MAX_ITEMS else "")]
    chunk = items[page * PAGE:(page + 1) * PAGE]
    rows = [[{"text": label, "callback_data": f"{kind}:{oid}:0:{qid}" if kind != "b" else f"b:{oid}:{qid}"}]
            for kind, oid, label in chunk]
    nav = _nav(f"q:{qid}", page, len(items))
    return "\n".join(head), {"inline_keyboard": rows + ([nav] if nav else [])}


def _book_label(book: Book, ser_no: Optional[int] = None) -> str:
    prefix = f"{ser_no}. " if ser_no else ""
    return f"📖 {_cut(prefix + book.title)}"


def author_screen(author_id: int, page: int = 0, qid: str = "") -> Optional[Screen]:
    author = Author.objects.filter(id=author_id).first()
    if author is None:
        return None
    books = list(Book.objects.filter(authors=author_id).exclude(avail=0).order_by("search_title", "-docdate"))
    rows = [[{"text": _book_label(b), "callback_data": f"b:{b.id}:{qid}"}]
            for b in books[page * PAGE:(page + 1) * PAGE]]
    nav = _nav(f"a:{author_id}", page, len(books), f":{qid}")
    series = (Series.objects.filter(book__authors=author_id, book__avail__gt=0)
              .annotate(n=Count("book", distinct=True)).order_by("search_ser"))
    ser_rows = [[{"text": f"📚 {_cut(s.ser, 50)} ({s.n})", "callback_data": f"s:{s.id}:0:{qid}"}]
                for s in series[:PAGE]] if page == 0 else []
    text = f"👤 <b>{_e(author.full_name)}</b> — книг: {len(books)}"
    if ser_rows:
        text += "\nСерии — сверху, все книги — ниже."
    return text, {"inline_keyboard": ser_rows + rows + ([nav] if nav else []) + _back(qid)}


def series_screen(series_id: int, page: int = 0, qid: str = "") -> Optional[Screen]:
    series = Series.objects.filter(id=series_id).first()
    if series is None:
        return None
    links = list(bseries.objects.filter(ser=series_id, book__avail__gt=0).select_related("book")
                 .order_by("ser_no", "book__search_title"))
    rows = [[{"text": _book_label(link.book, link.ser_no), "callback_data": f"b:{link.book_id}:{qid}"}]
            for link in links[page * PAGE:(page + 1) * PAGE]]
    nav = _nav(f"s:{series_id}", page, len(links), f":{qid}")
    return (f"📚 <b>{_e(series.ser)}</b> — книг: {len(links)}",
            {"inline_keyboard": rows + ([nav] if nav else []) + _back(qid)})


def book_screen(book_id: int, qid: str = "", extra_rows: Optional[List[List[Dict[str, str]]]] = None
                ) -> Optional[Screen]:
    book = Book.objects.exclude(avail=0).filter(id=book_id).prefetch_related("authors", "genres").first()
    if book is None:
        return None
    authors = list(book.authors.all())
    ser = bseries.objects.filter(book=book).select_related("ser").first()
    lines = [f"📖 <b>{_e(book.title)}</b>"]
    if authors:
        lines.append("👤 " + _e(", ".join(a.full_name for a in authors)))
    if ser:
        lines.append(f"📚 {_e(ser.ser.ser)}" + (f", № {ser.ser_no}" if ser.ser_no else ""))
    genres = [g.subsection or g.genre for g in book.genres.all()]
    if genres:
        lines.append("🏷 " + _e(", ".join(dict.fromkeys(genres))))
    if book.annotation:
        lines += ["", _e(_cut(book.annotation, 700))]
    from opds_catalog.dl import available_formats
    rows = [[{"text": f"⬇ {fmt.upper()}", "callback_data": f"f:{book.id}:{fmt}"} for fmt in available_formats(book)]]
    rows += list(extra_rows or [])
    nav_row = []
    if authors:
        nav_row.append({"text": "👤 Автор", "callback_data": f"a:{authors[0].id}:0:{qid}"})
    if ser:
        nav_row.append({"text": "📚 Серия", "callback_data": f"s:{ser.ser_id}:0:{qid}"})
    if nav_row:
        rows.append(nav_row)
    return "\n".join(lines), {"inline_keyboard": rows + _back(qid)}


def screen_for(data: str) -> Optional[Screen]:
    """Экран по callback_data; None — устарел или не найден."""
    parts = data.split(":")
    try:
        if parts[0] == "q":
            return results_screen(parts[1], int(parts[2]))
        if parts[0] == "a":
            return author_screen(int(parts[1]), int(parts[2]), parts[3] if len(parts) > 3 else "")
        if parts[0] == "s":
            return series_screen(int(parts[1]), int(parts[2]), parts[3] if len(parts) > 3 else "")
        if parts[0] == "b":
            return book_screen(int(parts[1]), parts[2] if len(parts) > 2 else "")
    except (IndexError, ValueError):
        return None
    return None
