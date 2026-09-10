"""Регрессия для `FB2CompilerService.find_groups()` — docs/quality-
roadmap.md, баг №52.

Реальный случай (замечен пользователем в превью компилятора): Гришанин
Дмитрий / "Безликие" — папка содержит "1. Безликие.fb2" (настоящий том
1, номер из имени файла) и "Мах-недоучка.fb2" (СОВЕРШЕННО ДРУГОЕ
произведение без единого номера, `order_ambiguous=True`) — оба просто
физически лежат в одной папке автора. Превью показывало ОДНУ группу
компиляции из 2 файлов с суффиксом "в 1 книге" — единственный
одиночный пронумерованный том сливался в одну группу с полностью
неопределённой книгой только потому, что оба лежат в одной папке.

Тот же принцип, что и в Баге №47 (не компилировать книги без реального
номера/позиции) — здесь применяется к смешанному случаю "один
номерной том + один полностью неопределённый", который прежняя защита
не покрывала (та защищала только бакеты, где ВСЕ книги неопределены).
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import FB2CompilerService


def _rec(file_path, title, series_number=""):
    return BookRecord(
        file_path=file_path, file_title=title,
        metadata_authors="Дмитрий Гришанин", proposed_author="Гришанин Дмитрий",
        author_source="folder_dataset", metadata_series="Безликие", proposed_series="Безликие",
        series_source="folder_dataset", series_number=series_number,
        series_number_source="filename_prefix" if series_number else "",
    )


class TestLoneNumberedVolumeNotMergedWithFullyAmbiguousBook:
    def test_bezlikie_and_makh_nedouchka_produce_no_compilation_group(self):
        records = [
            _rec("Гришанин Дмитрий\\Безликие\\1. Безликие.fb2", "Безликие", series_number="1"),
            _rec("Гришанин Дмитрий\\Безликие\\Мах-недоучка.fb2", "Мах-недоучка"),
        ]
        svc = FB2CompilerService()
        groups = svc.find_groups(records, Path("."))

        bezlikie_groups = [g for g in groups if g.series == "Безликие"]
        assert bezlikie_groups == []

    def test_real_run_of_numbered_volumes_still_compiles(self):
        # Sanity: обычная, полностью пронумерованная серия по-прежнему
        # компилируется как раньше — фикс не тронул нормальный случай.
        records = [
            _rec("Автор Тест\\Серия\\1. Первый том.fb2", "Первый том", series_number="1"),
            _rec("Автор Тест\\Серия\\2. Второй том.fb2", "Второй том", series_number="2"),
        ]
        for r in records:
            r.proposed_author = "Автор Тест"
            r.metadata_authors = "Автор Тест"
            r.proposed_series = "Серия"
            r.metadata_series = "Серия"

        svc = FB2CompilerService()
        groups = svc.find_groups(records, Path("."))
        matches = [g for g in groups if g.series == "Серия"]
        assert len(matches) == 1
        assert len(matches[0].books) == 2
