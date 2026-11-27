"""Серия из метаданных книги не выводит сборник за папку назначения
(проверка безопасности 2026-11-27).

Имя сборника — «Автор - Серия (Суффикс).fb2». Первая «\\» в серии —
внутренний разделитель серии и подсерии; остальные «\\» в имени файла не
заменялись (в отличие от «/»), а на Windows это разделитель каталогов:
серия «Мир\\..\\..\\..\\x» из <sequence name> книги во входящих поднимала
запись сборника выше папки серии. Теперь лишние «\\» заменяются на «_», и
запись вне папки назначения запрещена.
"""
from pathlib import Path

from fb2parser_core.fb2_compiler import (
    CompilationBook,
    CompilationGroup,
    FB2CompilerService,
)
from fb2parser_core.passes.pass1_read_files import BookRecord

EVIL = "Мир\\..\\..\\..\\pwn"


def _volume(folder: Path, n: int) -> Path:
    path = folder / f"Автор Тестов - Том {n}.fb2"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info>'
        '<genre>sf</genre><author><first-name>Автор</first-name><last-name>Тестов</last-name></author>'
        f'<book-title>Том {n}</book-title><lang>ru</lang>'
        f'<sequence name="Мир" number="{n}"/></title-info></description>'
        f'<body><section><title><p>Глава</p></title><p>текст {n}</p></section></body></FictionBook>',
        encoding="utf-8")
    return path


def _group(folder: Path, series: str) -> CompilationGroup:
    books = []
    for n in (1, 2):
        path = _volume(folder, n)
        record = BookRecord(file_path=path.name, file_title=f"Том {n}", metadata_authors="Автор Тестов",
                            proposed_author="Автор Тестов", author_source="metadata", metadata_series=series,
                            proposed_series=series, series_source="metadata", metadata_genre="sf",
                            series_number=str(n))
        books.append(CompilationBook(record=record, abs_path=path, sort_key=(0, n, 0, 0),
                                     sort_source="series_number", order_ambiguous=False, volume_label=str(n)))
    return CompilationGroup(author="Автор Тестов", series=series, books=books,
                            order_determined=True, volume_range="1-2")


def test_series_with_backslashes_stays_in_the_destination_folder(tmp_path):
    folder = tmp_path / "lib" / "Фантастика" / "Автор Тестов" / "Мир"
    folder.mkdir(parents=True)
    result = FB2CompilerService().compile_group(_group(folder, EVIL), folder)
    assert result.success, result.error
    assert result.output_path.parent == folder
    assert "\\" not in result.output_path.name and "/" not in result.output_path.name
    assert not list(tmp_path.rglob("*pwn*.fb2")) or all(p.parent == folder for p in tmp_path.rglob("*pwn*.fb2"))


def test_subseries_separator_still_shown_as_before():
    """Первая «\\» — разделитель серии и подсерии — по-прежнему даёт «Серия. Подсерия»."""
    assert FB2CompilerService._safe_series_filename("Хроники\\Первый цикл") == "Хроники. Первый цикл"
    assert "\\" not in FB2CompilerService._safe_series_filename("Хроники\\Первый\\Второй")
