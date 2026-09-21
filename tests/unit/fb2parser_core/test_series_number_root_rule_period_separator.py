"""Регрессия для `Pass2SeriesFilename._correct_series_number_from_filename()`
(Правило 2, «SeriesRoot N. BookTitle») — docs/quality-roadmap.md, баг №109
(продолжение).

Реальный случай (Маркова Юлия, Михайловский Александр / "В закоулках
мироздания"): все файлы серии называются "В закоулках мироздания. N.
Название.fb2" — ТОЧКА сразу после корня серии, перед номером тома. Для
18 из 19 файлов это не имело значения — номер брался из метаданных
(`<sequence number>`). Но у тома 9 ("В закоулках мироздания. 9. В дни
Бородина.fb2") метаданные без `<sequence>` вовсе — единственный
источник номера — само имя файла. Правило 2 требовало пробел/дефис
сразу после корня серии — точка в этот класс не входила,
поэтому "мироздания." + " 9" вообще не матчилось. Том 9 остался без
номера — не попал в объединённую компиляцию с соседними томами и
синхронизировался как отдельный, безномерной файл (тот же класс
проблемы, что и с "Кроу" — см. предыдущую запись про `_TOM_WORD_RE`, но
здесь дыра в ДРУГОМ правиле).
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path


def _pass2():
    return Pass2SeriesFilename(config_path=_config_path())


def _rec(file_path, series="В закоулках мироздания"):
    return BookRecord(
        file_path=file_path, file_title="Т",
        metadata_authors="Юлия Маркова; Александр Михайловский",
        proposed_author="Маркова Юлия, Михайловский Александр", author_source="folder_dataset",
        metadata_series="", proposed_series=series, series_source="folder_dataset",
        series_number="",
    )


class TestPeriodAfterSeriesRootRecognized:
    def test_period_separator_recognized(self):
        rec = _rec("В закоулках мироздания. 9. В дни Бородина.fb2")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "9"
        assert rec.series_number_source == "filename_series_root"

    def test_matching_existing_number_left_as_is(self):
        # Sanity: если series_number уже совпадает (напр. из metadata,
        # просто с ведущим нулём) — источник не перезаписывается.
        rec = _rec("В закоулках мироздания. 9. В дни Бородина.fb2")
        rec.series_number = "9"
        rec.series_number_source = "metadata"
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "9"
        assert rec.series_number_source == "metadata"

    def test_space_separator_still_works(self):
        # Sanity: старый формат без точки ("Серия N.") не сломан.
        rec = _rec("В закоулках мироздания 9. В дни Бородина.fb2")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "9"
        assert rec.series_number_source == "filename_series_root"
