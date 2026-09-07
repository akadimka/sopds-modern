"""Регрессия для `Pass2SeriesFilename._detect_named_arcs()` — обнаружено
пользователем на реальной библиотеке (docs/quality-roadmap.md, баг №27,
часть 2): "Галеев Эдуард - S-T-I-K-S. Сварной-1.fb2" … "…Сварной-5.fb2"
(франшиза "S-T-I-K-S" уже извлечена как proposed_series голым корнем без
арки) — имя арки "Сварной" вообще не распознавалось. Существующий паттерн
именованных дуг (`_ARC_RE_ANY`) требует "Корень N. Заголовок" (пробел
перед числом, точка+пробел+текст ПОСЛЕ числа) — здесь же номер идёт через
ДЕФИС сразу после имени арки и завершает имя файла без подзаголовка.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, author, series, series_source="filename"):
    return BookRecord(
        file_path=path, file_title="T", metadata_authors=author,
        proposed_author=author, author_source="filename",
        metadata_series="", proposed_series=series,
        series_source=series_source,
    )


def _pass2():
    return Pass2SeriesFilename(config_path=_config_path())


class TestDashSuffixedNamedArc:
    def test_svarnoy_arc_recognized_from_dash_number(self):
        records = [
            _rec(f"Галеев Эдуард - S-T-I-K-S. Сварной-{n}.fb2",
                 "Галеев Эдуард", "S-T-I-K-S")
            for n in range(1, 6)
        ]
        _pass2()._detect_named_arcs(records)

        for n, rec in enumerate(records, 1):
            assert rec.proposed_series == "S-T-I-K-S\\Сварной"
            assert rec.series_number == str(n)
            assert rec.series_source == "filename_named_arc"

    def test_single_dash_numbered_file_is_not_treated_as_arc(self):
        # Единичное совпадение (нет повторения имени арки) — не должно
        # считаться подтверждённой дугой.
        records = [
            _rec("Автор Тест - Франшиза. Одиночка-1.fb2", "Автор Тест", "Франшиза"),
        ]
        _pass2()._detect_named_arcs(records)
        assert records[0].proposed_series == "Франшиза"
        assert records[0].series_source == "filename"

    def test_different_authors_do_not_get_merged_into_same_arc(self):
        records = [
            _rec("Галеев Эдуард - S-T-I-K-S. Сварной-1.fb2",
                 "Галеев Эдуард", "S-T-I-K-S"),
            _rec("Другой Автор - S-T-I-K-S. Одиночка-1.fb2",
                 "Другой Автор", "S-T-I-K-S"),
        ]
        _pass2()._detect_named_arcs(records)
        # У обоих — по одному вхождению своей арки → ни один не подтверждён.
        assert records[0].proposed_series == "S-T-I-K-S"
        assert records[1].proposed_series == "S-T-I-K-S"


class TestSpaceSuffixedNamedArcWithSameNumberAnnotation:
    """Реальный случай (docs/quality-roadmap.md, баг №31): "Елисеев Алексей
    - S-T-I-K-S. Богиня Смерти 2.fb2" + "…Богиня Смерти 2 (дополнение).fb2"
    — пробел (не дефис) перед номером, ОБА тома с ОДИНАКОВЫМ номером
    (основной том + его доп. издание/расширение) — раньше не распознавалось
    вообще: паттерн требовал дефис, а совпадающие номера трактовались как
    "дубли одного номера — не реальная дуга".
    """

    def test_same_number_with_parenthetical_annotation_confirms_arc(self):
        records = [
            _rec("Елисеев Алексей - S-T-I-K-S. Богиня Смерти 2.fb2",
                 "Елисеев Алексей", "S-T-I-K-S"),
            _rec("Елисеев Алексей - S-T-I-K-S. Богиня Смерти 2 (дополнение).fb2",
                 "Елисеев Алексей", "S-T-I-K-S"),
        ]
        _pass2()._detect_named_arcs(records)

        for rec in records:
            assert rec.proposed_series == "S-T-I-K-S\\Богиня Смерти"
            assert rec.series_number == "2"
            assert rec.series_source == "filename_named_arc"


class TestSoleNumberedVolumeConfirmedByUnnumberedBase:
    """Реальный случай (docs/quality-roadmap.md, баг №33): "Калинин Максим
    - S-T-I-K-S. Офис.fb2" (безномерная база) + "…Офис 2.fb2" (единственный
    численный том) — всего 2 файла, только ОДИН с цифрой, что не набирает
    требуемых для "прохода 0" ≥2 численных вхождений. Но пара "база + том
    N" сама по себе достаточное подтверждение дуги: два РЕАЛЬНЫХ файла с
    одинаковым именем "Офис" после франшизы-корня.
    """

    def test_base_plus_single_numbered_volume_confirms_arc(self):
        records = [
            _rec("Калинин Максим - S-T-I-K-S. Офис.fb2",
                 "Калинин Максим", "S-T-I-K-S"),
            _rec("Калинин Максим - S-T-I-K-S. Офис 2.fb2",
                 "Калинин Максим", "S-T-I-K-S"),
        ]
        _pass2()._detect_named_arcs(records)

        assert records[0].proposed_series == "S-T-I-K-S\\Офис"
        assert records[0].series_number == "1"
        assert records[0].series_source == "filename_named_arc"
        assert records[1].proposed_series == "S-T-I-K-S\\Офис"
        assert records[1].series_number == "2"
        assert records[1].series_source == "filename_named_arc"

    def test_single_numbered_volume_without_base_stays_unconfirmed(self):
        # Sanity: без базового файла-соседа одиночный численный том
        # по-прежнему не подтверждается (старое поведение не сломано).
        records = [
            _rec("Автор Тест - Франшиза. Одиночка 2.fb2", "Автор Тест", "Франшиза"),
        ]
        _pass2()._detect_named_arcs(records)
        assert records[0].proposed_series == "Франшиза"
        assert records[0].series_source == "filename"
