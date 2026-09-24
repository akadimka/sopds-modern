"""Регрессия для `FB2CompilerService._STEM_NUM_RE` и `_determine_sort_key()`'s
"Серия N. Подзаголовок. Том M" ветка — docs/quality-roadmap.md, баг №109.

Реальный случай (замечен пользователем в превью компиляции, Test1):
Шарапов Валерий / "Контрразведка" — серия из 17 файлов раскалывалась на
3 несвязанные группы компиляции ("1-5", "8-10", "14-17") вместо одной
"1-17". Метаданные `<sequence>` отсутствовали у части файлов (06, 07,
08, 11, 12) — для них компилятор ищет номер тома напрямую в имени
файла, встроенном ПОСЛЕ имени автора ("Шарапов В.Г. - 06.Название...").

Причина 1 (`_STEM_NUM_RE`): ветка для номера, встроенного НЕ в самом
начале строки (после имени автора), требовала ОБЯЗАТЕЛЬНЫЙ пробел
после разделителя (`\\s` вместо `\\s*`) — асимметрично с веткой для
номера В САМОМ начале строки, где пробел опциональный. "06.Нелегал"
(без пробела после точки) не матчился этой веткой вовсе — в отличие от
"08. Чекистский" (с пробелом), которому "повезло" по чистой
случайности. У НЕ совпавших файлов (06, 07, 11) число не находилось
вовсе, поиск проваливался до менее надёжных эвристик (год издания как
позиция) — серия рвалась на куски.

Причина 2 (`_kw_after`/`_VOLUME_KEYWORDS_RE` в ветке "Серия N.
Подзаголовок. Том M"): то же самое число-после-ключевого-слова, что
уже чинилось в `_extract_inline_volume_number()` (см. соседний тест
`test_inline_volume_number_rejects_year_like_values.py`) — но здесь
ДРУГОЙ, отдельный вызов `_VOLUME_KEYWORDS_RE.search()` напрямую, без
проверки на правдоподобие числа, был пропущен при первом фиксе.
"""
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord


def _rec(file_path, title, series_number="", metadata_series=""):
    return BookRecord(
        file_path=file_path, file_title=title, metadata_authors="Шарапов Валерий",
        proposed_author="Шарапов Валерий", author_source="folder_dataset",
        metadata_series=metadata_series, proposed_series="Контрразведка",
        series_source="folder_dataset", series_number=series_number,
        series_number_source="metadata" if series_number else "",
    )


class TestStemNumREEmbeddedNumberNoSpaceRequired:
    def test_embedded_number_without_trailing_space_recognised(self):
        svc = FB2CompilerService()
        stem = "Шарапов  В.Г. - 06.Нелегал из контрразведки - 2024"
        m = svc._STEM_NUM_RE.match(stem) or svc._STEM_NUM_RE.search(stem)
        assert m is not None
        assert next(g for g in m.groups() if g is not None) == "06"

    def test_embedded_number_with_trailing_space_still_recognised(self):
        # Sanity: старый рабочий случай (пробел после точки) не задет.
        svc = FB2CompilerService()
        stem = "Шарапов  В.Г. - 08. Чекистский невод - 2024"
        m = svc._STEM_NUM_RE.match(stem) or svc._STEM_NUM_RE.search(stem)
        assert m is not None
        assert next(g for g in m.groups() if g is not None) == "08"

    def test_full_series_no_longer_splits_on_missing_metadata_number(self, tmp_path):
        rec = _rec(
            "Шарапов  В.Г. - 06.Нелегал из контрразведки - 2024.fb2",
            "Нелегал из контрразведки",
        )
        svc = FB2CompilerService()
        sort_key, sort_source, ambiguous, label = svc._determine_sort_key(
            rec, Path(rec.file_path)
        )
        assert sort_key == (0, 6, 0, 0)
        assert sort_source == "filename"


class TestVolumeKeywordAfterMetaNumRejectsYearLikeValues:
    def test_year_after_series_subtitle_keyword_not_treated_as_inner_volume(self):
        # "Серия N. Подзаголовок. <ключевое слово> <год>" — год не должен
        # приниматься как номер внутреннего тома этой же внешней позиции.
        rec = _rec(
            "05.Секретная часть - 2023.fb2", "Секретная часть",
            series_number="5", metadata_series="Контрразведка",
        )
        svc = FB2CompilerService()
        sort_key, sort_source, ambiguous, label = svc._determine_sort_key(
            rec, Path(rec.file_path)
        )
        assert sort_key == (0, 5, 0, 0)

    def test_genuine_subtitle_volume_keyword_still_recognised(self):
        # Sanity: реальный случай из существующего докстрока —
        # "Война великого бога 2. Внутренняя война. Том 1" → (0,2,1,0).
        rec = _rec(
            "2.Война великого бога 2. Внутренняя война. Том 1.fb2",
            "Война великого бога 2. Внутренняя война. Том 1",
            series_number="2", metadata_series="Война великого бога",
        )
        svc = FB2CompilerService()
        sort_key, sort_source, ambiguous, label = svc._determine_sort_key(
            rec, Path(rec.file_path)
        )
        assert sort_key == (0, 2, 1, 0)
