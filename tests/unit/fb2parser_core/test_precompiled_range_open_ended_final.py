"""Регрессия для `FB2CompilerService._precompiled_range()` — docs/quality-roadmap.md,
баг №69 (причина №2 из анализа "Сердце Дракона всё плохо" — последняя из
трёх, после багов №66-№68).

Реальный случай (Клеванский Кирилл / "Сердце Дракона"): уже
скомпилированный файл "Часть III [Книги 16-финал].fb2" покрывает тома с
16 по последний реально существующий (20), но верхняя граница диапазона
в его имени — слово "финал", а не число. Регэксп диапазона
(`_BOOKS_BEFORE_RANGE_RE`) требует цифр с обеих сторон, поэтому файл не
распознавался как предкомпиляция вовсе — падал в обычную книгу на
позиции своего собственного `series_number` (порядковый номер "Части",
не диапазон томов), сталкиваясь и конфликтуя с настоящим томом на той же
позиции, а тома 16-20 оставались вне какой-либо группы компиляции.
"""
from pathlib import Path
from types import SimpleNamespace

from fb2parser_core.fb2_compiler import CompilationBook, FB2CompilerService

_SERIES = "Пламяборец"


def _book(filename: str, title: str) -> CompilationBook:
    record = SimpleNamespace(file_title=title, proposed_series=_SERIES, series_number="")
    return CompilationBook(
        record=record, abs_path=Path(filename), sort_key=(0, 0, 0, 0),
        sort_source="metadata", order_ambiguous=False,
    )


class TestOpenEndedFinalRangeResolvedAgainstKnownMax:
    def test_final_keyword_resolved_with_max_known_position(self):
        book = _book(f"{_SERIES}. Часть [Книги 16-финал].fb2", f"{_SERIES}. Часть")
        svc = FB2CompilerService()

        lo, hi = svc._precompiled_range(book, _SERIES, max_known_position=20)
        assert (lo, hi) == (16, 20)

    def test_final_keyword_without_max_known_position_stays_unrecognised(self):
        # Без сигнала о максимальной известной позиции безопаснее НЕ строить
        # диапазон вовсе, чем угадать неверную верхнюю границу.
        book = _book(f"{_SERIES}. Часть [Книги 16-финал].fb2", f"{_SERIES}. Часть")
        svc = FB2CompilerService()

        lo, hi = svc._precompiled_range(book, _SERIES)
        assert (lo, hi) == (0, 0)

    def test_final_keyword_with_max_known_position_not_greater_than_lo_stays_unrecognised(self):
        book = _book(f"{_SERIES}. Часть [Книги 16-финал].fb2", f"{_SERIES}. Часть")
        svc = FB2CompilerService()

        lo, hi = svc._precompiled_range(book, _SERIES, max_known_position=16)
        assert (lo, hi) == (0, 0)
