"""Сборник с тем же именем, не входящий в сборку (сервер 2026-10-22).

Сборник из новых томов писался «Автор - Серия (Дилогия).fb2» рядом с уже
лежащим «… (Дилогия).fb2.zip» (прежний сборник, сжатый «Сжать»), которого в
группе не было: в библиотеке и каталоге — два сборника одной серии (189 пар
после синхронизации ЛитРПГ). Несжатый тёзка молча перезаписывался. Теперь
сборка такой группы не выполняется: тома и прежний файл остаются как были.
"""
import zipfile
from pathlib import Path

import pytest

from fb2parser_core.fb2_compiler import CompilationBook, CompilationGroup, FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

TITLES = {1: "Первый", 2: "Второй"}


def _volume(folder: Path, n: int) -> Path:
    path = folder / f"Автор Тестов - Серия {n}. {TITLES[n]}.fb2"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0" '
        'xmlns:l="http://www.w3.org/1999/xlink"><description><title-info>'
        '<genre>sf</genre><author><first-name>Автор</first-name><last-name>Тестов</last-name></author>'
        f'<book-title>{TITLES[n]}</book-title><lang>ru</lang>'
        f'<sequence name="Серия" number="{n}"/></title-info></description>'
        f'<body><section><title><p>Глава</p></title><p>текст {n}</p></section></body></FictionBook>',
        encoding="utf-8")
    return path


def _group(folder: Path) -> CompilationGroup:
    books = []
    for n in TITLES:
        path = _volume(folder, n)
        record = BookRecord(file_path=path.name, file_title=TITLES[n], metadata_authors="Автор Тестов",
                            proposed_author="Автор Тестов", author_source="metadata", metadata_series="Серия",
                            proposed_series="Серия", series_source="metadata", metadata_genre="sf",
                            series_number=str(n))
        books.append(CompilationBook(record=record, abs_path=path, sort_key=(0, n, 0, 0),
                                     sort_source="series_number", order_ambiguous=False, volume_label=str(n)))
    return CompilationGroup(author="Автор Тестов", series="Серия", books=books,
                            order_determined=True, volume_range="1-2")


@pytest.fixture
def output_name(tmp_path):
    """Имя сборника, которое даёт компилятор для этой группы."""
    folder = tmp_path / "probe"
    folder.mkdir()
    result = FB2CompilerService().compile_group(_group(folder), folder)
    assert result.success, result.error
    return result.output_path.name


@pytest.mark.parametrize("existing_suffix", [".zip", ""])
def test_same_named_file_outside_group_is_left_alone(tmp_path, output_name, existing_suffix):
    folder = tmp_path / "lib"
    folder.mkdir()
    group = _group(folder)
    existing = folder / (output_name + existing_suffix)
    if existing_suffix:
        with zipfile.ZipFile(existing, "w") as z:
            z.writestr(output_name, "<FictionBook>прежний сборник</FictionBook>")
    else:
        existing.write_text("<FictionBook>прежний сборник</FictionBook>", encoding="utf-8")
    before = existing.read_bytes()

    result = FB2CompilerService().compile_group(group, folder, delete_sources=True)

    assert not result.success and existing.name in result.error
    assert existing.read_bytes() == before
    assert sorted(p.name for p in folder.iterdir()) == sorted([existing.name] + [b.abs_path.name for b in group.books])
