"""Имя файла при скачивании (opds_catalog/utils.getFileName): название книги
(SOPDS_TITLE_AS_FILENAME) — из FB2, имя книги из архива — сырое имя записи
ZIP. «../../x» (и то же с обратной косой чертой) попадало в zip-архив,
который сайт отдаёт читалке (наивная распаковка писала бы выше целевой
папки), а кавычка — в заголовок Content-Disposition."""
import io
import zipfile

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from opds_catalog import dl
from opds_catalog.models import Book, Catalog
from tests.fixtures.fixture_django import publish_books

pytestmark = pytest.mark.django_db

_FB2 = b'<?xml version="1.0" encoding="utf-8"?><FictionBook><body><p>x</p></body></FictionBook>'


def _book(monkeypatch, filename, title):
    cat = Catalog.objects.create(parent=None, cat_name=".", path=".", cat_type=0)
    book = Book.objects.create(filename=filename, path=".", filesize=len(_FB2), format="fb2", cat_type=0,
                               docdate="2020", lang="ru", title=title, search_title=title.upper(),
                               annotation="", avail=2, catalog=cat)
    publish_books(book)
    monkeypatch.setattr(dl, "getFileData", lambda b: io.BytesIO(_FB2))
    return book


def _download(book, zipped):
    client = Client()
    client.force_login(User.objects.get_or_create(username="root", is_superuser=True, is_staff=True)[0])
    return client.get(reverse("opds:download", args=[book.pk, 1 if zipped else 0]))


def _members(response):
    return zipfile.ZipFile(io.BytesIO(response.content)).namelist()


@pytest.mark.parametrize("title", ["../../AppData/evil", "..\\..\\evil", "/etc/evil", "C:\\Windows\\evil"])
def test_title_as_filename_has_no_directories(monkeypatch, override_config, title):
    book = _book(monkeypatch, "Автор - Книга.fb2", title)
    with override_config(SOPDS_TITLE_AS_FILENAME=True):
        response = _download(book, zipped=True)
    assert _members(response) == ["evil.fb2"]
    assert 'filename="evil.fb2.zip"' in response["Content-Disposition"]


def test_archive_entry_name_has_no_directories(monkeypatch):
    book = _book(monkeypatch, "..\\../evil.fb2", "Книга")
    assert _members(_download(book, zipped=True)) == ["evil.fb2"]


def test_quote_does_not_break_the_header(monkeypatch, override_config):
    book = _book(monkeypatch, "Автор - Книга.fb2", 'Он сказал "да"')
    with override_config(SOPDS_TITLE_AS_FILENAME=True):
        header = _download(book, zipped=False)["Content-Disposition"]
    ascii_name = header.split('filename="', 1)[1].split('";', 1)[0]
    assert '"' not in ascii_name


def test_name_of_only_dots_falls_back_to_book_id(monkeypatch):
    book = _book(monkeypatch, "..", "x")
    assert _members(_download(book, zipped=True)) == [f"book{book.pk}.fb2"]
