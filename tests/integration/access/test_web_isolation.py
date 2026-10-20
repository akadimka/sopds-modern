"""Изоляция библиотек на сайте /web/ (docs/multiuser-plan.md шаг 1.7):
поиск (в т.ч. полка, дубли, книга по id, крошки автора и серии), подсказки,
карточка книги, папки, главная страница и данные на каждой странице
(счётчики, случайная книга, полка) — только видимые книги."""
import pytest
from django.urls import reverse

from opds_catalog.models import LibraryShare, bookshelf

pytestmark = pytest.mark.django_db


def _page(client, user, name, **params):
    client.force_login(user)
    response = client.get(reverse(f"web:{name}"), params)
    return response.status_code, response.content.decode("utf-8")


@pytest.mark.parametrize("params", [
    {"searchtype": "m", "searchterms": "Е"},
    {"searchtype": "b", "searchterms": "С"},
])
def test_search_shows_only_visible(client, lib, params):
    users, _, _ = lib
    status, anna = _page(client, users["anna"], "searchbooks", **params)
    assert status == 200 and "Секрет Бориса" not in anna
    _, admin = _page(client, users["admin"], "searchbooks", **params)
    assert "Секрет Бориса" in admin


def test_own_books_found_on_web(client, lib):
    """Поиск получал имя пользователя вместо пользователя — свои книги
    (в закрытой библиотеке) не находились бы."""
    users, _, _ = lib
    _, anna = _page(client, users["anna"], "searchbooks", searchtype="m", searchterms="ДНЕВНИК")
    assert "Дневник Анны" in anna


def test_foreign_ids_in_search_reveal_nothing(client, lib):
    users, books, _ = lib
    boris = books["boris"]
    author, series = boris.authors.get(), boris.series.get()
    for params in ({"searchtype": "i", "searchterms": boris.pk},
                   {"searchtype": "a", "searchterms": author.pk},
                   {"searchtype": "s", "searchterms": series.pk}):
        status, html = _page(client, users["anna"], "searchbooks", **params)
        assert status == 200 and "Бориса" not in html, params
    status, _ = _page(client, users["anna"], "searchbooks", searchtype="d", searchterms=boris.pk)
    assert status == 404


def test_bookshelf_hides_revoked_books(client, lib):
    users, books, _ = lib
    share = LibraryShare.objects.create(owner=users["boris"], grantee=users["anna"])
    bookshelf.objects.create(user=users["anna"], book=books["boris"])
    _, html = _page(client, users["anna"], "searchbooks", searchtype="u", searchterms="0")
    assert "Секрет Бориса" in html
    share.delete()
    _, html = _page(client, users["anna"], "searchbooks", searchtype="u", searchterms="0")
    assert "Секрет Бориса" not in html
    _, main = _page(client, users["anna"], "main")              # полка в боковой панели
    assert "Секрет Бориса" not in main
    assert bookshelf.objects.filter(user=users["anna"], book=books["boris"]).exists()   # запись осталась


@pytest.mark.parametrize("kind, term", [("title", "СЕ"), ("author", "АВТОР"), ("series", "СЕРИЯ")])
def test_suggestions(client, lib, kind, term):
    users, _, _ = lib
    _, html = _page(client, users["anna"], "suggest", searchterms=term, type=kind)
    assert "Бориса" not in html
    _, admin = _page(client, users["admin"], "suggest", searchterms=term, type=kind)
    assert "Бориса" in admin


def test_book_card(client, lib):
    users, books, _ = lib
    client.force_login(users["anna"])
    assert client.get(reverse("web:book_card", args=(books["boris"].pk,))).status_code == 404
    assert client.get(reverse("web:book_card", args=(books["anna"].pk,))).status_code == 200


def test_catalog_folder(client, lib):
    users, _, boris_cat = lib
    _, html = _page(client, users["anna"], "catalog", cat=boris_cat.pk)
    assert "Тайник Бориса" not in html and "Секрет Бориса" not in html
    _, root = _page(client, users["anna"], "catalog")
    assert "Тайник Бориса" not in root
    _, admin = _page(client, users["admin"], "catalog", cat=boris_cat.pk)
    assert "Секрет Бориса" in admin


def test_main_page_and_every_page_context(client, lib):
    users, _, _ = lib
    for _ in range(5):   # случайная книга — тоже только из видимых
        status, main = _page(client, users["anna"], "main")
        assert status == 200 and "Бориса" not in main
    client.force_login(users["anna"])
    response = client.get(reverse("web:main"))
    assert response.context["stats"]["allbooks"] == 1
    assert response.context["random_book"].title == "Дневник Анны"
    assert [g.cnt for g in response.context["chart_genres"]] == [1]
