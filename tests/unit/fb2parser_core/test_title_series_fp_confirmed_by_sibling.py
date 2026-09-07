"""Регрессия для `RegenCSVService._postcheck_clear_title_series_fp()` —
обнаружено пользователем на реальной библиотеке (docs/quality-roadmap.md,
баг №28): "Волков. Город сестёр 1.fb2" (title="Город сестёр (СИ)" — не
сводится к "серия+номер") + "Волков. Город сестёр 2.fb2" (title="Город
сестёр 2" — формально СВОДИТСЯ к "серия+номер", ложно-положительный
паттерн). Том 2 терял верно извлечённую серию "Город сестёр", откатываясь
на голое metadata_series (название вселенной "Вселенная S-T-I-K-S", а не
серии конкретной книги).

Причина: `_confirmed_series_pairs` (защита "не очищать, если другой том
того же автора+серии уже подтверждён") строился ТОЛЬКО из
meta-подтверждённых источников (`filename+meta_confirmed`,
`folder_dataset` и т.п.) — том 1 (чисто filename-источник) никогда туда
не попадал, хотя сам факт, что его title НЕ сводится к паттерну
"серия+номер" (несёт независимый смысл — "(СИ)"), уже доказывает, что
серия настоящая, а не случайное совпадение.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, series, series_number, title, metadata_series="Вселенная S-T-I-K-S"):
    return BookRecord(
        file_path=path, file_title=title, metadata_authors="Юрий Николаевич Волков",
        proposed_author="Волков Юрий", author_source="filename+meta_expanded",
        metadata_series=metadata_series, proposed_series=series,
        series_source="filename", series_number=series_number,
        series_number_source="filename",
    )


class TestSiblingWithIndependentTitleConfirmsSeries:
    def test_second_volume_series_not_cleared(self):
        recs = [
            _rec("Волков. Город сестёр 1.fb2", "Город сестер", "1", "Город сестёр (СИ)"),
            _rec("Волков. Город сестёр 2.fb2", "Город сестер", "2", "Город сестёр 2"),
        ]
        service = RegenCSVService(_config_path())
        service.records = recs
        service._postcheck_clear_title_series_fp()

        assert recs[0].proposed_series == "Город сестер"
        assert recs[1].proposed_series == "Город сестер"
        assert recs[1].series_number == "2"

    def test_lone_fp_shaped_record_without_sibling_still_cleared(self):
        # Без подтверждающего тома-соседа поведение прежнее: одиночная
        # запись, чей title в точности "серия+номер", по-прежнему считается
        # ложным срабатыванием и очищается.
        recs = [
            _rec("Коу Джонатан - Номер 11.fb2", "Номер", "11", "Номер 11",
                 metadata_series=""),
        ]
        service = RegenCSVService(_config_path())
        service.records = recs
        service._postcheck_clear_title_series_fp()

        assert recs[0].proposed_series == ""
