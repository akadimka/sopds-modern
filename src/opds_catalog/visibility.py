"""Какие книги видит пользователь (docs/multiuser-design.md, «Правило
видимости»; docs/multiuser-plan.md шаг 1.5).

- суперпользователь (роль сайта «Суперпользователь») — все книги (avail > 0);
- пользователь — книги, у которых среди владельцев есть он сам, тот, кто
  открыл ему свою библиотеку (`LibraryShare`), или владелец с открытой
  всем библиотекой (`UserProfile.library_public`);
- аноним (только при выключенной авторизации SOPDS_AUTH) — книги открытых
  всем библиотек.

Авторы, серии, жанры, папки каталога и счётчики — только по видимым
книгам: сущность без видимых книг не показывается (иначе видны имена
авторов и серий чужих книг). Книга по id, которую пользователь не видит, —
404, как несуществующая (перебором id нельзя узнать, что книга есть).
"""
from __future__ import annotations

from typing import Optional

from django.core.cache import cache
from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.http import Http404

from opds_catalog.models import (
    Author,
    Book,
    BookOwner,
    Catalog,
    Genre,
    LibraryShare,
    Series,
)

_GENERATION_KEY = "visibility:generation"
CACHE_SECONDS = 300


def is_library_admin(user) -> bool:
    """Видит всю библиотеку: роль «Суперпользователь» (is_superuser) — она же
    управляет пользователями и fb2parser; «суперпользователь = Супер»
    (docs/multiuser-design.md)."""
    return bool(user is not None and getattr(user, "is_authenticated", False)
                and user.is_active and user.is_superuser)


_is_super = is_library_admin


def accessible_owner_ids(user) -> Optional[set[int]]:
    """Владельцы, чьи книги видит пользователь; None — все (суперпользователь)."""
    if _is_super(user):
        return None
    from sopds_web_backend.models import (
        UserProfile,  # sopds_web_backend зависит от opds_catalog, не наоборот
    )

    ids = set(UserProfile.objects.filter(library_public=True, user__is_active=True)
              .values_list("user_id", flat=True))
    if user is not None and getattr(user, "is_authenticated", False):
        ids |= set(LibraryShare.objects.filter(grantee=user, owner__is_active=True)
                   .values_list("owner_id", flat=True))
        ids.add(user.pk)
    return ids


def visible_books(user, library=None) -> QuerySet:
    """Видимые пользователю книги; `library` — только книги одной
    библиотеки (владелец или его id), если она пользователю доступна."""
    return restrict_books(Book.objects.all(), user, library)


def restrict_books(qs: QuerySet, user, library=None) -> QuerySet:
    """Оставить в запросе книг (любом: поиск, полка, папка) только видимые."""
    qs = qs.filter(avail__gt=0)
    ids = accessible_owner_ids(user)
    if library is not None:
        lib_id = getattr(library, "pk", library)
        if ids is not None and lib_id not in ids:
            return qs.none()
        ids = {lib_id}
    if ids is None:
        return qs
    return qs.filter(Exists(BookOwner.objects.filter(book=OuterRef("pk"), user_id__in=ids)))


def get_visible_book_or_404(user, book_id) -> Book:
    """Книга по id, если пользователь её видит; иначе — 404, как несуществующая."""
    try:
        book_id = int(book_id)
    except (TypeError, ValueError):
        raise Http404("No such book") from None
    book = visible_books(user).filter(pk=book_id).first()
    if book is None:
        raise Http404("No such book")
    return book


def _with_visible_books(qs: QuerySet, user, relation: str, library=None) -> QuerySet:
    if _is_super(user) and library is None:
        return qs
    return qs.filter(Exists(visible_books(user, library).filter(**{relation: OuterRef("pk")})))


def visible_authors(user, qs: Optional[QuerySet] = None, library=None) -> QuerySet:
    return _with_visible_books(Author.objects.all() if qs is None else qs, user, "authors", library)


def visible_series(user, qs: Optional[QuerySet] = None, library=None) -> QuerySet:
    return _with_visible_books(Series.objects.all() if qs is None else qs, user, "series", library)


def visible_genres(user, qs: Optional[QuerySet] = None, library=None) -> QuerySet:
    return _with_visible_books(Genre.objects.all() if qs is None else qs, user, "genres", library)


def visible_book_count(user, relation: str = "book", library=None) -> Count:
    """`Count` для аннотации — только видимые книги (для Author/Series/Genre
    обратная связь называется "book")."""
    if _is_super(user) and library is None:
        return Count(relation, distinct=True)
    return Count(relation, filter=Q(**{f"{relation}__in": visible_books(user, library)}), distinct=True)


def _generation() -> int:
    return cache.get(_GENERATION_KEY) or 0


def invalidate() -> None:
    """Видимость изменилась (владение, доступ, скан) — сбросить кэш счётчиков
    и папок у всех пользователей."""
    try:
        cache.incr(_GENERATION_KEY)
    except ValueError:
        cache.set(_GENERATION_KEY, 1, None)


def _cache_key(kind: str, user) -> str:
    who = getattr(user, "pk", None) if user is not None and getattr(user, "is_authenticated", False) else "anon"
    return f"visibility:{kind}:{who}:{_generation()}"


def _catalogs_with_books(books: QuerySet) -> set[int]:
    """Папки, в которых (или ниже которых) лежат книги из `books`."""
    parents = dict(Catalog.objects.values_list("id", "parent_id"))
    ids: set[int] = set()
    for cat_id in set(books.values_list("catalog_id", flat=True)):
        while cat_id is not None and cat_id not in ids:
            ids.add(cat_id)
            cat_id = parents.get(cat_id)
    return ids


def visible_catalog_ids(user) -> Optional[set[int]]:
    """Папки каталога, в которых (или ниже которых) есть видимые книги;
    None — все (суперпользователь)."""
    if _is_super(user):
        return None
    key = _cache_key("catalogs", user)
    ids = cache.get(key)
    if ids is None:
        ids = _catalogs_with_books(visible_books(user))
        cache.set(key, ids, CACHE_SECONDS)
    return ids


def user_counters(user) -> dict[str, int]:
    """Счётчики главной страницы и OPDS — по видимым книгам; у
    суперпользователя тоже (все существующие книги), а не общий `Counter`
    сканера: тот считает и логически удалённые книги, и пустые папки, и
    числа в шапке расходились бы со списками."""
    key = _cache_key("counters", user)
    counters = cache.get(key)
    if counters is None:
        books = visible_books(user)

        def with_books(model, relation):
            return model.objects.filter(Exists(books.filter(**{relation: OuterRef("pk")}))).count()

        counters = {"books": books.count(),
                    "catalogs": len(_catalogs_with_books(books)),
                    "authors": with_books(Author, "authors"),
                    "genres": with_books(Genre, "genres"),
                    "series": with_books(Series, "series")}
        cache.set(key, counters, CACHE_SECONDS)
    return counters
