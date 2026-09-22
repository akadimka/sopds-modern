"""Регрессия для `FB2CompilerService._precompiled_range()` — docs/
quality-roadmap.md, баг №109 (продолжение).

Реальный случай (замечен пользователем в превью компилятора: серия
"Демон" из "Седых Александр" полностью отсутствовала в списке —
пользователь спросил "сборку Демон видишь?"): папка "Демон - завершён"
содержит "Демон 3. Проводник хаоса.fb2" (series_number="3"), "Демон 4.
Магистр хаоса.fb2" (series_number="4") и уже готовый омнибус "Демон.
Трилогия.fb2" (без своего series_number, покрывает 1-3 через слово
"Трилогия").

Первопричина — arc-шорткат в `_precompiled_range()` (паттерн "SeriesName
N. Rest", мотивирующий случай — "Брия 1. Книга Длинного Солнца 1-2",
где "1." — маркер арки, а "1-2" после него — её ВНУТРЕННИЙ диапазон):
он матчил ЛЮБОЙ файл с именем вида "Демон N. Название" и сразу
возвращал (N, N), не проверяя, есть ли вообще внутренний диапазон
после — то есть точно так же матчил ОБЫЧНЫЙ, простой том плоской серии
("Демон 3. Проводник хаоса" — никакой арки, просто третий том). Итог:
`find_groups()`'s проверка "_all_precompiled" видела ВСЕ 3 файла серии
как "уже готовые предкомпиляции" (Трилогия=1-3, "3"=(3,3), "4"=(4,4)),
а "_any_multi" (Трилогия имеет lo<hi) решала, что это набор независимых
arc-подсерий — и пропускала ВСЮ группу "Демон" целиком, без единой
строки в превью компилятора (даже без cleanup_only).
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import FB2CompilerService


def _rec(path, title, series_number):
    return BookRecord(
        file_path=path, file_title=title,
        metadata_authors="Седых Александр", proposed_author="Седых Александр",
        author_source="folder_dataset", metadata_series="Демон",
        proposed_series="Демон", series_source="folder_dataset",
        series_number=series_number,
    )


class TestOwnConfirmedPositionNotTreatedAsArcMarker:
    def test_plain_volume_with_own_series_number_is_not_precompiled(self):
        svc = FB2CompilerService()
        rec = _rec("Седых А. - Демон 3. Проводник хаоса.fb2", "Проводник хаоса", "3")
        book = svc._make_book(rec, Path("."))
        assert svc._precompiled_range(book, "Демон") == (0, 0)

    def test_genuine_arc_without_own_position_still_detected(self):
        # Sanity: если у книги НЕТ собственного series_number вовсе (не
        # совпадает с угаданным arc-номером — потому что его попросту
        # нет), arc-шорткат по-прежнему срабатывает как раньше — фикс
        # трогает только случай "собственная позиция ЕСТЬ и СОВПАДАЕТ
        # с угаданным номером".
        svc = FB2CompilerService()
        rec = _rec(
            "Автор Тест - Брия 1. Книга Длинного Солнца.fb2",
            "Книга Длинного Солнца", "",
        )
        rec.proposed_series = "Брия"
        book = svc._make_book(rec, Path("."))
        assert svc._precompiled_range(book, "Брия") == (1, 1)

    def test_full_demon_bucket_forms_one_extending_group(self):
        records = [
            _rec("Седых А. - Демон 3. Проводник хаоса.fb2", "Проводник хаоса", "3"),
            _rec("Седых А. - Демон 4. Магистр хаоса.fb2", "Магистр хаоса", "4"),
            _rec("Седых А. - Демон. Трилогия.fb2", "Демон. Трилогия", "1"),
        ]
        svc = FB2CompilerService()
        groups = svc.find_groups(records, Path("."))

        demon_groups = [g for g in groups if g.series == "Демон"]
        assert len(demon_groups) == 1, [g.series for g in groups]
        g = demon_groups[0]
        assert not g.cleanup_only
        assert g.volume_range == "1-4"
