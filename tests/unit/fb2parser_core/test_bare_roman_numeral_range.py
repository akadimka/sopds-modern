"""Регрессия для `Pass2SeriesFilename._correct_series_number_from_filename()`
(Правило 4b, голый римский диапазон) — docs/quality-roadmap.md, баг №43.

Реальный случай (замечен пользователем в CSV): Клайв Баркер / "Книги
крови" — серия распозналась верно ("filename+meta_confirmed" /
"author-consensus"), но `series_number` остался пустым у обоих файлов:
"Книги крови. I–III.fb2" и "Книги крови. Запретное. IV-VI.fb2".
Правило 4 (голый арабский диапазон "Варяг 1-3.fb2") не видит римские
цифры вовсе — ни один из шести правил Pass2 не покрывал ГОЛЫЙ диапазон
римскими цифрами в конце имени файла (только одиночный "Том I" через
ключевое слово, Правило 6b).
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path


def _pass2():
    return Pass2SeriesFilename(config_path=_config_path())


def _rec(file_path, series_number=""):
    return BookRecord(
        file_path=file_path, file_title="T",
        metadata_authors="Клайв Баркер", proposed_author="Баркер Клайв", author_source="filename",
        metadata_series="Книги крови", proposed_series="Книги крови", series_source="filename+meta_confirmed",
        series_number=series_number,
    )


class TestBareRomanNumeralRangeRecognized:
    def test_dash_separated_roman_range(self):
        rec = _rec("Баркер Клайв - Книги крови. I–III.fb2")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "1-3"
        assert rec.series_number_source == "filename_bare_roman_range"

    def test_roman_range_after_extra_subtitle(self):
        rec = _rec("Баркер Клайв - Книги крови. Запретное. IV-VI.fb2")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "4-6"
        assert rec.series_number_source == "filename_bare_roman_range"

    def test_does_not_override_existing_series_number(self):
        rec = _rec("Баркер Клайв - Книги крови. I–III.fb2", series_number="9")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == "9"

    def test_reversed_or_equal_range_ignored(self):
        # Sanity: lo >= hi (мусорное совпадение) не порождает series_number.
        rec = _rec("Баркер Клайв - Книги крови. III-I.fb2")
        _pass2()._correct_series_number_from_filename([rec])
        assert rec.series_number == ""
