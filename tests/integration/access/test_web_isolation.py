"""Изоляция библиотек на сайте /web/ (docs/multiuser-plan.md шаг 1.7):
поиск (в т.ч. полка, дубли, книга по id, крошки автора и серии), подсказки,
карточка книги, папки, главная страница и данные на каждой странице
(счётчики, случайная книга, полка) — только видимые книги."""
import re

import pytest
from django.urls import reverse

from opds_catalog.models import Book, LibraryShare, bookshelf

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


def test_catalog_folder_row_counts_visible_books(client, lib):
    """Папка в «Каталогах» — строкой, как раздел «Жанров»: название и число
    книг; число — только по видимым книгам."""
    users, _, _ = lib
    _, admin = _page(client, users["admin"], "catalog")
    assert "Тайник Бориса" in admin and '<span class="sopdsitem-count">(1)</span>' in admin
    LibraryShare.objects.create(owner=users["boris"], grantee=users["anna"])
    _, anna = _page(client, users["anna"], "catalog")
    assert "Тайник Бориса" in anna and '<span class="sopdsitem-count">(1)</span>' in anna


def test_main_page_books_block_is_a_link(client, lib):
    """«Книги» на главной — переход на страницу выбора, как «Авторы», а не
    всплывающее окно."""
    users, _, _ = lib
    _, main = _page(client, users["anna"], "main")
    assert f'href="{reverse("web:book")}"' in main
    assert "BooksPickerModal" not in main


def test_main_page_and_every_page_context(client, lib):
    users, _, _ = lib
    for _ in range(5):   # случайная книга — тоже только из видимых
        status, main = _page(client, users["anna"], "main")
        assert status == 200 and "Бориса" not in main
    # Блок «Жанры» — по папкам библиотеки (баг №166): обе книги — в одной
    # папке-жанре, Анна видит в нём только свою.
    Book.objects.update(path="Фантастика")
    client.force_login(users["anna"])
    response = client.get(reverse("web:main"))
    assert response.context["stats"]["allbooks"] == 1
    assert response.context["random_book"].title == "Дневник Анны"
    assert [(g.name, g.cnt) for g in response.context["chart_genres"]] == [("Фантастика", 1)]


def test_news_shows_only_visible_books(client, lib):
    """«Новости»: книги дня, счёт, ссылки «за день» — только видимые (чужая
    книга того же дня не видна и не считается)."""
    from django.utils import timezone

    users, _, _ = lib
    for name in ("main", "news"):
        _, anna = _page(client, users["anna"], name)
        assert "Дневник Анны" in anna and "Бориса" not in anna, name
        _, admin = _page(client, users["admin"], name)
        assert "Дневник Анны" in admin and "Секрет Бориса" in admin, name
    client.force_login(users["anna"])
    item = client.get(reverse("web:main")).context["news_items"][0]
    assert item.total == 1 and [b.title for b in item.books] == ["Автор Анны - Дневник Анны"]
    day = {"searchtype": "n", "searchterms": timezone.localdate().isoformat()}
    _, anna = _page(client, users["anna"], "searchbooks", **day)
    assert "Дневник Анны" in anna and "Бориса" not in anna


@pytest.mark.parametrize("name, params", [
    ("searchauthors", {"searchtype": "b", "searchterms": "АВТОР"}),
    ("searchseries", {"searchtype": "b", "searchterms": "СЕРИЯ"}),
])
def test_author_and_series_search(client, lib, name, params):
    users, _, _ = lib
    _, anna = _page(client, users["anna"], name, **params)
    _, admin = _page(client, users["admin"], name, **params)
    assert "Бориса" not in anna and "Анны" in anna and "Бориса" in admin


def _context_items(client, user, name, **params):
    client.force_login(user)
    response = client.get(reverse(f"web:{name}"), params)
    assert response.status_code == 200
    return list(response.context["items"])


@pytest.mark.parametrize("name, params, count_key", [
    ("book", {"lang": 1}, "cnt"),
    ("author", {"lang": 1}, "cnt"),
    ("series", {"lang": 1}, "cnt"),
    ("genre", {}, "num_book"),
])
def test_menus_count_only_visible(client, lib, name, params, count_key):
    users, _, _ = lib
    total = lambda u: sum(i[count_key] for i in _context_items(client, users[u], name, **params))  # noqa: E731
    assert (total("anna"), total("admin")) == (1, 2)


def test_rating_progress_admin_only(client, lib):
    """Прогресс загрузки рейтингов называет книгу, по которой идёт загрузка, —
    из любой библиотеки: только суперпользователю."""
    from django.core.cache import cache

    from opds_catalog.ratings_progress import _key
    users, _, _ = lib
    cache.set(_key("samlib"), {"status": "running", "book_title": "Секрет Бориса"})
    url = reverse("web:ratings_progress", args=("samlib",))
    client.force_login(users["anna"])
    response = client.get(url)
    assert response.status_code != 200 and "Секрет Бориса" not in response.content.decode("utf-8")
    client.force_login(users["admin"])
    assert client.get(url).json()["book_title"] == "Секрет Бориса"
    _, main = _page(client, users["anna"], "main")
    assert 'id="ratings-live-' not in main


def test_reader_in_app(client, lib):
    """Читалка внутри приложения (/web/read/<id>/, scope PWA): чужая книга —
    404, своя — страница читалки с кнопкой «к библиотеке»; без входа —
    страница входа сайта, а не запрос пароля OPDS."""
    users, books, _ = lib
    url = reverse("web:read", args=(books["anna"].pk,))
    anonymous = client.get(url)
    assert anonymous.status_code == 302 and reverse("web:login") in anonymous["Location"]
    client.force_login(users["anna"])
    assert client.get(reverse("web:read", args=(books["boris"].pk,))).status_code == 404
    page = client.get(url)
    assert page.status_code == 200
    html = page.content.decode("utf-8")
    assert f'href="{reverse("web:main")}" data-act="back"' in html
    assert reverse("opds:save_progress", args=(books["anna"].pk,)) in html
    assert 'data-act="offline"' in html and f"var USER = {users['anna'].pk};" in html


def test_offline_page_lists_saved_books_without_server_data(client):
    """Страница «нет связи» (её отдаёт service worker без сети) — общая для
    всех: список сохранённых книг строится в браузере, сервер в неё данных
    каталога не кладёт."""
    page = client.get(reverse("web:offline"))
    assert page.status_code == 200
    html = page.content.decode("utf-8")
    assert 'id="offline-books"' in html and "sopds_offline_books" in html


def test_genre_section_without_subsections_opens_books(client, lib):
    """Жанр по папке библиотеки (поджанра нет): «Жанры» → раздел — сразу
    список книг, а не страница из одной безымянной строки с числом книг;
    в крошках — без пустого звена."""
    from opds_catalog.models import Genre, bgenre
    users, books, _ = lib
    folder_genre = Genre.objects.create(genre="Без жанра", section="Без жанра", subsection="")
    bgenre.objects.filter(book=books["anna"]).update(genre=folder_genre)
    client.force_login(users["anna"])
    _, genres = _page(client, users["anna"], "genre")
    assert re.search(r'Без жанра</span>\s*<span class="sopdsitem-count">\(1\)</span>', genres)
    response = client.get(reverse("web:genre"), {"section": folder_genre.pk})
    assert response.status_code == 302 and f"searchterms={folder_genre.pk}" in response.url
    books_page = client.get(response.url)
    assert "Дневник Анны" in books_page.content.decode()
    assert [c["label"] for c in books_page.context["breadcrumbs"]][-2:] == ["Genres", "Без жанра"]


def test_genre_section_with_subsections_lists_them(client, lib):
    users, _, _ = lib
    from opds_catalog.models import Genre
    section_id = Genre.objects.get(genre="sf").pk
    _, html = _page(client, users["admin"], "genre", section=section_id)
    assert '<span class="sopdsitem-key">Космос</span>' in html
