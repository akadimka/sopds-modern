"""Жанры, которые видит сканер жанров (`FB2AuthorExtractor.
_extract_genres_from_fb2`, regex), должны совпадать с тем, что видит
pass1 regen (`FB2SAXExtractor`, SAX по локальному имени тега).

Regex искал только `<genre>` без префикса и `<title-info>` без атрибутов:
у файла с `<fb:genre>` сканер жанров на главной показывал «Не определено»,
хотя regen жанр находил. Плюс `&amp;` не раскрывался, повторы не убирались.
"""
import pytest

from fb2parser_core.fb2_author_extractor import FB2AuthorExtractor
from fb2parser_core.fb2_sax_extractor import FB2SAXExtractor
from fb2parser_web.fb2parser_bridge import _config_path

_PLAIN = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>
<genre>sf_fantasy</genre><genre>adventure</genre>
<book-title>Т</book-title></title-info></description><body><section><p>x</p></section></body></FictionBook>
"""

_PREFIXED = """<?xml version="1.0" encoding="utf-8"?>
<fb:FictionBook xmlns:fb="http://www.gribuser.ru/xml/fictionbook/2.0">
<fb:description><fb:title-info>
<fb:genre>detective</fb:genre><fb:genre match="80">thriller</fb:genre>
<fb:book-title>Т</fb:book-title></fb:title-info></fb:description>
<fb:body><fb:section><fb:p>x</fb:p></fb:section></fb:body></fb:FictionBook>
"""

_ATTRS_AND_ENTITIES = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info xml:lang="ru">
<genre>sf</genre><genre>  sf  </genre><genre>home_cooking &amp; diy</genre>
<book-title>Т</book-title></title-info>
<src-title-info><genre>prose_classic</genre></src-title-info></description>
<body><section><p>x</p></section></body></FictionBook>
"""


@pytest.mark.parametrize("content, expected", [
    (_PLAIN, "sf_fantasy, adventure"),
    (_PREFIXED, "detective, thriller"),
    (_ATTRS_AND_ENTITIES, "sf, home_cooking & diy"),
])
def test_regex_genres_match_sax(tmp_path, content, expected):
    fb2 = tmp_path / "book.fb2"
    fb2.write_text(content, encoding="utf-8")

    regex_genres = FB2AuthorExtractor(_config_path())._extract_genres_from_fb2(fb2)
    sax_genres = FB2SAXExtractor(_config_path())._extract_all_metadata_at_once(fb2)["genre"]

    assert regex_genres == expected
    assert regex_genres == sax_genres
