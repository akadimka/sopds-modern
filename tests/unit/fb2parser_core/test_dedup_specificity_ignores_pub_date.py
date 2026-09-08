"""Регрессия для `FB2CompilerService._dedup_by_content()` — docs/quality-
roadmap.md, баг №39.

Реальный случай (замечен пользователем в превью компиляции): Флинн
Гиллиан / "Острые предметы" — два файла без номера в серии, с почти
идентичным началом текста. У одного файла в FB2 есть тег `<date>`
(год издания), из-за чего `_determine_sort_key()` вернул fallback
`(2, 2013, 0, 0)` ('title_date'). `_specificity()` в дедупе слепо
считала любой `sort_key[1] > 0` "прямой позицией в серии" — год издания
попал под это правило и получил максимальный приоритет 3 против 0 у
второго файла, хотя год издания вообще не имеет отношения к позиции в
серии. В реальности "выигравший" по этой ложной причине файл был
меньше по содержанию, чем удалённый — тот содержал дополнительную
повесть.
"""
from pathlib import Path

import pytest

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import CompilationBook, FB2CompilerService

_OPENING = "Общий пролог романа, слово в слово одинаковый у обоих изданий. " * 40

_TMPL_WITH_DATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Гиллиан</first-name><last-name>Флинн</last-name></author>
<book-title>Острые предметы</book-title>
<date value="2013-01-01">2013</date></title-info></description>
<body><title><p>Острые предметы</p></title><section><p>{opening}</p></section></body>
</FictionBook>
"""

_TMPL_WITH_NOVELLA_NO_DATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Гиллиан</first-name><last-name>Флинн</last-name></author>
<book-title>Острые предметы. Кто-то взрослый</book-title></title-info></description>
<body><title><p>Острые предметы</p></title><section><p>{opening}</p></section></body>
<body name="notes"><title><p>Кто-то взрослый</p></title>
<section><p>{novella}</p></section></body>
</FictionBook>
"""


def _make_book(svc: FB2CompilerService, record: BookRecord, work_dir: Path) -> CompilationBook:
    return svc._make_book(record, work_dir)


@pytest.fixture
def paths(tmp_path):
    novella_text = "Уникальный текст дополнительной повести, которого нет в другом файле. " * 20

    path_with_date = tmp_path / "Флинн Гиллиан - Острые предметы.fb2"
    path_with_date.write_text(_TMPL_WITH_DATE.format(opening=_OPENING), encoding="utf-8")

    path_with_novella = tmp_path / "Флинн Гиллиан - Острые предметы. Кто-то взрослый.fb2"
    path_with_novella.write_text(
        _TMPL_WITH_NOVELLA_NO_DATE.format(opening=_OPENING, novella=novella_text), encoding="utf-8",
    )
    return path_with_date, path_with_novella


def test_publication_year_does_not_win_over_bigger_real_content(paths, tmp_path):
    path_with_date, path_with_novella = paths

    rec_date = BookRecord(
        file_path=path_with_date.name, file_title="Острые предметы",
        metadata_authors="Гиллиан Флинн", proposed_author="Флинн Гиллиан",
        author_source="filename", metadata_series="", proposed_series="Острые предметы",
        series_source="filename_prefix_pattern", series_number="",
    )
    rec_novella = BookRecord(
        file_path=path_with_novella.name, file_title="Острые предметы. Кто-то взрослый",
        metadata_authors="Гиллиан Флинн", proposed_author="Флинн Гиллиан",
        author_source="filename", metadata_series="", proposed_series="Острые предметы",
        series_source="filename_prefix_pattern", series_number="",
    )

    svc = FB2CompilerService()
    book_date = _make_book(svc, rec_date, tmp_path)
    book_novella = _make_book(svc, rec_novella, tmp_path)

    # Sanity: fallback по дате действительно сработал (level 2, 'title_date').
    assert book_date.sort_key[0] == 2
    assert book_novella.sort_key[0] == 9

    duplicate_paths = []
    kept = svc._dedup_by_content([book_date, book_novella], duplicate_paths)

    assert len(kept) == 1
    assert kept[0].abs_path == path_with_novella
    assert duplicate_paths == [path_with_date]
