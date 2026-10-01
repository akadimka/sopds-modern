import os
import shutil

import pytest
from django.utils.translation import gettext

from opds_catalog import opdsdb
from opds_catalog.dl import getFileData
from opds_catalog.models import Author, Book, Catalog, Genre, Series
from opds_catalog.opdsdb import CAT_ZIP
from opds_catalog.sopdscan import opdsScanner

_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "data")


@pytest.fixture
def sample_books_root(tmp_path):
    """Только книги верхнего уровня tests/data: подпапки (regen_library и др.) —
    фикстуры fb2parser, scan_all по ним считал бы сотни лишних книг."""
    for name in os.listdir(_DATA):
        path = os.path.join(_DATA, name)
        if os.path.isfile(path):
            shutil.copyfile(path, tmp_path / name)
    return str(tmp_path)


def _assert_root_level_author_and_genre(book):
    """Автор и жанр берутся из папок «жанр/автор/…» (не из тегов книги);
    у файла в корне библиотеки обоих уровней нет."""
    assert [a.full_name for a in book.authors.all()] == [gettext("Unknown author")]
    assert [g.section for g in book.genres.all()] == [str(opdsdb.unknown_genre)]


@pytest.mark.django_db
@pytest.mark.usefixtures("fake_sopds_root_lib")
class TestBookScaner(object):
    test_module_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir
    )
    test_ROOTLIB = os.path.join(test_module_path, "data")
    test_fb2 = "262001.fb2"
    test_fb2zip = "262001.zip"
    test_epub = "mirer.epub"
    test_mobi = "robin_cook.mobi"
    test_zip = "books.zip"

    @pytest.mark.parametrize("fb2sax", [True, False])
    def test_processfile_fb2(self, fb2sax, override_config):
        """Тестирование процедуры processfile (извлекает метаданные из книги FB2 и помещает в БД)"""
        opdsdb.clear_all()
        scanner = opdsScanner()
        with override_config(SOPDS_FB2SAX=fb2sax):
            scanner.processfile(
                self.test_fb2,
                self.test_ROOTLIB,
                os.path.join(self.test_ROOTLIB, self.test_fb2),
                None,
                0,
                495373,
            )
        book = Book.objects.get(filename=self.test_fb2)
        assert book is not None
        assert scanner.books_added == 1
        assert book.filename == self.test_fb2
        assert book.path == "."
        assert book.format == "fb2"
        assert book.cat_type == 0
        # self.assertGreaterEqual(book.registerdate, )
        assert book.docdate == "30.1.2011"
        assert book.lang == "en"
        assert book.title == "The Sanctuary Sparrow"
        assert book.search_title == "The Sanctuary Sparrow".upper()
        assert book.annotation == ""
        assert book.avail == 2
        assert book.catalog.path == "."
        assert book.catalog.cat_name == "."
        assert book.catalog.cat_type == 0
        assert book.filesize == 495373

        _assert_root_level_author_and_genre(book)
        assert getFileData(book) is not None

    @pytest.mark.parametrize("fb2sax", [True, False])
    def test_processfile_fb2zip(self, fb2sax, override_config):
        """Тестирование процедуры processfile (извлекает метаданные из книги FB2 и помещает в БД)"""
        opdsdb.clear_all()
        scanner = opdsScanner()
        with override_config(SOPDS_FB2SAX=fb2sax):
            scanner.processzip(
                self.test_fb2zip,
                self.test_ROOTLIB,
                os.path.join(self.test_ROOTLIB, self.test_fb2zip),
                # None,
                # 0,
                # 495373,
            )
        book: Book = Book.objects.all()[0]
        assert book is not None
        assert scanner.books_added == 1
        assert book.filename == self.test_fb2
        assert book.path == self.test_fb2zip
        assert book.format == "fb2"
        assert book.cat_type == CAT_ZIP
        # self.assertGreaterEqual(book.registerdate, )
        assert book.docdate == "30.1.2011"
        assert book.lang == "en"
        assert book.title == "The Sanctuary Sparrow"
        assert book.search_title == "The Sanctuary Sparrow".upper()
        assert book.annotation == ""
        assert book.avail == 2
        assert book.catalog.path == self.test_fb2zip
        assert book.catalog.cat_name == self.test_fb2zip
        assert book.catalog.cat_type == CAT_ZIP
        assert book.filesize == 495373

        _assert_root_level_author_and_genre(book)
        assert getFileData(book) is not None

    def test_processfile_epub(self):
        """Тестирование процедуры processfile (извлекает метаданные из книги EPUB и помещает в БД)"""
        opdsdb.clear_all()
        scanner = opdsScanner()
        scanner.processfile(
            self.test_epub,
            self.test_ROOTLIB,
            os.path.join(self.test_ROOTLIB, self.test_epub),
            None,
            0,
            491279,
        )
        book = Book.objects.get(filename=self.test_epub)
        assert book is not None
        assert scanner.books_added == 1
        assert book.filename == self.test_epub
        assert book.path == "."
        assert book.format == "epub"
        assert book.cat_type == 0
        # assertGreater book.registerdate== )
        assert book.docdate == "2015"
        assert book.lang == "ru"
        assert book.title == "У меня девять жизней (шф (продолжатели))"
        assert book.search_title == "У меня девять жизней (шф (продолжатели))".upper()

        assert book.annotation == "Собрание произведений. Том 2"
        assert book.avail == 2
        assert book.catalog.path == "."
        assert book.catalog.cat_name == "."
        assert book.catalog.cat_type == 0
        assert book.filesize == 491279

        _assert_root_level_author_and_genre(book)

    def test_processfile_mobi(self):
        """Тестирование процедуры processfile (извлекает метаданные из книги EPUB и помещает в БД)"""
        opdsdb.clear_all()
        scanner = opdsScanner()
        scanner.processfile(
            self.test_mobi,
            self.test_ROOTLIB,
            os.path.join(self.test_ROOTLIB, self.test_mobi),
            None,
            0,
            542811,
        )
        book = Book.objects.get(filename=self.test_mobi)
        assert book is not None
        assert scanner.books_added == 1
        assert book.filename == self.test_mobi
        assert book.path == "."
        assert book.format == "mobi"
        assert book.cat_type == 0
        # self.assertGreaterEqual(book.registerdate ==
        assert book.docdate == "2011-11-20"
        assert book.lang == ""
        assert book.title == "Vector"
        assert book.search_title == "Vector".upper()
        assert book.annotation == ""
        assert book.avail == 2
        assert book.catalog.path == "."
        assert book.catalog.cat_name == "."
        assert book.catalog.cat_type == 0
        assert book.filesize == 542811

        _assert_root_level_author_and_genre(book)

    def test_processzip(self):
        """Тестирование процедуры processzip (извлекает метаданные из книг, помещенных в архив и помещает их БД)"""
        opdsdb.clear_all()
        scanner = opdsScanner()
        scanner.processzip(
            self.test_zip,
            self.test_ROOTLIB,
            os.path.join(self.test_ROOTLIB, self.test_zip),
        )
        assert scanner.books_added == 3
        assert Book.objects.all().count() == 3
        assert Catalog.objects.all().count() == 2

        book = Book.objects.get(filename="539603.fb2")
        assert book.filesize == 15194
        assert book.path == self.test_zip
        assert book.cat_type == 1
        assert book.catalog.path == self.test_zip
        assert book.catalog.cat_name == self.test_zip
        assert book.catalog.cat_type == 1
        assert book.docdate == "2014-09-15"
        assert book.title == "Любовь в жизни Обломова"
        assert book.avail == 2
        _assert_root_level_author_and_genre(book)

        book = Book.objects.get(filename="539485.fb2")
        assert book.filesize == 12293
        assert book.path == self.test_zip
        assert book.cat_type == 1
        assert book.title == "Китайски сладкиш с късметче"
        _assert_root_level_author_and_genre(book)

        book = Book.objects.get(filename="539273.fb2")
        assert book.filesize == 21722
        assert book.path == self.test_zip
        assert book.cat_type == 1
        assert book.title == "Драконьи Услуги"
        _assert_root_level_author_and_genre(book)

    def test_scanall(self, sample_books_root, override_config):
        """Тестирование процедуры scanall (извлекает метаданные из книг и помещает в БД)"""
        opdsdb.clear_all()
        with override_config(SOPDS_ROOT_LIB=sample_books_root):
            scanner = opdsScanner()
            scanner.scan_all()
        assert scanner.books_added == 10
        assert scanner.bad_books == 1
        assert Book.objects.all().count() == 10
        # Все книги в корне библиотеки: автор и жанр — «неизвестные».
        assert Author.objects.all().count() == 1
        assert Genre.objects.all().count() == 1
        assert Series.objects.all().count() == 1
        assert Catalog.objects.all().count() == 5


@pytest.mark.django_db
def test_inpx_scanner(sample_books_root, override_config) -> None:
    with override_config(SOPDS_ROOT_LIB=sample_books_root, SOPDS_INPX_ENABLE=True, SOPDS_INPX_TEST_FILES=False):
        scanner = opdsScanner()
        scanner.scan_all()
    assert scanner.books_added == 3
    assert scanner.bad_books == 0
    assert Book.objects.count() == scanner.books_added


@pytest.mark.django_db
class TestScanAllRespectsDeleteLogicalSetting:
    """Регрессия — docs/quality-roadmap.md, баг №89.

    `scan_all()` игнорировал `SOPDS_DELETE_LOGICAL` — ветка была
    закомментирована, и книги, пропавшие с диска, ВСЕГДА удалялись из
    БД физически и безвозвратно, даже когда в настройках сайта включено
    «логическое удаление» (checkbox в sopds_settings.html), которое
    пользователь ожидает восстанавливаемым.
    """

    def test_logical_delete_keeps_row_but_marks_unavailable(
        self, override_config, tmp_path, catalog
    ):
        book = Book.objects.create(
            filename="gone.fb2", path=".", format="fb2", cat_type=0,
            title="Gone", search_title="GONE", avail=2, catalog=catalog,
        )
        with override_config(SOPDS_ROOT_LIB=str(tmp_path), SOPDS_DELETE_LOGICAL=True):
            scanner = opdsScanner()
            scanner.scan_all()

        assert scanner.books_deleted == 1
        book.refresh_from_db()
        assert book.avail == 0  # мягко скрыта, но строка сохранена
        assert Book.objects.filter(id=book.id).exists()

    def test_physical_delete_removes_row(self, override_config, tmp_path, catalog):
        book = Book.objects.create(
            filename="gone.fb2", path=".", format="fb2", cat_type=0,
            title="Gone", search_title="GONE", avail=2, catalog=catalog,
        )
        with override_config(SOPDS_ROOT_LIB=str(tmp_path), SOPDS_DELETE_LOGICAL=False):
            scanner = opdsScanner()
            scanner.scan_all()

        # books_del_phisical() возвращает результат QuerySet.delete() —
        # (total_count, {model_label: count}), а не голое число.
        assert scanner.books_deleted[0] == 1
        assert not Book.objects.filter(id=book.id).exists()
