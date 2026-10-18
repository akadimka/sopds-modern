"""Сжатие библиотеки не пересоздаёт книги каталога OPDS (баг №159).

«книга.fb2 → книга.fb2.zip»: сканер ищет книгу по (имени, пути), а у книги
в zip путь — сам архив. Без правки следующее сканирование заводило новую
запись (рейтинги и полки остаются у старой — их пришлось бы скачивать
заново для всей библиотеки), а старую помечало удалённой.
`compress_catalogs.catalog_updater` переводит запись на архив на месте.
"""
import sqlite3
import zipfile

import pytest

from fb2parser_core.compress_service import compress_library
from fb2parser_core.fb2_utils import compress_fb2_file
from fb2parser_web.compress_catalogs import catalog_updater
from opds_catalog import opdsdb
from opds_catalog.models import Book, SamlibRating
from opds_catalog.sopdscan import opdsScanner

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>sf</genre>
<author><first-name>Оро</first-name><last-name>Призывающий</last-name></author>
<book-title>{title}</book-title><lang>ru</lang></title-info></description>
<body><section><p>Текст книги {title}.</p></section></body>
</FictionBook>
"""


@pytest.mark.django_db
def test_compressed_book_keeps_its_record_and_rating(tmp_path, override_config):
    opdsdb.clear_all()
    folder = tmp_path / "Фантастика" / "Оро Призывающий" / "Маны"
    folder.mkdir(parents=True)
    for n in (1, 2):
        (folder / f"Оро Призывающий - Маны т. {n}.fb2").write_text(_FB2.format(title=f"Маны {n}"), encoding="utf-8")
    db = tmp_path / "cache.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE books (file_path TEXT UNIQUE, author TEXT)")
        conn.execute("INSERT INTO books VALUES (?, ?)",
                     ("Фантастика/Оро Призывающий/Маны/Оро Призывающий - Маны т. 1.fb2", "Оро Призывающий"))

    with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
        opdsScanner().scan_all()
        ids = {b.filename: b.pk for b in Book.objects.filter(avail__gt=0)}
        assert len(ids) == 2
        SamlibRating.objects.create(book_id=ids["Оро Призывающий - Маны т. 1.fb2"], rating=8.5, votes=10)

        update, close = catalog_updater(tmp_path, db_path=db)
        try:
            result = compress_library(tmp_path, on_compressed=update)
        finally:
            close()
        assert result["compressed"] == 2 and not result["errors"]

        scanner = opdsScanner()
        scanner.scan_all()

    books = list(Book.objects.all())
    assert {b.filename: b.pk for b in books} == ids          # те же записи, новых нет
    assert all(b.avail > 0 and b.cat_type == opdsdb.CAT_ZIP and b.path.endswith(".fb2.zip") for b in books)
    assert SamlibRating.objects.get(book_id=ids["Оро Призывающий - Маны т. 1.fb2"]).rating == 8.5
    with sqlite3.connect(db) as conn:
        assert conn.execute("select file_path from books").fetchone()[0].replace("\\", "/") == \
            "Фантастика/Оро Призывающий/Маны/Оро Призывающий - Маны т. 1.fb2.zip"


def test_existing_zip_is_not_overwritten(tmp_path):
    plain = tmp_path / "книга.fb2"
    plain.write_text(_FB2.format(title="Новая"), encoding="utf-8")
    other = tmp_path / "книга.fb2.zip"
    with zipfile.ZipFile(other, "w") as zf:
        zf.writestr("книга.fb2", "другая редакция")
    with pytest.raises(FileExistsError):
        compress_fb2_file(plain)
    assert plain.exists()
    with zipfile.ZipFile(other) as zf:
        assert zf.read("книга.fb2") == "другая редакция".encode()


@pytest.mark.django_db
def test_busy_catalog_database_is_waited_for(tmp_path, override_config, monkeypatch):
    """Баг №161: база каталога занята пересканированием sopds-watch (сервер
    2026-10-16, 24 книги) — обновление записи ждёт и повторяет, а не
    сдаётся: иначе сканер заводит книге новую запись."""
    from django.db.utils import OperationalError

    import fb2parser_web.compress_catalogs as cc

    opdsdb.clear_all()
    folder = tmp_path / "Фантастика" / "Оро Призывающий"
    folder.mkdir(parents=True)
    (folder / "Книга.fb2").write_text(_FB2.format(title="Книга"), encoding="utf-8")
    real = opdsdb.addcattree
    calls = []

    def busy_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OperationalError("database is locked")
        return real(*args, **kwargs)

    monkeypatch.setattr(cc, "LOCK_WAIT_SECONDS", 0)
    with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
        opdsScanner().scan_all()
        [book] = Book.objects.all()
        monkeypatch.setattr(opdsdb, "addcattree", busy_once)
        update, close = catalog_updater(tmp_path, db_path=tmp_path / "нет.db")
        try:
            result = compress_library(tmp_path, on_compressed=update)
        finally:
            close()

    assert not result["errors"] and len(calls) >= 2   # первая попытка — «locked», повтор прошёл
    book.refresh_from_db()
    assert book.cat_type == opdsdb.CAT_ZIP and book.path.endswith("Книга.fb2.zip")
