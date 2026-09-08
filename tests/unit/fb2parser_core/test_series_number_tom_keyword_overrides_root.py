"""Регрессия для `Pass2SeriesFilename._correct_series_number_from_filename()`
(Правило 2, «SeriesRoot N. BookTitle») — обнаружено на реальной библиотеке
(Вязовский Алексей / "15 ножевых").

4 разных файла одной папки (folder_dataset series="15 ножевых"):
  "Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том 2.fb2"
  "Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том 3.fb2"
  "Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том 4.fb2"
  "Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том 5.fb2"

У всех четырёх совпадает "15 ножевых 1." (число "1" тут — номер
части/арки саги, а не тома), и различаются они только явным "Том N" в
хвосте. Правило 2 искало "КореньСерии + число" по всему стему и находило
"1" из "15 ножевых 1." РАНЬШЕ, чем добиралось до настоящего "Том N" —
из-за этого все 4 файла получали одинаковый series_number="1", хотя
реальные позиции — 2, 3, 4, 5.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path


def _pass2():
    return Pass2SeriesFilename(config_path=_config_path())


def _rec(file_path, title):
    return BookRecord(
        file_path=file_path, file_title=title,
        metadata_authors="Алексей Викторович Вязовский; Сергей Линник",
        proposed_author="Вязовский Алексей", author_source="folder_dataset",
        metadata_series="15 ножевых", proposed_series="15 ножевых",
        series_source="folder_dataset", series_number="", series_number_source="",
    )


class TestExplicitTomKeywordBeatsAmbiguousRootNumber:
    def test_each_volume_gets_its_own_number_from_tom_keyword(self):
        recs = [
            _rec(
                f"Вязовский, Линник - 15 ножевых 1. Пятнадцать ножевых. Том {n}.fb2",
                f"Пятнадцать ножевых. Том {n}",
            )
            for n in (2, 3, 4, 5)
        ]
        _pass2()._correct_series_number_from_filename(recs)

        assert [r.series_number for r in recs] == ["2", "3", "4", "5"]
        assert all(r.series_number_source == "filename_word_number" for r in recs)

    def test_root_number_still_used_when_no_tom_keyword_present(self):
        # Sanity: без Том-keyword правило по-прежнему берёт число рядом
        # с названием серии — поведение не сломано целиком.
        rec = _rec("Ученики Ворона 3. Финал.fb2", "Финал")
        rec.proposed_series = "Ученики Ворона"
        _pass2()._correct_series_number_from_filename([rec])

        assert rec.series_number == "3"
        assert rec.series_number_source == "filename_series_root"
