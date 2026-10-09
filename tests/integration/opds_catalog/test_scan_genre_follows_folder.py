"""Жанр книги в каталоге — по жанровой папке 1-го уровня (Жанр/Автор/…), а
не по тегу <genre> файла: переложили книгу в другую жанровую папку — после
скана библиотеки у неё жанр новой папки, а старая запись исчезает из
каталога (opdsdb.addgenre_from_folder, sopdscan.processfile)."""
import shutil

import pytest

from opds_catalog import opdsdb
from opds_catalog.models import Book
from opds_catalog.sopdscan import opdsScanner

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>Фантастика</genre>
<author><first-name>Иван</first-name><last-name>Иванов</last-name></author>
<book-title>Звёзды</book-title><lang>ru</lang></title-info></description>
<body><section><p>Текст книги.</p></section></body>
</FictionBook>
"""


def _genres(book):
    return sorted(g.genre for g in book.genres.all())


@pytest.mark.django_db
@pytest.mark.parametrize("logical", [True, False])
def test_moving_book_to_another_genre_folder_changes_its_genre(tmp_path, override_config, logical):
    opdsdb.clear_all()
    src = tmp_path / "Фантастика" / "Иванов Иван" / "Звёзды.fb2"
    src.parent.mkdir(parents=True)
    src.write_text(_FB2, encoding="utf-8")

    with override_config(SOPDS_ROOT_LIB=str(tmp_path), SOPDS_DELETE_LOGICAL=logical):
        opdsScanner().scan_all()
        [book] = Book.objects.filter(avail__gt=0)
        assert _genres(book) == ["Фантастика"]

        dst = tmp_path / "Детектив" / "Иванов Иван" / "Звёзды.fb2"
        dst.parent.mkdir(parents=True)
        shutil.move(str(src), str(dst))  # тег <genre> в файле так и остался «Фантастика»
        opdsScanner().scan_all()

    available = list(Book.objects.filter(avail__gt=0))
    assert len(available) == 1
    assert _genres(available[0]) == ["Детектив"]
    assert [a.full_name for a in available[0].authors.all()] == ["Иванов Иван"]
    old = Book.objects.filter(path__startswith="Фантастика")
    assert (old.count() == 1 and old.first().avail == 0) if logical else not old.exists()
