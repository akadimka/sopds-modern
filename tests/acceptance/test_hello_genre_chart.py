"""Блок «Жанры» на главной: жанр — только название папки верхнего уровня
библиотеки, не тег FB2 и не записи Genre (баг №166, решение пользователя).

Жанр книги библиотеки берётся из папки верхнего уровня
(`opdsdb.addgenre_from_folder`: «Фантастика», подраздел пустой), жанр из
тега FB2 по genres.xml — «фантастика» с подразделом «Фантастика».
Легенда показывала только подраздел: у половины жанров — пустота, одно и
то же название — двумя кусками (сервер: «фантастика» 7 319 книг +
«Фантастика» 4 456).
"""
import os
from datetime import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from opds_catalog.models import Book, Counter, Genre, bgenre

pytestmark = pytest.mark.django_db


@pytest.fixture
def counter_with_books(db) -> None:
    """Counter с 'allbooks' для работы sopds_processor (как в test_views.py)."""
    Counter.obj.create(name="allbooks", value=0, update_time=timezone.make_aware(datetime(2024, 1, 1)))


def _books(catalog, genre, n, start, folder):
    for i in range(start, start + n):
        b = Book.objects.create(filename=f"b{i}.fb2", path=os.path.join(folder, f"Автор {i}"), format="fb2",
                                cat_type=0, title=f"Книга {i}", search_title=f"КНИГА {i}", catalog=catalog, avail=2)
        bgenre.objects.create(book=b, genre=genre)


def test_genre_name_falls_back_to_section_and_code():
    assert Genre(genre="sf", section="Фантастика", subsection="Космос").name == "Космос"
    assert Genre(genre="Фантастика", section="Фантастика", subsection="").name == "Фантастика"
    assert Genre(genre="xxx", section="", subsection="").name == "xxx"


def test_chart_counts_library_folders_only(client, django_user, counter_with_books, catalog):
    # теги FB2 намеренно «чужие»: блок не должен на них смотреть
    tag = Genre.objects.create(genre="sf_space", section="Фантастика", subsection="Космическая фантастика")
    other = Genre.objects.create(genre="detective", section="Детектив", subsection="Детектив")
    _books(catalog, tag, 3, 0, "Фантастика")
    _books(catalog, other, 2, 10, "Фантастика")
    _books(catalog, tag, 1, 20, "Современная проза")

    django_user.is_superuser = True   # суперпользователь видит все книги (видимость — по владельцам)
    django_user.save()
    client.force_login(django_user)
    response = client.get(reverse("web:main"))

    top = [(g.name, g.cnt) for g in response.context["top_genres"]]
    assert top == [("Фантастика", 5), ("Современная проза", 1)]
    html = response.content.decode()
    assert 'label: "Фантастика", cnt: 5' in html
    assert "Космическая фантастика" not in html.split('id="genreChart"')[1].split("</table>")[0]
