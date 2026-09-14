"""Регрессия для `FB2CompilerService._precompiled_range()` — docs/quality-roadmap.md,
баг №70.

Реальный случай (Далин Макс / "Город Внизу"): "Далин - Цикл «Город
внизу» (СИ).fb2" — уже готовый омнибус, физически содержащий все 5
повестей серии (313 КБ ≈ сумма 5 отдельных файлов, 68+23+164+24+38 КБ),
без своего номера тома и без диапазона в скобках. Слово "Цикл" не
задаёт число томов (в отличие от "Трилогия"/"Пенталогия" в
`_SERIES_WORDS`), поэтому файл не проходил ни один из числовых критериев
`_precompiled_range()` и полностью выпадал из `find_groups()`: не член
группы, не дубликат — просто молча оставался на диске нетронутым.
"""
from pathlib import Path
from types import SimpleNamespace

from fb2parser_core.fb2_compiler import CompilationBook, FB2CompilerService

_SERIES = "Город Внизу"


def _book(filename: str, title: str) -> CompilationBook:
    record = SimpleNamespace(file_title=title, proposed_series=_SERIES, series_number="")
    return CompilationBook(
        record=record, abs_path=Path(filename), sort_key=(0, 0, 0, 0),
        sort_source="metadata", order_ambiguous=False,
    )


class TestWholeSeriesMarkerResolvedAgainstKnownMax:
    def test_cycle_keyword_resolved_with_max_known_position(self):
        book = _book(f'Далин - Цикл «{_SERIES}» (СИ).fb2', f'Цикл "{_SERIES}" (СИ)')
        svc = FB2CompilerService()

        lo, hi = svc._precompiled_range(book, _SERIES, max_known_position=5)
        assert (lo, hi) == (1, 5)

    def test_cycle_keyword_without_max_known_position_stays_unrecognised(self):
        book = _book(f'Далин - Цикл «{_SERIES}» (СИ).fb2', f'Цикл "{_SERIES}" (СИ)')
        svc = FB2CompilerService()

        lo, hi = svc._precompiled_range(book, _SERIES)
        assert (lo, hi) == (0, 0)


class TestWholeSeriesGroupCleansUpDuplicateVolumes:
    def test_real_shape_end_to_end(self, tmp_path):
        from fb2parser_core.passes.pass1_read_files import BookRecord

        def _rec(path, num=""):
            return BookRecord(
                file_path=path, file_title=path, metadata_authors="Далин Макс",
                proposed_author="Далин Макс", author_source="folder_dataset",
                metadata_series=_SERIES, proposed_series=_SERIES,
                series_source="folder_dataset", series_number=num,
                series_number_source="metadata" if num else "",
            )

        records = [
            _rec(f'Далин - Цикл «{_SERIES}» (СИ).fb2'),
        ] + [_rec(f"Далин 0{n} Том.fb2", str(n)) for n in range(1, 6)]

        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        matches = [g for g in groups if g.author == "Далин Макс"]
        assert len(matches) == 1
        group = matches[0]
        assert group.cleanup_only is True
        assert group.kept_paths and group.kept_paths[0].name == f'Далин - Цикл «{_SERIES}» (СИ).fb2'
        assert len(group.duplicate_paths or []) == 5
