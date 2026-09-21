"""Регрессия для `Pass2SeriesFilename._correct_series_number_from_filename()`
(новое Правило 6c) — docs/quality-roadmap.md, баг №111.

Реальный случай (Михайлов Руслан, «Мир Вальдиры\\Герой крайних
рубежей»/«Сточные Воды Альгоры») — автор иногда называет файлы
аббревиатурой уже известной (из папки, folder_dataset) серии вместо
полного имени: «ГКР-1. Герой озёрного края.fb2», «СВА-2.
Темнотропье.fb2». Ни одно из существующих правил не покрывало этот
случай: Правило 1 (`_PREFIX_RE`) требует ведущую ЦИФРУ, а не буквы;
Правило 2 требует ЛИТЕРАЛЬНОЕ имя серии перед числом, а не аббревиатуру;
Правило 6 («Слово N») требует одно из фиксированных ключевых слов
(«Том»/«Часть»/«Книга» и т.п.), а не произвольную аббревиатуру. Когда
у файла ЕЩЁ И нет `<sequence>` в метаданных (частый случай — автор
проставляет номер только в части томов), `series_number` оставался
полностью пустым, хотя номер прямо виден в имени файла.

Правило 6c распознаёт ведущую аббревиатуру ТОЛЬКО когда она совпадает с
инициалами уже определённой (`proposed_series`, обычно folder_dataset —
высший приоритет) серии — это отличает настоящую аббревиатуру серии от
случайного набора заглавных букв в начале произвольного имени файла.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path


def _pass2():
    return Pass2SeriesFilename(config_path=_config_path())


def _rec(file_path, proposed_series, series_number=""):
    return BookRecord(
        file_path=file_path, file_title="T",
        metadata_authors="Михайлов Руслан", proposed_author="Михайлов Руслан",
        author_source="folder_dataset",
        metadata_series="", proposed_series=proposed_series, series_source="folder_dataset",
        series_number=series_number,
    )


class TestAbbreviationPrefixMatchingSeriesInitials:
    def test_gkr_abbreviation_recognized(self):
        rec = _rec("ГКР-1. Герой озёрного края .fb2", "Герой крайних рубежей")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "1"
        assert rec.series_number_source == "filename_abbrev_prefix"

    def test_sva_abbreviation_recognized(self):
        rec = _rec("СВА-2. Темнотропье.fb2", "Сточные Воды Альгоры")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "2"
        assert rec.series_number_source == "filename_abbrev_prefix"

    def test_does_not_override_existing_series_number(self):
        rec = _rec("ГКР-1. Герой озёрного края .fb2", "Герой крайних рубежей", series_number="9")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "9"

    def test_unrelated_abbreviation_not_matching_series_ignored(self):
        # Sanity: аббревиатура НЕ совпадает с инициалами серии — не
        # трогаем (иначе любой случайный набор заглавных букв в начале
        # имени файла ложно принимался бы за номер тома).
        rec = _rec("АБВ-3. Случайное совпадение.fb2", "Герой крайних рубежей")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == ""

    def test_no_proposed_series_ignored(self):
        # Sanity: без уже определённой серии сравнивать не с чем.
        rec = _rec("ГКР-1. Герой озёрного края .fb2", "")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == ""
