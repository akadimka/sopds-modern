"""Тесты getFileData, get_fs_book_path — integration."""

import os
from pathlib import Path

import pytest

from opds_catalog.models import Book
from opds_catalog.utils import get_fs_book_path, getFileData
from tests.helpers import read_book_from_zip_file, read_file_as_iobytes

pytestmark = [pytest.mark.django_db, pytest.mark.integration]


# ── getFileData ──────────────────────────────────────────────────────────


@pytest.mark.usefixtures("fake_sopds_root_lib")
class TestGetFileData:
    """Тесты getFileData — поиск и чтение файла книги из ФС."""

    def test_read_book_from_regular_file(self, book_factory) -> None:
        from opds_catalog.sopds_config import sopds_cfg as config

        book = book_factory(filename="262001.fb2", cat_type=0, path=".")
        expected = read_file_as_iobytes(
            os.path.join(config.SOPDS_ROOT_LIB, book.filename)
        )
        assert expected is not None

        actual = getFileData(book)
        assert actual is not None
        assert actual.getvalue() == expected.getvalue()

    def test_read_book_from_zip_file(self, book_factory) -> None:
        from opds_catalog.sopds_config import sopds_cfg as config

        book = book_factory(filename="539273.fb2", cat_type=1, path="books.zip")
        expected = read_book_from_zip_file(
            os.path.join(config.SOPDS_ROOT_LIB, book.path), book.filename
        )
        assert expected is not None

        actual = getFileData(book)
        assert actual is not None
        assert actual.getvalue() == expected.getvalue()

    def test_read_book_from_inp_file(self, test_rootlib) -> None:
        expected = read_book_from_zip_file(
            os.path.join(test_rootlib, "books.zip"),
            "539273.fb2",
        )
        assert expected is not None

        book = Book(filename="539273.fb2", cat_type=3, path="inpx/inp/books.zip")
        actual = getFileData(book)
        assert actual is not None
        assert actual.getvalue() == expected.getvalue()

    def test_read_absent_book(self) -> None:
        # Несуще��твующий обычный файл
        book = Book(filename="263001.fb2", cat_type=0, path="data")
        actual = getFileData(book)
        assert actual is None

        # Несуществующий ZIP
        book = Book(filename="539273.fb2", cat_type=1, path="data/books1.zip")
        actual = getFileData(book)
        assert actual is None

        # Несуществующий файл внутри существующего ZIP
        book = Book(filename="559273.fb2", cat_type=1, path="data/books.zip")
        actual = getFileData(book)
        assert actual is None

        # INP — несуществующий архив
        book = Book(filename="539273.fb2", cat_type=3, path="data/inpx/inp/books1.zip")
        actual = getFileData(book)
        assert actual is None

        # INP — несуществующий файл внутри архива
        book = Book(filename="559273.fb2", cat_type=3, path="data/inpx/inp/books.zip")
        actual = getFileData(book)
        assert actual is None


# getFileDataZip была удалена — упаковку в zip теперь делает Download()
# напрямую в opds_catalog/dl.py (см. TestDownloads.test_download_zip в
# tests/acceptance/test_downloads.py — тот же сценарий через реальный HTTP-эндпоинт).


# ── get_fs_book_path ─────────────────────────────────────────────────────


@pytest.mark.override_config(SOPDS_ROOT_LIB="opds_catalog/tests/data/")
class TestGetFsBookPath:
    """Тесты get_fs_book_path — формирование пути в ФС."""

    def test_inp_book_path(self) -> None:
        # CAT_INP разбирает путь на составляющие через os.path.split/join,
        # чтобы вырезать промежуточные inpx/inp-сегменты — из-за этого
        # финальный путь всегда собирается с НАТИВНЫМ разделителем ОС
        # (обратный слэш на Windows), даже если входные строки используют "/".
        book = Book(filename="539273.fb2", cat_type=3, path="inpx/inp/books.zip")
        expected_path = os.path.normpath("opds_catalog/tests/data/books.zip")
        actual_path = get_fs_book_path(book)
        assert os.path.normpath(actual_path) == expected_path

    def test_normal_book_path(self) -> None:
        book = Book(filename="539273.fb2", cat_type=0, path="books.zip")
        expected_path = os.path.join("opds_catalog/tests/data/", "books.zip")
        actual_path = get_fs_book_path(book)
        assert actual_path is not None
        assert actual_path == expected_path
