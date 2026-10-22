"""Книги на сайте и в боте показываются по имени файла — «Автор - Серия N.
Название», «Автор - Название (Трилогия)» (такое имя даёт синхронизация
fb2parser), а не по названию из метаданных FB2, у каждого издателя своему.
Поиск находит книгу и по названию, и по имени файла; в OPDS — название, как
прежде (читалки показывают автора отдельным полем)."""
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from opds_catalog.models import Author, Book, Catalog, bauthor, book_display_name
from sopds_web_backend import telegram_search as ts
from tests.fixtures.fixture_django import publish_books

pytestmark = pytest.mark.django_db

FILE = "Роберт Сойер - Триггеры.fb2"
SHOWN = "Роберт Сойер - Триггеры"
META = "Сойер. Триггеры"


@pytest.mark.parametrize("filename, expected", [
    ("Роберт Сойер - Триггеры.fb2", "Роберт Сойер - Триггеры"),
    ("Роберт Сойер - Триггеры.fb2.zip", "Роберт Сойер - Триггеры"),
    ("Фантастика/Сойер/Роберт Сойер - Триггеры.FB2", "Роберт Сойер - Триггеры"),
    ("Белянин Андрей - Мечом и смехом. Лев Голицын 1-3.fb2", "Белянин Андрей - Мечом и смехом. Лев Голицын 1-3"),
    ("Автор - Том 1.5.epub", "Автор - Том 1.5"),
    ("8. Алхимик. Том XI-XII", "8. Алхимик. Том XI-XII"),
    ("", "Название из метаданных"),
])
def test_display_name_is_file_name_without_extension(filename, expected):
    assert book_display_name(filename, "Название из метаданных") == expected


@pytest.fixture
def book(settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    cat = Catalog.objects.create(cat_name=".", path=".")
    b = Book.objects.create(filename=FILE, path=".", catalog=cat, avail=2, format="fb2", docdate="", lang="ru",
                            title=META, search_title=META.upper(), annotation="", lang_code=1)
    name = "Сойер Роберт"
    bauthor.objects.create(book=b, author=Author.objects.create(full_name=name, search_full_name=name.upper(),
                                                                lang_code=1))
    publish_books(b)
    return b


@pytest.fixture
def client_in(book):
    client = Client()
    client.force_login(User.objects.create_user("reader"))
    return client


def test_search_field_filled_on_save(book):
    assert book.search_filename == SHOWN.upper()
    Book.objects.filter(pk=book.pk).update(search_filename="")
    book.refresh_from_db()
    book.filename = "Сойер Роберт - Триггеры.fb2"
    book.save(update_fields=["filename"])
    book.refresh_from_db()
    assert book.search_filename == "СОЙЕР РОБЕРТ - ТРИГГЕРЫ"


@pytest.mark.parametrize("query", ["сойер триггеры", "Роберт Сойер - Триггеры", "СОЙЕР. ТРИГГЕРЫ"])
def test_site_search_finds_by_file_name_or_title_and_shows_file_name(client_in, query):
    html = client_in.get(reverse("web:searchbooks"), {"searchtype": "m", "searchterms": query}).content.decode()
    assert SHOWN in html and META not in html


def test_book_card_shows_file_name_and_keeps_author_link(client_in, book):
    html = client_in.get(reverse("web:book_card", args=(book.pk,))).content.decode()
    assert f'<div class="sopds-card-title">{SHOWN}</div>' in html
    assert "Сойер Роберт</a>" in html                       # ссылка на книги автора — на месте


def test_suggestions_show_and_link_file_name(client_in):
    html = client_in.get(reverse("web:suggest"), {"searchterms": "роберт сой", "type": "title"}).content.decode()
    assert f">{SHOWN}</a>" in html
    assert "searchterms=%D0%A0%D0%BE%D0%B1%D0%B5%D1%80%D1%82" in html          # ссылка — по имени «Роберт …»


def test_main_page_lists_file_name(client_in):
    assert SHOWN in client_in.get(reverse("web:main")).content.decode()


def test_opds_keeps_metadata_title(book, override_config):
    with override_config(SOPDS_AUTH=False):
        feed = Client().get(reverse("opds:searchbooks", kwargs={"searchtype": "m", "searchterms": "сойер триггеры"}))
    xml = feed.content.decode()
    assert f"<title>{META}</title>" in xml and f"<title>{SHOWN}</title>" not in xml   # имя файла — лишь в описании


def test_telegram_search_finds_by_file_name_and_shows_it(book):
    user = User.objects.create_user("reader")
    items = ts.search("роберт сойер триггеры", user)
    assert ("b", book.pk, f"📖 {SHOWN}") in items
