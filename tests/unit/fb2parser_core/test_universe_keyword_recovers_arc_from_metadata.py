"""Регрессия — docs/quality-roadmap.md, баг №34.

Реальный случай (замечен пользователем в CSV): "Лазарев Василий -
S-T-I-K-S #25. И пришёл Лесник! 19.fb2" … "…S-T-I-K-S #32. И пришёл
Лесник! 26.fb2" — у автора собственная нумерация эпизодов через решётку
("#25".."#32"), которую filename-экстракция принимала за корень серии
("S-T-I-K-S #25" и т.п.), теряя настоящее имя арки "И пришёл Лесник!"
целиком. Оно оставалось ТОЛЬКО в `metadata_series` ("S-T-I-K-S. И пришёл
Лесник!", одинаковое у всех файлов) — без восстановления запись просто
очищалась бы как голая франшиза (баг №27, часть 3).
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, title, meta_series="S-T-I-K-S. И пришёл Лесник!", series="S-T-I-K-S #25"):
    return BookRecord(
        file_path=path, file_title=title, metadata_authors="Василий Лазарев",
        proposed_author="Лазарев Василий", author_source="filename",
        metadata_series=meta_series, proposed_series=series, series_source="filename",
    )


def _service(records):
    service = RegenCSVService(_config_path())
    service.settings.get_series_universe_keywords = lambda: ["S-T-I-K-S"]
    service.records = records
    return service


class TestRecoverArcFromMetadataWhenFilenameExtractionFails:
    def test_arc_recovered_when_confirmed_by_two_siblings(self):
        recs = [
            _rec("Лазарев - S-T-I-K-S #25. И пришёл Лесник! 19.fb2",
                 "И пришел Лесник! 19", series="S-T-I-K-S #25"),
            _rec("Лазарев - S-T-I-K-S #26. И пришёл Лесник! 20.fb2",
                 "И пришел Лесник! 20", series="S-T-I-K-S #26"),
        ]
        service = _service(recs)
        service._postcheck_clear_universe_keyword_series()

        assert recs[0].proposed_series == "И пришёл Лесник!"
        assert recs[0].series_number == "19"
        assert recs[0].series_source == "metadata_arc_consensus"
        assert recs[1].proposed_series == "И пришёл Лесник!"
        assert recs[1].series_number == "20"

    def test_lone_record_without_confirming_sibling_still_cleared(self):
        # Одиночная запись без подтверждающих соседей (одна и та же арка
        # должна встретиться у ≥2 файлов автора) — по-прежнему очищается
        # целиком, как обычный одноразовый рассказ (баг №27/№30).
        recs = [
            _rec("Лазарев - S-T-I-K-S #99. Одиночный рассказ.fb2",
                 "Одиночный рассказ", meta_series="S-T-I-K-S. Одиночный рассказ",
                 series="S-T-I-K-S #99"),
        ]
        service = _service(recs)
        service._postcheck_clear_universe_keyword_series()

        assert recs[0].proposed_series == ""
        assert recs[0].series_source == ""


class TestRecoverArcByTitleMatchAgainstConfirmedSiblingArc:
    """Продолжение бага №34: "Лазарев Василий - S-T-I-K-S #9. И пришёл
    Лесник! 3.fb2" — та же арка "И пришёл Лесник!" (уже подтверждена у
    других файлов автора через metadata_arc_consensus), но у ЭТОГО файла
    metadata_series вообще пуста (нет тега <sequence>) — восстановить
    через метаданные нечем. Franchise-метка здесь не префиксом, а
    скобочным суффиксом в title: "И пришел Лесник! 3 (S-T-I-K-S)".
    Последний шанс: сравнить title (без скобочной метки и номера) с уже
    подтверждённым именем арки того же автора.
    """

    def test_title_with_parenthetical_franchise_suffix_matched_to_confirmed_arc(self):
        recs = [
            # Уже подтверждённые (как после metadata_arc_consensus).
            BookRecord(
                file_path="Лазарев - S-T-I-K-S #25. И пришёл Лесник! 19.fb2",
                file_title="И пришел Лесник! 19", metadata_authors="Василий Лазарев",
                proposed_author="Лазарев Василий", author_source="filename",
                metadata_series="", proposed_series="И пришёл Лесник!",
                series_source="metadata_arc_consensus", series_number="19",
            ),
            # Без metadata_series, franchise-метка скобочным суффиксом.
            BookRecord(
                file_path="Лазарев - S-T-I-K-S #9. И пришёл Лесник! 3.fb2",
                file_title="И пришел Лесник! 3 (S-T-I-K-S)", metadata_authors="Василий Лазарев",
                proposed_author="Лазарев Василий", author_source="filename",
                metadata_series="", proposed_series="S-T-I-K-S #9",
                series_source="filename",
            ),
        ]
        service = _service(recs)
        service._postcheck_clear_universe_keyword_series()

        assert recs[1].proposed_series == "И пришёл Лесник!"
        assert recs[1].series_number == "3"


class TestRecoverArcWhenMetadataSeriesHasNoFranchisePrefix:
    """Баг №53: "Гришанин Дмитрий - S-T-I-K-S. Рихтовщик\\7. Дорога без
    начала.fb2" … "…8. Пепел дорог.fb2" — папка даёт плоское
    "S-T-I-K-S. Рихтовщик" в proposed_series, но `metadata_series`
    (тег <sequence> в самих файлах) содержит просто "Рихтовщик" — БЕЗ
    префикса-франшизы "S-T-I-K-S." вообще. Старый код искал арку только
    в metadata_series, начинающейся с ключевого слова франшизы — для
    голого "Рихтовщик" такого совпадения нет, восстановление не
    срабатывало, и серия у ОБОИХ файлов стиралась целиком — вместе с
    ПРАВИЛЬНО извлечённым из имени файла номером тома (7/8), который к
    вопросу "это голая франшиза" не имеет отношения.
    """

    def test_bare_metadata_series_without_franchise_prefix_recovered(self):
        recs = [
            BookRecord(
                file_path="Гришанин Дмитрий\\S-T-I-K-S. Рихтовщик\\7. Дорога без начала.fb2",
                file_title="Дорога без начала", metadata_authors="Дмитрий Гришанин",
                proposed_author="Гришанин Дмитрий", author_source="folder_dataset",
                metadata_series="Рихтовщик", proposed_series="S-T-I-K-S. Рихтовщик",
                series_source="folder_dataset", series_number="7",
                series_number_source="filename_prefix",
            ),
            BookRecord(
                file_path="Гришанин Дмитрий\\S-T-I-K-S. Рихтовщик\\8. Пепел дорог.fb2",
                file_title="Пепел дорог", metadata_authors="Дмитрий Гришанин",
                proposed_author="Гришанин Дмитрий", author_source="folder_dataset",
                metadata_series="Рихтовщик", proposed_series="S-T-I-K-S. Рихтовщик",
                series_source="folder_dataset", series_number="8",
                series_number_source="filename_prefix",
            ),
        ]
        service = _service(recs)
        service._postcheck_clear_universe_keyword_series()

        for rec, expected_num in zip(recs, ("7", "8")):
            assert rec.proposed_series == "Рихтовщик"
            assert rec.series_source == "metadata_arc_consensus"
            # Номер тома, уже верно извлечённый из имени файла, не должен
            # затираться при восстановлении серии из метаданных.
            assert rec.series_number == expected_num
            assert rec.series_number_source == "filename_prefix"
