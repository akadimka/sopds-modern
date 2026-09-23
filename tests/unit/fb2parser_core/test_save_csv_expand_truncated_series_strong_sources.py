"""Регрессия для `RegenCSVService._save_csv()`'s "expand truncated
metadata series" пост-чек — docs/quality-roadmap.md, баг №109,
"хрупкость каскада" часть 6.

Реальный случай: у автора один файл несёт полное название серии из
надёжного источника (не только `'filename'`/`'folder_dataset'`, но и
`'filename_named_arc'`, составные вида `'folder_dataset+subfolder_
hierarchy'` — 18 записей каждого вида в tests/data/regen_library), а
другой файл того же автора — усечённое название из `metadata`
(`series_source == 'metadata'`). Пост-чек должен расширить усечённое
значение до полного, если полное взято из достаточно надёжного
источника.

Раньше проверка "достаточно ли надёжен источник" была литеральным
набором `_STRONG = {'filename+meta_confirmed', 'filename',
'folder_dataset', 'folder_hierarchy', 'folder_meta_consensus'}` —
точное совпадение молча исключало `'filename_named_arc'` (не совпадает
буквально с `'filename'`) и любые составные значения вида
`'folder_dataset+subfolder_hierarchy'` (не совпадает буквально с
`'folder_dataset'`) — та же природа бага, что и в
`pass3_series_normalize.py` (часть 4 этого размышления). Заменено на
`evidence.series_source_rank(...) >= 20` (файловый тир и выше) —
корректно распознаёт и filename_*-варианты, и составные значения через
базовую часть до `'+'`.
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(file_path, proposed_author, proposed_series, series_source):
    return BookRecord(
        file_path=file_path, file_title="Т", metadata_authors=proposed_author,
        proposed_author=proposed_author, author_source="folder_dataset",
        metadata_series="", proposed_series=proposed_series,
        series_source=series_source,
    )


def _run_save_csv(records, tmp_path):
    service = RegenCSVService(_config_path())
    service.records = records
    service.output_csv = tmp_path / "regen.csv"
    service._save_csv()
    return records


class TestExpandTruncatedSeriesRecognizesAllStrongSources:
    def test_filename_named_arc_source_expands_truncated_metadata(self, tmp_path):
        records = _run_save_csv([
            _rec("a.fb2", "Иванов Иван", "Хроники Сиалы Полная", "filename_named_arc"),
            _rec("b.fb2", "Иванов Иван", "Хроники", "metadata"),
        ], tmp_path)
        assert records[1].proposed_series == "Хроники Сиалы Полная"
        assert records[1].series_source == "metadata+author_expanded"

    def test_composite_folder_dataset_suffix_expands_truncated_metadata(self, tmp_path):
        records = _run_save_csv([
            _rec("a.fb2", "Петров Петр", "Режиссёр Советского Союза", "folder_dataset+subfolder_hierarchy"),
            _rec("b.fb2", "Петров Петр", "Режиссёр", "metadata"),
        ], tmp_path)
        assert records[1].proposed_series == "Режиссёр Советского Союза"
        assert records[1].series_source == "metadata+author_expanded"

    def test_plain_filename_and_folder_dataset_still_work_as_before(self, tmp_path):
        # Sanity: значения, что и раньше работали (баг их не затрагивал),
        # не должны быть задеты рефакторингом.
        records = _run_save_csv([
            _rec("a.fb2", "Сидоров Сидор", "Путь домой полностью", "filename"),
            _rec("b.fb2", "Сидоров Сидор", "Путь домой", "metadata"),
        ], tmp_path)
        assert records[1].proposed_series == "Путь домой полностью"
        assert records[1].series_source == "metadata+author_expanded"

    def test_metadata_source_itself_not_used_as_expansion_source(self, tmp_path):
        # Sanity: сам 'metadata' не должен становиться "надёжным" источником
        # для этого расширения — иначе одна плохая metadata-запись могла бы
        # "расширить" другую. Имя файла содержит "Тайна", чтобы не попасть
        # под ДРУГОЙ, не связанный с этим тестом пост-чек ("singleton
        # metadata series not found in file path").
        records = _run_save_csv([
            _rec("Кузнецов Кузьма - Тайна полностью.fb2", "Кузнецов Кузьма", "Тайна полностью", "metadata"),
            _rec("Кузнецов Кузьма - Тайна.fb2", "Кузнецов Кузьма", "Тайна", "metadata"),
        ], tmp_path)
        assert records[1].proposed_series == "Тайна"
        assert records[1].series_source == "metadata"
