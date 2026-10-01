"""`GenreAssignmentService._assign_genre_to_file()` меняет жанры только в
`<title-info>`.

Раньше regex вырезал `<genre>` по всему документу — включая
`<src-title-info>` (жанры оригинала у переводной книги) — и вставлял
новый тег в конец `<title-info>` без префикса пространства имён, хотя по
схеме FB2 жанры идут первыми, а в файле с `<fb:title-info>` тег без
префикса оказывается в чужом пространстве имён.
"""
import xml.etree.ElementTree as ET

from fb2parser_core.genre_assign import GenreAssignmentService

FB = "{http://www.gribuser.ru/xml/fictionbook/2.0}"

_TRANSLATED = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
  <title-info>
    <genre>sf</genre>
    <genre>adventure</genre>
    <author><first-name>Ф</first-name><last-name>А</last-name></author>
    <book-title>Перевод</book-title>
  </title-info>
  <src-title-info>
    <genre>prose_classic</genre>
    <book-title>Original</book-title>
  </src-title-info>
</description>
<body><section><p>x</p></section></body></FictionBook>
"""

_PREFIXED = """<?xml version="1.0" encoding="utf-8"?>
<fb:FictionBook xmlns:fb="http://www.gribuser.ru/xml/fictionbook/2.0">
<fb:description><fb:title-info lang="ru">
<fb:genre>sf</fb:genre>
<fb:book-title>Т</fb:book-title></fb:title-info></fb:description>
<fb:body><fb:section><fb:p>x</fb:p></fb:section></fb:body></fb:FictionBook>
"""


def _assign(tmp_path, content, genre):
    path = tmp_path / "book.fb2"
    path.write_text(content, encoding="utf-8")
    assert GenreAssignmentService()._assign_genre_to_file(path, genre) is True
    return ET.fromstring(path.read_bytes())


def test_src_title_info_genres_untouched_and_genre_first(tmp_path):
    root = _assign(tmp_path, _TRANSLATED, "Фантастика")
    title_info = root.find(f"{FB}description/{FB}title-info")
    assert [g.text for g in title_info.findall(f"{FB}genre")] == ["Фантастика"]
    assert title_info[0].tag == f"{FB}genre"
    src = root.find(f"{FB}description/{FB}src-title-info")
    assert [g.text for g in src.findall(f"{FB}genre")] == ["prose_classic"]


def test_prefixed_document_gets_prefixed_genre(tmp_path):
    root = _assign(tmp_path, _PREFIXED, "Детектив & триллер")
    title_info = root.find(f"{FB}description/{FB}title-info")
    assert [g.text for g in title_info.findall(f"{FB}genre")] == ["Детектив & триллер"]
