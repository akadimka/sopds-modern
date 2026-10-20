"""Владение и сканер / сжатие / сверка (docs/multiuser-plan.md шаг 1.3):
новая запись каталога получает владельцев своего файла (без владельцев —
суперпользователь), сжатие переносит владельцев на архив, команда сверки
чинит расхождения с диском и каталогом."""
import sqlite3

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command

from fb2parser_core.compress_service import compress_library
from fb2parser_web.compress_catalogs import catalog_updater
from opds_catalog import opdsdb, ownership
from opds_catalog.models import Book, BookOwner, LibraryFileOwner
from opds_catalog.sopdscan import opdsScanner

pytestmark = pytest.mark.django_db

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>sf</genre>
<author><first-name>Иван</first-name><last-name>Иванов</last-name></author>
<book-title>{title}</book-title><lang>ru</lang></title-info></description>
<body><section><p>Текст книги {title}.</p></section></body>
</FictionBook>
"""


@pytest.fixture
def lib(tmp_path, override_config):
    opdsdb.clear_all()
    with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def users():
    return User.objects.create_user("admin", is_superuser=True), User.objects.create_user("anna")


def _write(lib, rel, title):
    p = lib.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_FB2.format(title=title), encoding="utf-8")
    return p


def _book_owners(filename):
    return set(BookOwner.objects.filter(book__filename=filename).values_list("user__username", flat=True))


def _file_owners(rel):
    return set(LibraryFileOwner.objects.filter(rel_path=rel).values_list("user__username", flat=True))


def test_scanned_book_without_owner_goes_to_admin(lib, users):
    _write(lib, "Фантастика/Иванов Иван/Звёзды.fb2", "Звёзды")
    opdsScanner().scan_all()
    assert _book_owners("Звёзды.fb2") == {"admin"}
    assert _file_owners("Фантастика/Иванов Иван/Звёзды.fb2") == {"admin"}


def test_scanned_book_gets_owners_of_its_file(lib, users):
    _admin, anna = users
    _write(lib, "Фантастика/Иванов Иван/Звёзды.fb2", "Звёзды")
    ownership.assign("Фантастика/Иванов Иван/Звёзды.fb2", anna, source="upload")
    opdsScanner().scan_all()
    assert _book_owners("Звёзды.fb2") == {"anna"}


def test_compression_moves_owners_to_archive(lib, users):
    _admin, anna = users
    rel = "Фантастика/Иванов Иван/Звёзды.fb2"
    _write(lib, rel, "Звёзды")
    ownership.assign(rel, anna)
    opdsScanner().scan_all()
    db = lib / "cache.db"
    sqlite3.connect(db).execute("CREATE TABLE books (file_path TEXT UNIQUE, author TEXT)").connection.commit()
    update, close = catalog_updater(lib, db_path=db)
    try:
        assert compress_library(lib, on_compressed=update)["compressed"] == 1
    finally:
        close()
    opdsScanner().scan_all()
    assert _file_owners(rel) == set() and _file_owners(rel + ".zip") == {"anna"}
    [book] = Book.objects.filter(avail__gt=0)
    assert set(book.owners.values_list("user__username", flat=True)) == {"anna"}


def test_reconcile_report_only_changes_nothing(lib, users):
    _write(lib, "Жанр/Автор/a.fb2", "A")
    report = ownership.reconcile()
    assert report["to_admin"] == ["Жанр/Автор/a.fb2"] and not report["applied"]
    assert not LibraryFileOwner.objects.exists()


def test_reconcile_applies(lib, users, tmp_path_factory):
    _admin, anna = users
    _write(lib, "Жанр/Автор/без владельца.fb2", "Без владельца")
    moved = _write(lib, "Жанр/Автор/Серия/переехала.fb2", "Переехала")
    ownership.assign("Жанр/Автор/переехала.fb2", anna)    # путь до перемещения в обход учёта
    ownership.assign("Жанр/Автор/удалена.fb2", anna)      # файла больше нет
    memory = tmp_path_factory.mktemp("mem") / "library_memory.db"
    with sqlite3.connect(memory) as conn:
        conn.execute("CREATE TABLE books (path TEXT PRIMARY KEY, fingerprint TEXT)")
        conn.executemany("INSERT INTO books VALUES (?, ?)", [
            ("Жанр/Автор/переехала.fb2", "fp-moved"), ("Жанр/Автор/Серия/переехала.fb2", "fp-moved")])
    opdsdb.addbook(moved.name, "Жанр\\Автор\\Серия", opdsdb.addcattree("Жанр\\Автор\\Серия", 0), ".fb2",
                   "Переехала", "", "", "ru")
    BookOwner.objects.all().delete()   # каталог разошёлся с владением

    report = ownership.reconcile(apply=True, memory_db=str(memory))

    assert report["relinked"] == {"Жанр/Автор/переехала.fb2": "Жанр/Автор/Серия/переехала.fb2"}
    assert _file_owners("Жанр/Автор/Серия/переехала.fb2") == {"anna"}
    assert _file_owners("Жанр/Автор/без владельца.fb2") == {"admin"}
    assert _file_owners("Жанр/Автор/удалена.fb2") == set()
    assert _book_owners("переехала.fb2") == {"anna"}   # автоматический суперпользователь снят


def test_reconcile_repairs_catalog_links(lib, users):
    admin, anna = users
    _write(lib, "Жанр/Автор/a.fb2", "A")
    ownership.assign("Жанр/Автор/a.fb2", anna)
    opdsScanner().scan_all()
    book = Book.objects.get(filename="a.fb2")
    BookOwner.objects.filter(book=book).delete()
    BookOwner.objects.create(book=book, user=admin)  # лишняя связь
    report = ownership.reconcile(apply=True)
    assert (report["book_links_added"], report["book_links_removed"]) == (1, 1)
    assert _book_owners("a.fb2") == {"anna"}


def test_reconcile_guard_when_library_mostly_missing(lib, users, settings):
    _admin, anna = users
    LibraryFileOwner.objects.bulk_create(
        [LibraryFileOwner(rel_path=f"Жанр/{i}.fb2", user=anna) for i in range(150)])
    report = ownership.reconcile(apply=True)
    assert report["guard"] and not report["applied"]
    assert LibraryFileOwner.objects.count() == 150


def test_command_reports(lib, users, capsys):
    _write(lib, "Жанр/Автор/a.fb2", "A")
    call_command("library_owners_check")
    out = capsys.readouterr().out
    assert "Files without owner (-> administrator): 1" in out and "Report only" in out
    assert not LibraryFileOwner.objects.exists()
