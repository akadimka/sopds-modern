"""Изоляция библиотек в OPDS (docs/multiuser-plan.md шаг 1.6): чужая книга
не отдаётся (404, как несуществующая) и не видна в фидах — ни сама, ни
имя её автора, серии, папки, ни в счётчиках; открытая мне — видна;
администратор видит всё. Объект фида общий для всех запросов — состояние
запроса в нём не хранится."""
import pytest
from django.contrib.auth.models import User
from django.urls import resolve, reverse

from opds_catalog.models import (
    Author,
    Book,
    BookOwner,
    Catalog,
    Genre,
    LibraryShare,
    Series,
    bauthor,
    bgenre,
    bseries,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def lib(override_config, test_rootlib, simple_fb2):
    with override_config(SOPDS_AUTH=True, SOPDS_ROOT_LIB=test_rootlib, SOPDS_DOUBLES_HIDE=False):
        users = {n: User.objects.create_user(n, password="x") for n in ("anna", "boris")}
        users["admin"] = User.objects.create_user("admin", password="x", is_superuser=True)
        root = Catalog.objects.create(cat_name=".", path=".")
        boris_cat = Catalog.objects.create(cat_name="Тайник Бориса", path="Тайник Бориса", parent=root)
        genre = Genre.objects.create(genre="sf", section="Фантастика", subsection="Космос")
        books = {}
        for who, title, cat in (("anna", "Дневник Анны", root), ("boris", "Секрет Бориса", boris_cat)):
            b = Book.objects.create(filename=simple_fb2, path=".", catalog=cat, avail=2, format="fb2",
                                    docdate="", lang="ru", title=title, search_title=title.upper(),
                                    annotation="", lang_code=1)
            name = f"Автор {title.split()[1]}"
            bauthor.objects.create(book=b, author=Author.objects.create(
                full_name=name, search_full_name=name.upper(), lang_code=1))
            bseries.objects.create(book=b, ser=Series.objects.create(
                ser=f"Серия {title}", search_ser=f"СЕРИЯ {title}".upper(), lang_code=1))
            bgenre.objects.create(book=b, genre=genre)
            BookOwner.objects.create(book=b, user=users[who])
            books[who] = b
        yield users, books, boris_cat


def _as(client, user):
    client.force_login(user)
    return client


def _book_urls(book_id):
    return [reverse("opds:download", args=(book_id, 0)), reverse("opds:download", args=(book_id, 1)),
            reverse("opds:cover", args=(book_id,)), reverse("opds:thumb", args=(book_id,)),
            reverse("opds:view_html", args=(book_id,)), reverse("opds:convert", args=(book_id, "epub"))]


def test_foreign_book_is_not_served(client, lib):
    users, books, _ = lib
    c = _as(client, users["anna"])
    for url in _book_urls(books["boris"].pk):
        assert c.get(url).status_code == 404, url
    assert c.post(reverse("opds:save_progress", args=(books["boris"].pk,)), "{}",
                  content_type="application/json").status_code == 404
    assert c.get(reverse("opds:download", args=(books["anna"].pk, 0))).status_code == 200
    assert c.get(reverse("opds:download", args=(10 ** 9, 0))).status_code == 404   # было 500


def test_shared_and_admin_see_the_book(client, lib):
    users, books, _ = lib
    LibraryShare.objects.create(owner=users["boris"], grantee=users["anna"])
    assert _as(client, users["anna"]).get(reverse("opds:download", args=(books["boris"].pk, 0))).status_code == 200
    assert _as(client, users["admin"]).get(reverse("opds:download", args=(books["boris"].pk, 0))).status_code == 200


def _feed(client, user, name, **kwargs):
    response = _as(client, user).get(reverse(f"opds:{name}", kwargs=kwargs))
    assert response.status_code == 200
    return response.content.decode("utf-8")


@pytest.mark.parametrize("name, kwargs", [
    ("searchbooks", {"searchtype": "m", "searchterms": "Е"}),
    ("searchauthors", {"searchtype": "b", "searchterms": "АВТОР"}),
    ("searchseries", {"searchtype": "b", "searchterms": "СЕРИЯ"}),
    ("catalogs", {}),
])
def test_feeds_show_only_visible(client, lib, name, kwargs):
    users, _, _ = lib
    anna = _feed(client, users["anna"], name, **kwargs)
    admin = _feed(client, users["admin"], name, **kwargs)
    assert "Бориса" not in anna
    if name in ("searchbooks", "searchauthors", "searchseries"):
        assert "Анны" in anna and "Бориса" in admin


@pytest.mark.parametrize("name, anna, admin", [
    ("char_books", ["<title>Д</title>"], ["<title>Д</title>", "<title>С</title>"]),   # Дневник / Секрет
    ("char_authors", ["Found: 1 authors"], ["Found: 2 authors"]),                     # оба на «А»
    ("char_series", ["Found: 1 series"], ["Found: 2 series"]),                        # оба на «С»
])
def test_first_letter_menus_count_only_visible(client, lib, name, anna, admin):
    users, _, _ = lib
    anna_feed, admin_feed = (_feed(client, users[u], name, lang_code=1) for u in ("anna", "admin"))
    assert all(x in anna_feed for x in anna) and all(x in admin_feed for x in admin)
    assert "<title>С</title>" not in anna_feed if name == "char_books" else "Found: 2" not in anna_feed


def test_genre_and_main_counters_are_per_user(client, lib):
    users, _, _ = lib
    assert "Found: 1 books" in _feed(client, users["anna"], "genres")
    assert "Found: 2 books" in _feed(client, users["admin"], "genres")
    main = _feed(client, users["anna"], "main")
    assert "Books: 1." in main and "Authors: 1." in main


def test_foreign_catalog_folder_is_not_shown(client, lib):
    users, _, boris_cat = lib
    feed = _feed(client, users["anna"], "cat_tree", cat_id=boris_cat.pk)
    assert "Тайник Бориса" not in feed and "Секрет Бориса" not in feed
    assert "Тайник Бориса" in _feed(client, users["admin"], "cat_tree", cat_id=boris_cat.pk)


def test_author_series_page_hides_foreign_series(client, lib):
    users, books, _ = lib
    boris_author = books["boris"].authors.get()
    feed = _feed(client, users["anna"], "searchseries", searchtype="a", searchterms=boris_author.pk)
    assert "Секрет Бориса" not in feed


def test_feed_object_keeps_no_request_state(client, lib):
    users, _, _ = lib
    _feed(client, users["anna"], "main")
    _feed(client, users["anna"], "char_books", lang_code=1)
    for url in (reverse("opds:main"), reverse("opds:char_books", kwargs={"lang_code": 1}),
                reverse("opds:genres"), reverse("opds:searchseries", kwargs={"searchtype": "b", "searchterms": "С"})):
        feed = resolve(url).func
        assert not {"request", "lang_code", "section_id", "author_id"} & set(vars(feed)), url
