"""Соавторская серия уже лежит у пары соавторов — книга, пришедшая под одним
из них, идёт туда же, а не в отдельную папку одиночного автора (2026-11-06).

В Фантастике 34 книги лежали дважды: у «Аржанов Алексей, Молотов Виктор» и у
«Аржанов Алексей». Почти все — синхронизация с ПК: проверки дублей смотрят в
БД синхронизации своей машины, а у ПК в ней нет книг, положенных сервером.
Теперь синхронизация смотрит в саму библиотеку (папки), БД ей не нужна.
"""
import pytest

from fb2parser_core.fb2_utils import compress_fb2_file, read_fb2_bytes
from tests.integration.fb2parser_core.test_ownership_in_operations import (  # noqa: F401
    _book,
    _sync,
    lib,
    users,
)

pytestmark = pytest.mark.django_db

PAIR = "Иванов Иван, Петров Петр"


def _pair_book_in_library(lib, extra=""):
    """Книга серии у пары соавторов — как её положил сервер (в БД этой машины её нет)."""
    folder = lib / "Фантастика" / PAIR / "Звезды"
    plain = _book(folder / f"{PAIR} - Звезды. 1 т. 1.fb2", 1, extra=extra)
    compress_fb2_file(plain)
    return folder / f"{plain.name}.zip"


def test_copy_under_one_coauthor_is_a_duplicate(tmp_path, lib, users):
    placed = _pair_book_in_library(lib)
    scan = tmp_path / "in"
    incoming = _book(scan / "Иванов Иван" / "Звезды" / "1. Звезды 1.fb2", 1)
    _sync(tmp_path, lib, scan).synchronize(allowed_folders={str(scan / "Иванов Иван")})
    assert not (lib / "Фантастика" / "Иванов Иван").exists()            # папки одиночного автора нет
    assert list(lib.rglob("*.fb2*")) == [placed]
    assert not incoming.exists()


def test_fuller_version_under_one_coauthor_replaces_the_pair_copy(tmp_path, lib, users):
    placed = _pair_book_in_library(lib)
    scan = tmp_path / "in"
    _book(scan / "Иванов Иван" / "Звезды" / "1. Звезды 1.fb2", 1, extra=" Дополнено." * 50)
    _sync(tmp_path, lib, scan).synchronize(allowed_folders={str(scan / "Иванов Иван")})
    assert not (lib / "Фантастика" / "Иванов Иван").exists()
    assert list(lib.rglob("*.fb2*")) == [placed]
    assert "Дополнено." in read_fb2_bytes(placed).decode("utf-8")


def test_own_series_folder_wins_over_the_pair(tmp_path, lib, users):
    """У самого автора эта серия уже есть — кладём к нему (ничего не меняется)."""
    _pair_book_in_library(lib)
    own = lib / "Фантастика" / "Иванов Иван" / "Звезды"
    own.mkdir(parents=True)
    scan = tmp_path / "in"
    _book(scan / "Иванов Иван" / "Звезды" / "2. Звезды 2.fb2", 2)
    _sync(tmp_path, lib, scan).synchronize(allowed_folders={str(scan / "Иванов Иван")})
    assert len(list(own.glob("*.fb2.zip"))) == 1
