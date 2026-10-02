"""Извлечение метаданных из EPUB рабочим парсером `book_tools.format.epub.EPub`
(его использует сканер через create_bookfile). Раньше эти значения
проверялись на `parsers.EpubParser` — недоподключённой альтернативе,
которую нигде не вызывали; она удалена."""
import os

import pytest

from book_tools.format.epub import EPub
from tests.helpers import read_file_as_iobytes


@pytest.mark.parametrize(
    "book",
    [
        "mirer.epub",
    ],
)
def test_epub_parser(test_rootlib, book) -> None:
    file = read_file_as_iobytes(os.path.join(test_rootlib, book))
    book_actual = EPub(file, "Test Book")
    book_new = EPub(file, "Test Book").parse_book_data(file, "Test Book")
    assert book_actual == book_new


@pytest.fixture(scope="module")
def parsed_epub(test_rootlib) -> EPub:
    return EPub(read_file_as_iobytes(os.path.join(test_rootlib, "mirer.epub")), "Test Book")


class TestEpubValues:
    """Тесты извлечения данных книги парсером epub"""

    def test_title(self, parsed_epub) -> None:
        assert parsed_epub.title == "У меня девять жизней (шф (продолжатели))"

    def test_authors(self, parsed_epub) -> None:
        assert [a["name"] for a in parsed_epub.authors] == ["Александр Мирер"]

    def test_tags(self, parsed_epub) -> None:
        assert parsed_epub.tags == ["sf"]

    def test_language_code(self, parsed_epub) -> None:
        assert parsed_epub.language_code == "ru"

    def test_series_info(self, parsed_epub) -> None:
        assert parsed_epub.series_info == {"title": "ШФ (продолжатели)", "index": None}

    def test_docdate(self, parsed_epub) -> None:
        assert parsed_epub.docdate == "2015"

    def test_description(self, parsed_epub) -> None:
        assert len(parsed_epub.description) == 28

    def test_cover(self, parsed_epub) -> None:
        assert len(parsed_epub.extract_cover_memory()) == 41886
