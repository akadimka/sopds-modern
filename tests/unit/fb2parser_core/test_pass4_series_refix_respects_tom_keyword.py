"""Регрессия для `Pass4Consensus.execute()` — "финальный рефикс"
series_number из имени файла (см. `filename_series_refix` в
pass4_consensus.py) откатывал уже верно найденный номер тома обратно к
неоднозначному числу рядом с именем серии.

Реальный случай (Вязовский Алексей / "15 ножевых"): 4 файла

  "Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том 2.fb2"
  "Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том 3.fb2"
  "Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том 4.fb2"
  "Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том 5.fb2"

У всех четырёх есть верный `<sequence number="2..5">` в самих метаданных
FB2 (series_number_source='metadata') — но полный прогон пайплайна
переводит серию в иерархическую форму "15 ножевых\\Пятнадцать ножевых"
(`_detect_named_arcs`, source='filename_named_arc'), и "финальный рефикс"
в Pass4Consensus заново ищет "ИмяСерии N" в стеме — находит общий для всех
4 файлов "15 ножевых **1**" (число части/арки, не тома) и перезаписывает
им уже верный series_number из метаданных, схлопывая все 4 тома в
одинаковую "1". Проверка должна сработать независимо от того, ОТКУДА взят
текущий корректный номер (metadata или собственная filename_word_number
эвристика pass2_series_filename.py, см.
test_series_number_tom_keyword_overrides_root.py) — раз он подтверждён
явным "Том N" в имени файла, трогать его нельзя.
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_web.fb2parser_bridge import _config_path


def _settings():
    from fb2parser_core.settings_manager import SettingsManager
    return SettingsManager(_config_path())


def _rec(file_path, title, series_number, series_number_source, proposed_series):
    return BookRecord(
        file_path=file_path, file_title=title,
        metadata_authors="Алексей Викторович Вязовский; Сергей Линник",
        proposed_author="Вязовский Алексей", author_source="folder_dataset",
        metadata_series="15 ножевых", proposed_series=proposed_series,
        series_source="filename_named_arc" if '\\' in proposed_series else "folder_dataset",
        series_number=series_number, series_number_source=series_number_source,
    )


class TestFinalRefixDoesNotClobberTomKeywordNumber:
    def test_metadata_sourced_numbers_survive_final_refix(self):
        # Реальный сценарий: номера верны из <sequence> метаданных FB2, а
        # серия уже разложена на иерархическую арку ("15 ножевых\Заголовок").
        records = [
            _rec(
                f"Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том {n}.fb2",
                f"Пятнадцать ножевых. Том {n}", str(n), "metadata",
                "15 ножевых\\Пятнадцать ножевых",
            )
            for n in (2, 3, 4, 5)
        ]
        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert [r.series_number for r in records] == ["2", "3", "4", "5"]

    def test_filename_word_number_sourced_numbers_survive_final_refix(self):
        records = [
            _rec(
                f"Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том {n}.fb2",
                f"Пятнадцать ножевых. Том {n}", str(n), "filename_word_number",
                "15 ножевых",
            )
            for n in (2, 3, 4, 5)
        ]
        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert [r.series_number for r in records] == ["2", "3", "4", "5"]
