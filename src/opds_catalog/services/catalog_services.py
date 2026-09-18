"""Сервисы для работы с каталогами."""

from __future__ import annotations

import logging

from django.db.models import Prefetch, QuerySet
from django.utils.html import strip_tags

from opds_catalog.models import Book, Catalog, bookshelf
from opds_catalog.utils import get_lang_name

DUMMY_CATALOG = Catalog(id=0, cat_name="Empty", cat_type=0)

log = logging.getLogger(__name__)


def get_root() -> Catalog:
    """Возвращает корневой каталог."""
    try:
        cat = Catalog.objects.get(parent__id=None)
        return cat
    except Exception as e:
        log.warning(e)
    return DUMMY_CATALOG


def get_by_id(id: int) -> Catalog:
    """Возвращает каталог по идентификатору.

    :param id: Идентификатор каталога
    :type id: int

    :returns: Найденный каталог или каталог-заглушку если каталога с таким идентификатором
    не существует
    :rtype: Catalog
    """
    try:
        return Catalog.objects.get(id=id)
    except Catalog.DoesNotExist:
        log.error(f"Catalog with id={id} does not exists")
        return DUMMY_CATALOG


def get_catalogs_query(root: Catalog | None) -> QuerySet[Catalog, Catalog]:
    """Запрос подкаталогов текущего каталога.

    :param root: каталог, для которого требуется найти подкаталоги
    :type root: Catalog|None

    :returns: Запрос, позволяющий получить подкаталоги
    :rtype: QuerySet[Catalog, Catalog]
    """
    return Catalog.objects.filter(parent=root)


def get_books_query(catalog: Catalog) -> QuerySet[Book, Book]:
    """Запрос книг в каталоге.

    :param catalog: каталог, в котором требуется найти книги
    :type catalog: Catalog

    :returns: Запрос, позволяющий получить книги
    :rtype: Queryset[Book, Book]
    """
    return Book.objects.filter(catalog=catalog)


def get_catalogs_count(root: Catalog) -> int:
    """Запрос числа подкаталогов в каталоге."""
    return get_catalogs_query(root).count()


def get_books_count(root: Catalog) -> int:
    """Запрос числа книг в каталоге."""
    return get_books_query(root).count()


def _catalog_row_to_dict(row) -> dict:
    return {
        "is_catalog": 1,
        "title": row.cat_name,
        "id": row.id,
        "cat_type": row.cat_type,
        "parent_id": row.parent_id,
        "prefix": "c",
    }


def _book_row_to_dict(row, auth_enabled: bool) -> dict:
    authors_list = list(row.c_authors)
    genres_list = list(row.c_genres)
    series_list = list(row.c_series)
    ser_no_list = list(row.c_ser_no)

    readtime = None
    if auth_enabled and hasattr(row, "c_bookshelf") and row.c_bookshelf:
        readtime = row.c_bookshelf[0].readtime

    return {
        "is_catalog": 0,
        "lang_code": row.lang_code,
        "lang": get_lang_name(row.lang),
        "filename": row.filename,
        "path": row.path,
        "registerdate": row.registerdate,
        "id": row.id,
        "annotation": strip_tags(row.annotation),
        "docdate": row.docdate,
        "format": row.format,
        "title": row.title,
        "filesize": row.filesize // 1000,
        "authors": authors_list,
        "genres": genres_list,
        "series": series_list,
        "ser_no": ser_no_list,
        "readtime": readtime,
        "prefix": "b",
        "samlib_rating": getattr(row, "samlib_rating", None),
        "authortoday_rating": getattr(row, "authortoday_rating", None),
        "fantlab_rating": getattr(row, "fantlab_rating", None),
        "litmarket_rating": getattr(row, "litmarket_rating", None),
    }


def paginated_catalog_content(
    cat: Catalog,
    current_page: int,
    pager_max_items: int,
    user=None,
    auth_enabled: bool = False,
) -> tuple[list, dict]:
    """Предоставляет содержимое каталога в виде одной страницы.

    Баг №101: раньше ВСЕ подкаталоги и ВСЕ книги папки полностью
    материализовались в Python-список и только потом оборачивались в
    Paginator — то есть каждый запрос страницы вытягивал из БД и
    строил словари для целой папки, а не только для нужной страницы.
    На папке из тысяч книг (например, "плоская" неотсортированная
    свалка файлов) это означало полный проход по ВСЕЙ папке на каждый
    запрос страницы. Теперь считаем count() подкаталогов/книг (2
    быстрых запроса) и вытягиваем через QuerySet-слайсинг (LIMIT/OFFSET
    на уровне БД) только тот диапазон записей, что реально попадает на
    запрошенную страницу — подкаталоги всегда идут первыми, книги
    следом, как и раньше, просто без материализации целиком.
    """

    # Prefetch связанных объектов для книг
    prefetch = [
        Prefetch("authors", to_attr="c_authors"),
        Prefetch("genres", to_attr="c_genres"),
        Prefetch("series", to_attr="c_series"),
        Prefetch("bseries_set", to_attr="c_ser_no"),
    ]
    if auth_enabled and user is not None:
        prefetch.append(
            Prefetch(
                "bookshelf_set",
                queryset=bookshelf.objects.filter(user=user),
                to_attr="c_bookshelf",
            )
        )

    catalogs_list = get_catalogs_query(cat).order_by("cat_name")
    books_list = (
        get_books_query(cat).select_related(
            "samlib_rating", "authortoday_rating", "fantlab_rating", "litmarket_rating"
        ).order_by("search_title").prefetch_related(*prefetch)
    )

    catalogs_count = catalogs_list.count()
    books_count = books_list.count()
    total_count = catalogs_count + books_count

    per_page = pager_max_items if pager_max_items and pager_max_items > 0 else 1
    num_pages = max(1, -(-total_count // per_page))  # ceil(total_count / per_page)

    try:
        page_number = int(current_page)
        if page_number < 1 or page_number > num_pages:
            raise ValueError
    except (TypeError, ValueError):
        # Тот же фолбэк, что и раньше был у Paginator для
        # PageNotAnInteger/EmptyPage — откат на ПОСЛЕДНЮЮ страницу.
        page_number = num_pages

    start = (page_number - 1) * per_page
    end = start + per_page

    merged: list[dict] = []

    cat_start = max(0, min(start, catalogs_count))
    cat_end = max(0, min(end, catalogs_count))
    if cat_start < cat_end:
        for row in catalogs_list[cat_start:cat_end]:
            merged.append(_catalog_row_to_dict(row))

    book_start = max(0, min(start - catalogs_count, books_count))
    book_end = max(0, min(end - catalogs_count, books_count))
    if book_start < book_end:
        for row in books_list[book_start:book_end]:
            merged.append(_book_row_to_dict(row, auth_enabled))

    pager_dict = {
        "num_pages": num_pages,
        "has_previous": page_number > 1,
        "has_next": page_number < num_pages,
        "previous_page_number": page_number - 1 if page_number > 1 else 1,
        "next_page_number": page_number + 1 if page_number < num_pages else num_pages,
        "number": page_number,
        "page_range": list(range(1, num_pages + 1)),
    }

    return merged, pager_dict
