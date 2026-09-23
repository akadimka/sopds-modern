"""Регрессия для `Pass2SeriesFilename._correct_series_number_from_filename()`
(Правило 2, «SeriesRoot N. BookTitle») и
`RegenCSVService._extract_series_from_folder_name()` — docs/quality-
roadmap.md, баг №109 (продолжение).

Реальный случай (Седых Александр): папка "Хранитель 2 (Защитник тьмы)"
— арка №2 цикла "Хранитель" со спин-офф-названием "Защитник тьмы".
Все реальные файлы физически называются "Защитник тьмы N...", а не
"Хранитель 2 N...". `_extract_series_from_folder_name()` раньше слепо
отбрасывала "(Защитник тьмы)" как если бы это было имя автора
("Series (Author)" — самый частый паттерн в реальной библиотеке), давая
голое `proposed_series="Хранитель 2"`. Из-за этого Правило 2 (якорится
на `proposed_series`) искало "Хранитель 2 N." в имени файла — которого
там нет вовсе — и номер тома брался из противоречивых метаданных
(<sequence name="Хранитель (Седых)" number="5">, номер позиции в
РОДИТЕЛЬСКОМ цикле, а не внутри "Защитник тьмы"). Итог: пара файлов с
номерами 5 и 3 выглядела как несмежная (разрыв на позиции 4) и вообще
не группировалась в компиляции.
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _pass2():
    return Pass2SeriesFilename(config_path=_config_path())


def _rec(file_path, series_number=""):
    return BookRecord(
        file_path=file_path, file_title="Т",
        metadata_authors="Александр Седых", proposed_author="Седых Александр",
        author_source="folder_dataset", metadata_series="Хранитель (Седых)",
        proposed_series="Хранитель 2 (Защитник тьмы)", series_source="folder_dataset",
        series_number=series_number,
    )


class TestFolderNameKeepsAltNameInsteadOfTreatingItAsAuthor:
    def test_root_n_altname_kept_whole(self):
        svc = RegenCSVService(_config_path())
        assert svc._extract_series_from_folder_name(
            "Хранитель 2 (Защитник тьмы)"
        ) == "Хранитель 2 (Защитник тьмы)"

    def test_genuine_series_author_pattern_still_stripped(self):
        # Sanity: "Серия (Автор)" — самый частый паттерн в реальной
        # библиотеке — не должен быть задет фиксом.
        svc = RegenCSVService(_config_path())
        assert svc._extract_series_from_folder_name(
            "Защита Периметра (Абенд Эдвард)"
        ) == "Защита Периметра"

    def test_year_like_number_not_treated_as_arc_index(self):
        # Sanity: "Война 2020 (Марчук Николай)" — 2020 это часть НАЗВАНИЯ
        # серии (год), не номер арки; (Марчук Николай) — настоящий автор,
        # должен по-прежнему стрипаться.
        svc = RegenCSVService(_config_path())
        assert svc._extract_series_from_folder_name(
            "Война 2020 (Марчук Николай)"
        ) == "Война 2020"


class TestSeriesNumberCorrectedViaAltNameAnchor:
    def test_alt_name_anchor_recognized_when_root_anchor_absent(self):
        rec = _rec("Седых А. - Защитник тьмы 2. Тайны мира.fb2", series_number="5")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "2"
        assert rec.series_number_source == "filename_series_root"

    def test_second_volume_confirmed_by_own_metadata_unaffected(self):
        # Sanity: том, чья метадата уже согласована с именем файла
        # ("Защитник тьмы 3", <sequence number="3">), не должен быть
        # затронут — Правило 2 просто подтверждает то же значение.
        rec = _rec("Седых А. - Защитник тьмы 3.fb2", series_number="3")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "3"


class TestFullPairFormsCompilableGroup:
    def test_pair_groups_with_correct_range(self):
        records = [
            _rec("Седых А. - Защитник тьмы 2. Тайны мира.fb2", series_number="5"),
            _rec("Седых А. - Защитник тьмы 3.fb2", series_number="3"),
        ]
        _pass2()._correct_series_number_from_filename(records)

        svc = FB2CompilerService()
        groups = svc.find_groups(records, Path("."))
        matching = [g for g in groups if g.series == "Хранитель 2 (Защитник тьмы)"]
        assert len(matching) == 1, [g.series for g in groups]
        g = matching[0]
        assert not g.cleanup_only
        assert g.volume_range == "2-3"
