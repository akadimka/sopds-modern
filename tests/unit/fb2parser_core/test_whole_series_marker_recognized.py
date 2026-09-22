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


class TestWholeSeriesMarkerIgnoredWhenBookHasOwnDefinitePosition:
    """Реальный случай (Панфилов Василий / "Улан") — docs/quality-roadmap.md,
    баг №109 (продолжение). Том 2 несёт ИСПОРЧЕННЫЙ, похоже скрейпленный с
    сайта, `file_title`: "Улан. Наследие предков – Василий Панфилов |
    Альтернативная история, попаданец в XVIII век, военная САГА" — слово
    "сага" (genre-тег из промо-текста, никак не связан со структурой файла)
    ложно матчило `_WHOLE_SERIES_MARKERS`, и файл (обычный, единственный
    том — 5.3 МБ, как и остальные 3 тома по отдельности, НЕ омнибус) считался
    "уже готовой компиляцией всей серии 1-4" — том 1/3/4 помечались на
    УДАЛЕНИЕ как "дубликаты", реальная потеря данных при выполнении.

    Различающий признак: у настоящего омнибуса (баг №70, "Далин - Цикл
    «Город Внизу»") НЕТ собственного, однозначно определённого номера тома
    — он получает позицию только ЧЕРЕЗ этот самый критерий 1.7. У тома 2
    "Улан" уже ЕСТЬ собственная, надёжная позиция "2" (найдена по ведущей
    цифре в имени файла, filename_prefix) — книга с уже известной
    определённой собственной позицией не может ОДНОВРЕМЕННО быть "всей
    серией в одном файле".
    """

    def test_book_with_own_definite_number_not_treated_as_whole_series(self):
        record = SimpleNamespace(
            file_title=(
                'Улан. Наследие предков – Василий Панфилов | Альтернативная '
                'история, попаданец в XVIII век, военная сага'
            ),
            proposed_series="Улан", series_number="2",
        )
        book = CompilationBook(
            record=record, abs_path=Path("2. Улан. Наследие предков.fb2"),
            sort_key=(0, 2, 0, 0), sort_source="filename", order_ambiguous=False,
        )
        svc = FB2CompilerService()

        lo, hi = svc._precompiled_range(book, "Улан", max_known_position=4)
        assert (lo, hi) == (0, 0)

    def test_real_shape_end_to_end_no_data_loss(self, tmp_path):
        from fb2parser_core.passes.pass1_read_files import BookRecord

        def _rec(path, num, title):
            return BookRecord(
                file_path=path, file_title=title, metadata_authors="Панфилов Василий",
                proposed_author="Панфилов Василий", author_source="folder_dataset",
                metadata_series="Улан", proposed_series="Улан",
                series_source="folder_dataset", series_number=num,
                series_number_source="filename_prefix",
            )

        records = [
            _rec("1. Улан. Танец на лезвии клинка.fb2", "1", "Улан. Танец на лезвии клинка"),
            _rec("2. Улан. Наследие предков.fb2", "2",
                 'Улан. Наследие предков – Василий Панфилов | Альтернативная '
                 'история, попаданец в XVIII век, военная сага'),
            _rec("3. Улан. Венедская держава.fb2", "3", "Улан. Венедская держава"),
            _rec("4. Улан. Небо славян.fb2", "4", "Улан. Небо славян"),
        ]

        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        matches = [g for g in groups if g.author == "Панфилов Василий"]
        assert len(matches) == 1
        group = matches[0]
        # НЕ cleanup_only — реальная новая компиляция всех 4 томов,
        # ни один не должен уйти в duplicate_paths на удаление.
        assert group.cleanup_only is False
        assert len(group.books) == 4
        assert not group.duplicate_paths


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
