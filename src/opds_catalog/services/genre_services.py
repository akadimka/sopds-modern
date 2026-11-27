"""Сервисы работы с жанрами."""

from typing import Any

from django.db.models import Count, Min, QuerySet
from django.http import Http404

from opds_catalog import visibility
from opds_catalog.models import Genre
from opds_catalog.utils import to_int


def get_genres(*, user) -> QuerySet[Genre, dict[str, Any]]:
    """Возвращает список жанров с количеством книг в каждом жанре."""
    return (
        Genre.objects.values("section")
        .annotate(section_id=Min("id"), num_book=visibility.visible_book_count(user))
        .filter(num_book__gt=0)
        .order_by("section")
    )


def get_genre_details(id: int, *, user) -> QuerySet[Genre, dict[str, Any]]:
    """Возвращает список поджанров жанра и количество книг в нем."""
    section = get_genre_section(id, user=user)
    return (
        Genre.objects.filter(section=section)
        .annotate(num_book=visibility.visible_book_count(user))
        .filter(num_book__gt=0)
        .values()
        .order_by("subsection")
    )


def get_genre_section(id: int, *, user) -> str:
    """Наименование раздела жанра. Раздел без видимых пользователю книг — 404,
    как несуществующий: иначе перебором номеров видны имена папок и жанров
    чужих закрытых библиотек (несуществующий номер давал 500)."""
    genre = Genre.objects.filter(id=to_int(id)).first()
    if genre is None or not visibility.visible_genres(user).filter(section=genre.section).exists():
        raise Http404("No such genre")
    return genre.section
