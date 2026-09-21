"""Регрессия для `SynchronizationService._build_folder_structure()`/
`_move_files()` — docs/quality-roadmap.md, баг №109 (продолжение).

Реальный случай (замечен пользователем): баг №109/п.1 убрал
автоматическую склейку "Мир Вальдиры\\<Подсерия>" в `proposed_series`
для подсерий без собственного ведущего номера ("Герой крайних
рубежей", "Цикл Люца" и т.п.) — это правильно защищает нумерацию
позиций компилятора от коллизий между независимыми сериями (см.
`test_dominant_folder_position_collision_guard.py`), но как побочный
эффект убирало папку "Мир Вальдиры" и из ФИЗИЧЕСКОГО пути при
синхронизации в библиотеку — пользователь явно попросил сохранить эту
папку-группировку на диске, даже когда она больше не общее
пространство нумерации.

Решение: `regen_csv.py` теперь пишет отброшенный корень в отдельное
поле `record.series_display_root` (не в `proposed_series` — чтобы не
трогать нумерацию), а синхронизация оборачивает `series`/`subseries`
в него при построении целевого пути.
"""
from pathlib import Path

from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.synchronization import SynchronizationService


def _make_service(tmp_path):
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.logger = Logger()
    svc.log_callback = None
    svc.db_path = tmp_path / "nonexistent.db"
    svc.stats = {}
    return svc


def _rec(file_path, proposed_series, series_display_root=""):
    return BookRecord(
        file_path=file_path, file_title=Path(file_path).stem,
        metadata_authors="Михайлов Руслан", proposed_author="Михайлов Руслан",
        author_source="folder_dataset",
        metadata_series="", proposed_series=proposed_series, series_source="folder_dataset",
        series_number="", metadata_genre="sf_fantasy",
        series_display_root=series_display_root,
    )


class TestDisplayRootWrapsSeriesInTargetPath:
    def test_display_root_nests_series_folder(self, tmp_path):
        svc = _make_service(tmp_path)
        records = [_rec("ГКР-1.fb2", "Герой крайних рубежей", "Мир Вальдиры")]
        folder_structure, _ = svc._build_folder_structure(records)
        genre, author, display_root, series, subseries = folder_structure["ГКР-1.fb2"]
        assert display_root == "Мир Вальдиры"
        assert series == "Герой крайних рубежей"

    def test_no_display_root_unaffected(self, tmp_path):
        # Sanity: обычная (не сплющенная) серия без display_root не меняется.
        svc = _make_service(tmp_path)
        records = [_rec("1.fb2", "Обычная серия")]
        folder_structure, _ = svc._build_folder_structure(records)
        genre, author, display_root, series, subseries = folder_structure["1.fb2"]
        assert display_root == ""
        assert series == "Обычная серия"
