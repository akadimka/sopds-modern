"""Метаданные книги в .fb2.zip читаются из архива.

Сервер 2026-11-02: книги, пришедшие на синхронизацию в .fb2.zip, легли в
папку «Без жанра» с «[no title]» в имени — разборщики читали байты zip, а не
XML внутри, и не находили ни названия, ни автора, ни жанра.
"""
import zipfile

import pytest

from fb2parser_core.fb2_author_extractor import FB2AuthorExtractor
from fb2parser_core.fb2_sax_extractor import FB2SAXExtractor

_FB2 = (
    '<?xml version="1.0" encoding="{enc}"?>\n'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">\n'
    "<description><title-info><genre>Классическая проза</genre>"
    "<author><first-name>Василий</first-name><last-name>Шукшин</last-name></author>"
    "<book-title>Рассказы</book-title><sequence name=\"Избранное\" number=\"2\"/>"
    "</title-info></description>\n<body><section><p>Текст</p></section></body></FictionBook>\n"
)


def _zipped(tmp_path, enc):
    path = tmp_path / "book.fb2.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("book.fb2", _FB2.format(enc=enc).encode(enc))
    return path


@pytest.mark.parametrize("extractor", [FB2SAXExtractor, FB2AuthorExtractor])
@pytest.mark.parametrize("enc", ["utf-8", "windows-1251"])
def test_metadata_is_read_from_zipped_book(tmp_path, extractor, enc):
    meta = extractor()._extract_all_metadata_at_once(_zipped(tmp_path, enc))
    assert meta["title"] == "Рассказы"
    assert meta["genre"] == "Классическая проза"
    assert "Шукшин" in meta["authors"]
    assert meta["series"] == "Избранное"


def test_genre_scan_reads_zipped_book(tmp_path):
    assert FB2AuthorExtractor()._extract_genres_from_fb2(_zipped(tmp_path, "windows-1251")) == "Классическая проза"
