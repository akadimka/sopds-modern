"""Сервисные функции для работы с книгами."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from functools import reduce
from operator import and_
from typing import Callable, TypeVar

from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.core.paginator import EmptyPage, Paginator
from django.db.models import Count, IntegerField, Prefetch, Q, QuerySet, Value
from django.db.models.functions import Substr
from django.utils import timezone
from django.utils.html import escape, strip_tags
from django.utils.translation import gettext as _

from opds_catalog import visibility
from opds_catalog.models import Author, Book, bookshelf
from opds_catalog.services import SearchType
from opds_catalog.sopds_config import sopds_cfg as config
from opds_catalog.utils import get_lang_name, to_int

T = TypeVar("T")

SearchFunction = Callable[[bool, str, str | None, User | None], QuerySet[Book]]

DEFAULT_ORDER_BY = ["search_title", "-docdate"]


# Стратегии поиска
def find_by_bookshelf(
    auth_enabled: bool, _: str, __: str | None = None, user: User | None = None
) -> QuerySet[Book]:
    """Книги на книжной полки пользователя."""
    if not auth_enabled:
        raise ImproperlyConfigured(
            f"Attempt to read {user} bookshelf from catalog without authorization"
        )
    return Book.objects.filter(bookshelf__user=user).order_by("-bookshelf__readtime")


def find_by_author_and_series(
    _: bool, author_id: str, series_id: str | None = None, __=None
) -> QuerySet[Book]:
    """Поиск книг по автору и серии."""
    return Book.objects.filter(
        authors=to_int(author_id), series=to_int(series_id)
    ).order_by(*DEFAULT_ORDER_BY, "bseries__ser_no")


def find_book_doubles(
    _: bool, book_id: str, __: str | None = None, user=None
) -> QuerySet[Book]:
    """Поиск дубликатов книги (сама книга должна быть видна пользователю)."""
    mbook = visibility.visible_books(user).only("title").filter(id=to_int(book_id)).first()
    if mbook is None:
        return Book.objects.none()
    return (
        Book.objects.filter(title__iexact=mbook.title, authors__in=mbook.authors.all())
        .exclude(id=to_int(book_id))
        .order_by(*DEFAULT_ORDER_BY)
    )


def title_or_filename_match(term: str) -> Q:
    """Каждое слово запроса — в названии или в имени файла («Автор -
    Название»): «Стругацкие Пикник» находит «Стругацкие Аркадий и Борис -
    Пикник на обочине». Сравнение — по полям в верхнем регистре (SQLite)."""
    words = term.upper().split()
    if not words:
        return Q(pk__in=[])
    return reduce(and_, [Q(search_title__contains=w) | Q(search_filename__contains=w) for w in words])


def find_books_by_substring(
    _: bool, term: str, __: str | None = None, ___=None
) -> QuerySet[Book]:
    """Поиск книг по словам — в названии или в имени файла."""
    return Book.objects.filter(title_or_filename_match(term)).order_by(*DEFAULT_ORDER_BY)


def find_books_by_start_letters(
    _: bool, term: str, __: str | None = None, ___=None
) -> QuerySet[Book]:
    """Поиск книг, начинающихся с заданной последовательности символов."""
    return Book.objects.filter(search_title__startswith=term.upper()).order_by(
        *DEFAULT_ORDER_BY
    )


def find_books_by_exact_match(
    _: bool, term: str, __: str | None = None, ___=None
) -> QuerySet[Book]:
    """Поиск книг по названию."""
    return Book.objects.filter(search_title=term.upper()).order_by(*DEFAULT_ORDER_BY)


def find_books_by_author(
    _: bool, author_id: str, __: str | None = None, ___=None
) -> QuerySet[Book]:
    """Поиск книг по автору."""
    return Book.objects.filter(authors=to_int(author_id)).order_by(*DEFAULT_ORDER_BY)


def find_books_by_series(
    _: bool, series_id: str, __: str | None = None, ___=None
) -> QuerySet[Book]:
    """Поиск книг по серии."""
    return Book.objects.filter(series=to_int(series_id)).order_by(
        *DEFAULT_ORDER_BY, "bseries__ser_no"
    )


def find_books_by_genre(
    _: bool, genre_id: str, __: str | None = None, ___=None
) -> QuerySet[Book]:
    """Поиск книг по жанру."""
    return Book.objects.filter(genres=to_int(genre_id)).order_by(*DEFAULT_ORDER_BY)


def find_book_by_id(
    _: bool, book_id: str, __: str | None = None, ___=None
) -> QuerySet[Book]:
    """Поиск книги по идентификатору."""
    return Book.objects.filter(id=book_id)


def added_on(day: date) -> Q:
    """Книги, добавленные в этот день по TIME_ZONE. Интервалом, а не
    registerdate__date: тот в SQLite переводит время каждой книги функцией
    Python — на 24 тыс. книг в 5 раз медленнее (главная, «Новости»)."""
    start = timezone.make_aware(datetime.combine(day, time.min))
    end = timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))
    return Q(registerdate__gte=start, registerdate__lt=end)


def find_books_added_on(
    _: bool, day: str, folder: str | None = None, ___=None
) -> QuerySet[Book]:
    """Книги, добавленные в этот день (YYYY-MM-DD, по TIME_ZONE) — ссылки
    «Новостей» на главной; folder — только из этой папки верхнего уровня
    (жанр, баг №166)."""
    try:
        day = date.fromisoformat(day)
    except (TypeError, ValueError):
        return Book.objects.none()
    books = Book.objects.filter(added_on(day))
    if folder:
        books = books.filter(Q(path=folder) | Q(path__startswith=folder + "/") | Q(path__startswith=folder + "\\"))
    return books.order_by(*DEFAULT_ORDER_BY)


SEARCH_BOOK_REGISTRY: dict[str, SearchFunction] = {
    SearchType.BY_USER: find_by_bookshelf,
    SearchType.BY_AUTHOR_AND_SERIES: find_by_author_and_series,
    SearchType.DOUBLES: find_book_doubles,
    SearchType.BY_SUBSTRING: find_books_by_substring,
    SearchType.BY_START_WITH: find_books_by_start_letters,
    SearchType.BY_EXACT_MATCH: find_books_by_exact_match,
    SearchType.BY_AUTHOR: find_books_by_author,
    SearchType.BY_SERIES: find_books_by_series,
    SearchType.BY_GENRE: find_books_by_genre,
    SearchType.BY_ID: find_book_by_id,
    SearchType.ADDED_ON: find_books_added_on,
}


def search_book(
    type: str, term: str, second_term: str | None, user
) -> QuerySet[Book, Book]:
    """Формирование запроса на выборку книг — только видимых пользователю
    (docs/multiuser-design.md); `user` обязателен, None — аноним."""
    search_function = SEARCH_BOOK_REGISTRY.get(type)
    if search_function is None:
        raise ValueError(f"Search type '{type}' is not supported")
    return visibility.restrict_books(search_function(config.SOPDS_AUTH, term, second_term, user), user)


def _build_book_item(row: Book, user=None, auth_enabled=False) -> dict:
    """Преобразует Book в словарь для постраничного вывода.

    Требует prefetch_related:
      - Prefetch('authors', to_attr='c_authors')
      - Prefetch('genres', to_attr='c_genres')
      - Prefetch('series', to_attr='c_series')
      - Prefetch('bseries_set', to_attr='c_ser_no')
    При auth_enabled дополнительно:
      - Prefetch('bookshelf_set', queryset=bookshelf.objects.filter(user=user), to_attr='c_bookshelf')
    """
    authors_list = list(row.c_authors)
    genres_list = list(row.c_genres)
    series_list = list(row.c_series)
    ser_no_list = list(row.c_ser_no)

    readtime = None
    if auth_enabled and hasattr(row, "c_bookshelf") and row.c_bookshelf:
        readtime = row.c_bookshelf[0].readtime

    return {
        "doubles": 0,
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
        "display_title": row.display_title,
        "filesize": row.filesize,
        "authors": authors_list,
        "genres": genres_list,
        "series": series_list,
        "ser_no": ser_no_list,
        "readtime": readtime,
        "samlib_rating": getattr(row, 'samlib_rating', None),
        "authortoday_rating": getattr(row, 'authortoday_rating', None),
        "fantlab_rating": getattr(row, 'fantlab_rating', None),
        "litmarket_rating": getattr(row, 'litmarket_rating', None),
    }


def _dedup_items(
    items: list[dict],
) -> tuple[list[dict], str, set]:
    """Схлопывает последовательные элементы с одинаковым названием+автором."""
    if not items:
        return [], "", set()

    deduped: list[dict] = []
    prev_title = ""
    prev_authors_set: set[int] = set()

    for p in items:
        title: str = p["title"]
        authors_set: set[int] = {a.id for a in p["authors"]}
        if title.upper() == prev_title.upper() and authors_set == prev_authors_set:
            deduped[-1]["doubles"] += 1
        else:
            deduped.append(p)
        prev_title = title
        prev_authors_set = authors_set

    return deduped, prev_title, prev_authors_set


def _paginator_to_dict(page) -> dict:
    """Преобразует Django Paginator Page в словарь, совместимый с get_data_dict()."""
    paginator = page.paginator
    return {
        "num_pages": paginator.num_pages,
        "has_previous": page.has_previous(),
        "has_next": page.has_next(),
        "previous_page_number": page.previous_page_number()
        if page.has_previous()
        else 1,
        "next_page_number": page.next_page_number()
        if page.has_next()
        else paginator.num_pages,
        "number": page.number,
        "page_range": list(paginator.page_range),
    }


def paginated_book_content(
    books: QuerySet[Book, Book],
    page_num: int,
    search_doubles: bool = False,
    user=None,
    auth_enabled: bool = False,
) -> tuple[list[dict], dict]:
    """Постраничный вывод списка книг.

    :param books: QuerySet книг с применённым search_filter.
    :param page_num: Номер страницы.
    :param search_doubles: Если True — не скрывать дубликаты.
    :param user: Пользователь (нужен для readtime, auth должен быть включен).
    :param auth_enabled: Флаг авторизации.

    :returns: (items, paginator_dict)
    """
    maxitems = config.SOPDS_MAXITEMS

    # Prefetch связанных объектов для устранения N+1
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
    books = books.select_related(
        'samlib_rating', 'authortoday_rating', 'fantlab_rating', 'litmarket_rating'
    ).prefetch_related(*prefetch)

    django_paginator = Paginator(books, maxitems)
    # Нумерация страниц 1-based; форма поиска на главной шлёт page=0.
    # Paginator.get_page() не подходит: для номера < 1 он отдаёт ПОСЛЕДНЮЮ страницу.
    try:
        page = django_paginator.page(max(page_num, 1))
    except EmptyPage:
        page = django_paginator.page(django_paginator.num_pages)

    summary_DOUBLES_HIDE = config.SOPDS_DOUBLES_HIDE and not search_doubles

    # Собираем элементы страницы
    if summary_DOUBLES_HIDE and page.has_previous():
        # Добавляем последний элемент предыдущей страницы для корректной
        # дедупликации на границе страниц
        prev_page = django_paginator.page(page.previous_page_number())
        page_objects = [prev_page.object_list[len(prev_page.object_list) - 1]] + list(
            page.object_list
        )
    else:
        page_objects = list(page.object_list)

    items = [
        _build_book_item(row, user=user, auth_enabled=auth_enabled)
        for row in page_objects
    ]

    if summary_DOUBLES_HIDE:
        items, prev_title, prev_authors_set = _dedup_items(items)

        # Удаляем граничный элемент с предыдущей страницы
        if page.has_previous() and items:
            items.pop(0)

        # "Вытягиваем" дубликаты со следующей страницы
        if page.has_next() and items:
            next_page = django_paginator.page(page.next_page_number())
            for row in next_page.object_list:
                authors_set = {a.id for a in row.c_authors}
                if (
                    row.title.upper() == prev_title.upper()
                    and authors_set == prev_authors_set
                ):
                    items[-1]["doubles"] += 1
                else:
                    break

    return items, _paginator_to_dict(page)


def _field(obj, name):
    """Поле связанной записи — ORM-объекта или dict (тесты)."""
    return obj[name] if isinstance(obj, dict) else getattr(obj, name)


def book_description(item) -> str:
    """Описание книги в OPDS (HTML: <content type="text/html">). Всё, что
    пришло из файла книги — название, авторы, жанры, серии, имя файла,
    аннотация, — экранируется: читалка отрисовала бы разметку из метаданных."""

    def line(label: str, value) -> str:
        return f"<b>{escape(label)}</b> {escape(str(value))}<br/>"

    s = [line(_("Book name:"), item["title"])]
    if item["authors"]:
        s.append(line(_("Authors:"), ", ".join(_field(a, "full_name") for a in item["authors"])))
    if item["genres"]:
        s.append(line(_("Genres:"), ", ".join(_field(g, "subsection") for g in item["genres"])))
    if item["series"]:
        s.append(line(_("Series:"), ", ".join(_field(x, "ser") for x in item["series"])))
    if item["ser_no"]:
        s.append(line(_("No in Series:"), ", ".join(str(_field(x, "ser_no")) for x in item["ser_no"])))
    s.append(line(_("File:"), item["filename"]))
    s.append(line(_("File size:"), item["filesize"]))
    s.append(line(_("Changes date:"), item["docdate"]))
    s.append(f"<p class='book'>{escape(item['annotation'])}</p>")
    return "".join(s)


def find_books_by_template(
    chars: str, length: int, lang_code: int | None = None, *, user
) -> QuerySet:
    """Поиск книг по шаблону.

    :param chars: Шаблон начала названия книги.
    :param length: Длина шаблона.
    :param lang_code: Опциональный код языка для фильтрации.
    :returns: Запрос для поиска книг по шаблону.
    """
    books = (
        visibility.restrict_books(Book.objects.filter(search_title__startswith=chars), user)
        .annotate(
            l=Value(length, output_field=IntegerField()),
            sid=Substr("search_title", 1, length),
        )
        .values("l", "sid")
        .annotate(cnt=Count("sid"))
        .order_by("sid")
    )
    if lang_code:
        books = books.filter(lang_code=lang_code)

    return books


def author_books_count(author: Author | int, *, user) -> int:
    """Подсчет числа видимых пользователю книг автора."""
    return visibility.visible_books(user).filter(authors=author).count()
