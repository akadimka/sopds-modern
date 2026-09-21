"""Регрессия для `Pass2SeriesFilename._correct_series_number_from_filename()`
(Правило 6, «Слово N») — docs/quality-roadmap.md, баг №109 (продолжение).

Реальный случай (Михайлов Руслан / "Мир Вальдиры\\Кроу"): том 2 ("Кроу 2.
Суровые земли (авт. вариант книги).fb2") не несёт `<sequence number>` в
метаданных — его единственный источник номера — совпадение "Кроу" +
цифра в начале имени файла (Правило 2, source='filename_series_root').

При синхронизации несжатого (ещё не скомпилированного) тома
`_build_target_filename()` (synchronization.py) переименовывает файл в
"Михайлов Руслан - Кроу. Азы мастерства т. 3.fb2" — свой
АББРЕВИИРОВАННЫЙ маркер тома "т. N" в хвосте (тот же формат, что и в
итоговом суффиксе компиляции "(т. N-M)"). Когда `auto_compile_library()`
позже пере-сканирует ЭТОТ, уже переименованный файл (библиотека, а не
исходная папка) — между "Кроу" и номером теперь стоит целое название
("Азы мастерства"), Правило 2 (даже расширенное точкой-разделителем,
см. `test_series_number_root_rule_period_separator.py`) не матчит;
`_TOM_WORD_RE` (Правило 6) требовал ПОЛНОЕ слово ("том"/"часть"/...) —
аббревиатуру "т." не распознавал вовсе. Итог: том терял номер при
повторном скане и не попадал в объединённую компиляцию с соседями.

Фикс: `_TOM_WORD_RE` также распознаёт "т." — свой же формат маркера,
уже используемый в suffix-е компилятора/синхронизации.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path


def _pass2():
    return Pass2SeriesFilename(config_path=_config_path())


def _rec(file_path, series="Кроу"):
    return BookRecord(
        file_path=file_path, file_title="Т",
        metadata_authors="Руслан Михайлов", proposed_author="Михайлов Руслан",
        author_source="filename",
        metadata_series=series, proposed_series=series, series_source="filename+meta_confirmed",
        series_number="",
    )


class TestAbbreviatedTomMarkerRecognized:
    def test_abbreviated_tom_suffix_recognized(self):
        rec = _rec("Михайлов Руслан - Кроу. Азы мастерства т. 3.fb2")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "3"
        assert rec.series_number_source == "filename_word_number"

    def test_matching_existing_number_left_as_is(self):
        # Sanity: если series_number уже совпадает (напр. из metadata) —
        # источник не перезаписывается.
        rec = _rec("Михайлов Руслан - Кроу. Азы мастерства т. 3.fb2")
        rec.series_number = "3"
        rec.series_number_source = "metadata"
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "3"
        assert rec.series_number_source == "metadata"

    def test_full_word_still_recognized_unaffected(self):
        # Sanity: полное слово "том" — старое поведение не сломано.
        rec = _rec("Автор - Серия. Название. Том 7.fb2", series="Серия")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "7"
