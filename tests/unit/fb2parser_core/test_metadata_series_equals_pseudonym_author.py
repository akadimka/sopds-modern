"""Регрессия для `Pass2SeriesFilename._postpass_metadata_fallback()` — docs/
quality-roadmap.md, баг №40.

Реальный случай (замечен пользователем в CSV): серия "Анонимус"
(Александр Мазин, псевдоним "Анонимус"; исторические детективы про
Загорского) — 18 томов. 15 из 18 файлов получили серию нормально
(`<sequence name="АНОНИМУС">` в верхнем регистре не совпадает буквально
с иначе испорченным именем автора в этих изданиях — "Анонимyс" с
латинской "y"). Но у томов 8, 13, 17 автор в метаданных записан БЕЗ
опечатки ("Анонимус", как есть) — и `<sequence name="Анонимус">`
оказывается буквально идентичен имени автора. Guard "серия == автор,
это ошибка конвертера" срабатывал и для этих трёх, хотя `<sequence
number="8"/>` из ТОЙ ЖЕ метадаты недвусмысленно подтверждал реальный,
согласованный номер тома — то есть это не опечатка, а действительно
одноимённая с псевдонимом серия.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path


def _pass2():
    return Pass2SeriesFilename(config_path=_config_path())


def _rec(number, metadata_series, metadata_author, series_number_source="metadata"):
    return BookRecord(
        file_path=f"Анонимус\\Анонимус {number:02d} - Дело.fb2", file_title="Дело",
        metadata_authors=metadata_author, proposed_author=metadata_author,
        author_source="metadata", metadata_series=metadata_series, proposed_series="",
        series_source="", series_number=str(number),
        series_number_source=series_number_source,
    )


def _folder_sibling():
    # Баг №109 (продолжение): _postpass_metadata_fallback() теперь не
    # придумывает серию из голой metadata_series для папки, которая
    # никогда не давала папочного сигнала. В реальном случае 15 из 18
    # томов "Анонимус" получают серию через folder_dataset нормально —
    # этот сосед в той же папке воспроизводит такой сигнал, иначе
    # изолированный тест из одной записи не отражал бы реальный контекст.
    return BookRecord(
        file_path="Анонимус\\Анонимус 01 - Дело.fb2", file_title="Дело",
        metadata_authors="Анонимус", proposed_author="Анонимус",
        author_source="folder_dataset", metadata_series="Анонимус",
        proposed_series="Анонимус", series_source="folder_dataset",
        series_number="1", series_number_source="metadata",
    )


class TestSeriesEqualsAuthorButHasConfirmedVolumeNumber:
    def test_series_kept_when_metadata_confirms_volume_number(self):
        # <sequence name="Анонимус" number="8"/> — имя буквально совпадает с
        # автором, но number пришёл из ТОЙ ЖЕ метадаты — не опечатка.
        rec = _rec(8, "Анонимус", "Анонимус")
        _pass2()._postpass_metadata_fallback([rec, _folder_sibling()])
        assert rec.proposed_series == "Анонимус"
        assert rec.series_source == "metadata"

    def test_series_still_cleared_without_confirmed_volume_number(self):
        # Sanity: без согласованного номера ИЗ МЕТАДАТЫ (например, номер
        # достался из имени файла, а не из <sequence>) старое поведение
        # (вероятная ошибка конвертера — дублирование имени автора в поле
        # серии) сохраняется — series остаётся пустой.
        rec = _rec(8, "Анонимус", "Анонимус", series_number_source="filename_prefix_pattern")
        _pass2()._postpass_metadata_fallback([rec, _folder_sibling()])
        assert rec.proposed_series == ""
        assert rec.series_source == ""

    def test_no_series_without_any_folder_signal_in_folder(self):
        # Баг №109 (продолжение): если у ВСЕХ файлов папки серия не из
        # папки (нет ни одного folder_dataset/folder_hierarchy соседа) —
        # это уже не описанный в баге №40 сценарий (15/18 через папку),
        # а файл без какого-либо папочного сигнала вообще — серия не
        # придумывается из голой metadata_series.
        rec = _rec(8, "Анонимус", "Анонимус")
        _pass2()._postpass_metadata_fallback([rec])
        assert rec.proposed_series == ""
        assert rec.series_source == ""
