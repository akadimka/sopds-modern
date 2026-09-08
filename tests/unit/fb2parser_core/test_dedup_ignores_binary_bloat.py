"""Регрессия для `FB2CompilerService._dedup_by_content()` — docs/quality-
roadmap.md, баг №38.

Реальный случай (замечен пользователем в превью компиляции): Флинн
Гиллиан / "Острые предметы" — два файла с совпадающим началом текста.
"Острые предметы.fb2" тяжелее в байтах ИЗ-ЗА встроенных иллюстраций
(<binary>), а "Острые предметы. Кто-то взрослый.fb2" легче по байтам, но
содержит ДОПОЛНИТЕЛЬНУЮ повесть (реально больше текста). Тай-брейкер
дедупа сравнивал сырой размер файла и оставлял файл с картинками,
удаляя файл с уникальным дополнительным произведением.
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import CompilationBook, FB2CompilerService

_OPENING = "Общий пролог романа, слово в слово одинаковый у обоих изданий. " * 40

_FAKE_IMAGE_B64 = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo=" * 2000  # ~70 КБ "картинки"

_TMPL_WITH_IMAGE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Гиллиан</first-name><last-name>Флинн</last-name></author>
<book-title>Острые предметы</book-title></title-info></description>
<body><title><p>Острые предметы</p></title><section><p>{opening}</p></section></body>
<binary id="cover.jpg" content-type="image/jpeg">{image}</binary>
</FictionBook>
"""

_TMPL_WITH_NOVELLA = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Гиллиан</first-name><last-name>Флинн</last-name></author>
<book-title>Острые предметы. Кто-то взрослый</book-title></title-info></description>
<body><title><p>Острые предметы</p></title><section><p>{opening}</p></section></body>
<body name="notes"><title><p>Кто-то взрослый</p></title>
<section><p>{novella}</p></section></body>
</FictionBook>
"""


def _make_book(record: BookRecord, path: Path) -> CompilationBook:
    return CompilationBook(
        record=record, abs_path=path, sort_key=(0, 0, 0, 0),
        sort_source="filename", order_ambiguous=False,
    )


def test_smaller_file_with_extra_work_beats_bigger_file_with_illustrations(tmp_path):
    novella_text = "Уникальный текст дополнительной повести, которого нет в другом файле. " * 20

    path_with_image = tmp_path / "Флинн Гиллиан - Острые предметы.fb2"
    path_with_image.write_text(
        _TMPL_WITH_IMAGE.format(opening=_OPENING, image=_FAKE_IMAGE_B64), encoding="utf-8",
    )
    path_with_novella = tmp_path / "Флинн Гиллиан - Острые предметы. Кто-то взрослый.fb2"
    path_with_novella.write_text(
        _TMPL_WITH_NOVELLA.format(opening=_OPENING, novella=novella_text), encoding="utf-8",
    )

    # Файл с картинкой физически тяжелее на диске, но текста в нём меньше.
    assert path_with_image.stat().st_size > path_with_novella.stat().st_size

    rec_image = BookRecord(
        file_path=path_with_image.name, file_title="Острые предметы",
        metadata_authors="Гиллиан Флинн", proposed_author="Флинн Гиллиан",
        author_source="filename", metadata_series="", proposed_series="",
        series_source="filename", series_number="",
    )
    rec_novella = BookRecord(
        file_path=path_with_novella.name, file_title="Острые предметы. Кто-то взрослый",
        metadata_authors="Гиллиан Флинн", proposed_author="Флинн Гиллиан",
        author_source="filename", metadata_series="", proposed_series="",
        series_source="filename", series_number="",
    )

    books = [_make_book(rec_image, path_with_image), _make_book(rec_novella, path_with_novella)]
    svc = FB2CompilerService()
    duplicate_paths = []
    kept = svc._dedup_by_content(books, duplicate_paths)

    assert len(kept) == 1
    assert kept[0].abs_path == path_with_novella
    assert duplicate_paths == [path_with_image]
