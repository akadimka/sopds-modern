"""Книга уже в каталоге, а файл на диске изменился (fb2parser поправил
<book-title> — баг №148: «Отдел «Q» 8. Жертва 2117» → «Жертва 2117»): скан
перечитывает название и аннотацию. Раньше сканер для известной книги
ничего не перечитывал, кроме пустой аннотации, и каталог навсегда
оставался со старым названием (sopdscan.processfile, opdsdb.refreshbook)."""
import pytest

from opds_catalog import opdsdb
from opds_catalog.models import Book
from opds_catalog.sopdscan import opdsScanner

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>detective</genre>
<author><first-name>Юсси</first-name><last-name>Адлер-Ольсен</last-name></author>
<book-title>{title}</book-title><annotation><p>{ann}</p></annotation><lang>ru</lang></title-info></description>
<body><section><p>Текст книги.</p></section></body>
</FictionBook>
"""


@pytest.mark.django_db
def test_changed_file_refreshes_title_and_annotation(tmp_path, override_config):
    opdsdb.clear_all()
    book_file = tmp_path / "Детектив" / "Адлер-Ольсен Юсси" / "Жертва 2117 т. 8.fb2"
    book_file.parent.mkdir(parents=True)
    book_file.write_text(_FB2.format(title="Отдел «Q» 8. Жертва 2117", ann="Старая"), encoding="utf-8")

    with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
        opdsScanner().scan_all()
        [book] = Book.objects.filter(avail__gt=0)
        assert book.title == "Отдел «Q» 8. Жертва 2117"

        book_file.write_text(_FB2.format(title="Жертва 2117", ann="Новая аннотация"), encoding="utf-8")
        scanner = opdsScanner()
        scanner.scan_all()

    book = Book.objects.get(pk=book.pk)
    assert (book.title, book.search_title) == ("Жертва 2117", "ЖЕРТВА 2117")
    assert "Новая аннотация" in book.annotation
    assert book.filesize == book_file.stat().st_size
    assert scanner.books_refreshed == 1
    assert Book.objects.count() == 1


@pytest.mark.django_db
def test_unchanged_file_is_not_reread(tmp_path, override_config, monkeypatch):
    opdsdb.clear_all()
    book_file = tmp_path / "Детектив" / "Адлер-Ольсен Юсси" / "Жертва 2117.fb2"
    book_file.parent.mkdir(parents=True)
    book_file.write_text(_FB2.format(title="Жертва 2117", ann="Аннотация"), encoding="utf-8")

    with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
        opdsScanner().scan_all()
        reads = []
        import opds_catalog.sopdscan as sopdscan
        monkeypatch.setattr(sopdscan, "create_bookfile", lambda *a: reads.append(a))
        scanner = opdsScanner()
        scanner.scan_all()

    assert reads == [] and scanner.books_refreshed == 0
