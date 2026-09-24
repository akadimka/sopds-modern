"""Регрессия для `FB2CompilerService._extract_inline_volume_number()` —
docs/quality-roadmap.md, баг №109.

Реальный случай (замечен пользователем в превью компиляции, Test1):
Шарапов Валерий / "Контрразведка" — "Шарапов В.Г. - 05.Секретная часть -
2023.fb2". Заголовок книги "Секретная часть" сам по себе не содержит
слова-маркера тома, но `_VOLUME_KEYWORDS_RE` ищет по СТЕМУ файла целиком,
где после слова "часть" (здесь — обычное слово в названии книги, "Секретная
часть" = "тайное подразделение", а не структурный маркер!) в некотором
отдалении стоит год издания "- 2023" — regex захватывал "2023" как номер
внутреннего тома. Итог: `sort_key=(0, 5, 2023, 0)`,
`sort_source='series_number_inner_tom'`, `volume_label='5.2023'` — том 5
раскалывался на отдельную позицию "5.2023", а серия из 17 файлов теряла
непрерывность диапазона в превью компиляции (`volume_range` вместо "1-5"
показывал "1-5.2023").

Починено: числа ≥ 501 после ключевого слова (том/часть/книга и т.п.)
больше не принимаются как номер тома — та же граница (500), что уже
используется для арифметического варианта («Книга 12+1») чуть выше по
коду. Год издания (1900+) заведомо больше этой границы.
"""
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord


class TestInlineVolumeNumberRejectsYearLikeValues:
    def test_year_after_incidental_title_word_not_treated_as_volume(self):
        svc = FB2CompilerService()
        result = svc._extract_inline_volume_number(
            "Секретная часть",
            "Шарапов  В.Г. - 05.Секретная часть - 2023",
        )
        assert result is None

    def test_genuine_volume_keyword_still_recognised(self):
        # Sanity: реальный маркер тома ("Том 2") — не задет фиксом.
        svc = FB2CompilerService()
        result = svc._extract_inline_volume_number("Заголовок. Том 2", "")
        assert result == 2

    def test_determine_sort_key_gives_plain_series_number_not_inner_tom(self):
        rec = BookRecord(
            file_path="Шарапов  В.Г. - 05.Секретная часть - 2023.fb2",
            file_title="Секретная часть", metadata_authors="Шарапов Валерий",
            proposed_author="Шарапов Валерий", author_source="folder_dataset",
            metadata_series="Контрразведка", proposed_series="Контрразведка",
            series_source="folder_dataset", series_number="5",
            series_number_source="metadata",
        )
        svc = FB2CompilerService()
        sort_key, sort_source, ambiguous, label = svc._determine_sort_key(
            rec, Path(rec.file_path)
        )
        assert sort_key == (0, 5, 0, 0)
        assert sort_source == "series_number"
        assert label == "5"
