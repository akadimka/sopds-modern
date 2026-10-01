"""Тесты getFileName — только расчёт имени файла для скачивания.

Имя не транслитерируется: кириллица сохраняется (d55819c), а для старых
клиентов dl.py добавляет ASCII-вариант в Content-Disposition (RFC 5987).
"""

import pytest

from opds_catalog.utils import getFileName

pytestmark = pytest.mark.django_db


@pytest.fixture
def _book(book_factory):
    """Создаёт книгу в памяти (без сохранения в БД)."""
    return book_factory(title="Книга", format="fb2", filename="123abc.zip")


@pytest.mark.override_config(SOPDS_TITLE_AS_FILENAME=False)
def test_by_filename(_book) -> None:
    """Имя файла из filename, если SOPDS_TITLE_AS_FILENAME=False."""
    expected = _book.filename
    result = getFileName(_book)
    assert result == expected


@pytest.mark.override_config(SOPDS_TITLE_AS_FILENAME=True)
def test_by_title(_book) -> None:
    """Имя файла из title, если SOPDS_TITLE_AS_FILENAME=True."""
    expected = "Книга.fb2"
    result = getFileName(_book)
    assert result == expected


@pytest.mark.override_config(SOPDS_TITLE_AS_FILENAME=False)
def test_by_russian_filename(book_factory) -> None:
    """Кириллическое имя файла сохраняется как есть."""
    book = book_factory(title="Книга", format="fb2", filename="Книга.zip")
    expected = "Книга.zip"
    result = getFileName(book)
    assert result == expected
