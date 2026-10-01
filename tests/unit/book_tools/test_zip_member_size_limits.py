"""Zip-бомбы: член архива читался целиком в память без проверки размера
(разбор FB2 из .fb2.zip, выдача книги из zip-коллекции, обложка EPUB,
сканер). Маленький архив с гигабайтами нулей внутри съедал память
воркера. Теперь объявленный `ZipInfo.file_size` сверяется с лимитом
(zipfile сам не отдаёт больше объявленного).

Лимиты в тестах подменены маленькими, чтобы не создавать архивы
на сотни мегабайт.
"""
import io
import zipfile

import pytest

import book_tools.services as services
import opds_catalog.utils as opds_utils
from book_tools.exceptions import FB2StructureException
from book_tools.format.util import ZipMemberTooLarge, read_zip_member

_FB2 = (
    b'<?xml version="1.0" encoding="utf-8"?>'
    b'<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
    b'<description><title-info><book-title>T</book-title></title-info></description>'
    b'<body><section><p>' + b' ' * 4000 + b'</p></section></body></FictionBook>'
)


def _zip(name: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, data)
    return buf.getvalue()


def test_read_zip_member_refuses_oversized_member():
    with zipfile.ZipFile(io.BytesIO(_zip("a.fb2", b"\0" * 5000))) as z:
        with pytest.raises(ZipMemberTooLarge):
            read_zip_member(z, "a.fb2", 1000)
        assert read_zip_member(z, "a.fb2", 5000) == b"\0" * 5000


def test_fb2_zip_parser_refuses_oversized_member(monkeypatch):
    monkeypatch.setattr(services, "MAX_FB2_XML_SIZE", 1000)
    with pytest.raises(FB2StructureException):
        services.parse_fb2_zip(io.BytesIO(_zip("book.fb2", _FB2)), "book.fb2.zip")


def test_fb2_zip_parser_reads_member_within_limit():
    meta = services.parse_fb2_zip(io.BytesIO(_zip("book.fb2", _FB2)), "book.fb2.zip")
    assert meta.title == "T"


def test_book_from_zip_collection_refused_when_oversized(tmp_path, monkeypatch):
    archive = tmp_path / "lib.zip"
    archive.write_bytes(_zip("book.fb2", _FB2))
    monkeypatch.setattr(opds_utils, "MAX_BOOK_FILE_SIZE", 1000)
    assert opds_utils.read_from_zipped_file(str(archive), "book.fb2") is None


def test_book_from_zip_collection_read_within_limit(tmp_path):
    archive = tmp_path / "lib.zip"
    archive.write_bytes(_zip("book.fb2", _FB2))
    assert opds_utils.read_from_zipped_file(str(archive), "book.fb2").getvalue() == _FB2
