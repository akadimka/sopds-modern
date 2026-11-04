"""SAX-разбор pass1 обрывается на ошибке XML в заголовке — жанр, стоящий
после неё, добирается из <title-info> (2026-11-04).

Файлы «Author Today Book to FB2» (E:\\Temp\\Book Source\\СЕРИЯ.LitRPG:
Shellina «Мир», Багиров «Проект Шахта») — windows-1251, кавычки внутри
атрибута не экранированы: `<sequence name=""Мир" Первая загрузка">`. SAX
падал на серии, `<genre>` за ней не читался — книга без жанра не
синхронизировалась (в библиотеку — только жанры из списка).
"""
from fb2parser_core.fb2_sax_extractor import FB2SAXExtractor

_BROKEN = """<?xml version="1.0" encoding="windows-1251"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" xmlns:l="http://www.w3.org/1999/xlink">
<description><title-info>
<author><first-name>shellina</first-name><last-name></last-name></author>
<book-title>"Мир" Первая загрузка</book-title>
<lang>ru</lang>
<sequence name=""Мир" Первая загрузка" number="1"></sequence>
<genre>Фантастика</genre>
</title-info></description>
<body><section><p>текст</p></section></body></FictionBook>
"""


def _write(tmp_path, text, encoding):
    path = tmp_path / "1. Первая загрузка.fb2"
    path.write_bytes(text.encode(encoding))
    return path


def test_genre_after_broken_sequence_is_found(tmp_path):
    meta = FB2SAXExtractor()._extract_all_metadata_at_once(_write(tmp_path, _BROKEN, "cp1251"))
    assert meta["genre"] == "Фантастика"
    assert meta["title"] == '"Мир" Первая загрузка' and meta["authors"] == "shellina"
    assert meta["series"] == ""          # сломанный атрибут не угадываем — серию дают папки


def test_well_formed_file_is_read_by_sax_as_before(tmp_path):
    good = _BROKEN.replace('name=""Мир" Первая загрузка"', 'name="Мир"').replace("Фантастика", "sf_litrpg")
    meta = FB2SAXExtractor()._extract_all_metadata_at_once(_write(tmp_path, good, "cp1251"))
    assert meta["genre"] == "sf_litrpg" and meta["series"] == "Мир" and meta["series_number"] == "1"
