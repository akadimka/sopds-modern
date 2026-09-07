"""Регрессия для `Pass2SeriesFilename._resolve_hierarchical_flat_mismatch()`
— docs/quality-roadmap.md, баг №29.

Реальный случай (замечен пользователем в CSV): "Елисеев Алексей -
S-T-I-K-S. Пройти через туман 2/3/4.fb2" (без подзаголовка после номера)
теряли верно восстановленную серию "S-T-I-K-S\\Пройти через туман",
откатываясь на голое metadata_series/пустую серию — хотя "…7/8/9.
Континент.fb2" (с подзаголовком "Континент") корректно получали ту же
иерархию.

Причина: `_resolve_hierarchical_flat_mismatch()` (запускается ДО
`_detect_named_arcs()`) корректно повышает "плоские" тома 2/3/4 (proposed_
series="S-T-I-K-S", стем содержит "Пройти через туман N") до той же
иерархии "S-T-I-K-S\\Пройти через туман", что и уже иерархические тома
7/8/9 — но НЕ обновляет `series_source`, оставляя его "filename". Позже
`Pass4Consensus`'s "Collapsed unconfirmed filename subseries" шаг
доверяет только `series_source in {..., 'filename_named_arc'}` —
записи 2/3/4 с source="filename" не считаются подтверждёнными и
откатываются обратно на плоский корень "S-T-I-K-S".
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, series, source="filename", number=""):
    return BookRecord(
        file_path=path, file_title="T", metadata_authors="Алексей Елисеев",
        proposed_author="Елисеев Алексей", author_source="filename",
        metadata_series="", proposed_series=series, series_source=source,
        series_number=number,
    )


def _pass2():
    return Pass2SeriesFilename(config_path=_config_path())


class TestFlatPromotionMarksConfirmedArc:
    def _group(self):
        # Тома 7-9 уже иерархические (как после ШАГ 0 filename-экстракции
        # с "Континент" в качестве продолжения названия); 2-4 — плоские
        # "S-T-I-K-S", но стем содержит "Пройти через туман N".
        return [
            _rec("Елисеев Алексей - S-T-I-K-S. Пройти через туман 2.fb2", "S-T-I-K-S"),
            _rec("Елисеев Алексей - S-T-I-K-S. Пройти через туман 3.fb2", "S-T-I-K-S"),
            _rec("Елисеев Алексей - S-T-I-K-S. Пройти через туман 4.fb2", "S-T-I-K-S"),
            _rec("Елисеев Алексей - S-T-I-K-S. Пройти через туман 7. Континент.fb2",
                 "S-T-I-K-S\\Пройти через туман", number="7"),
            _rec("Елисеев Алексей - S-T-I-K-S. Пройти через туман 8. Континент.fb2",
                 "S-T-I-K-S\\Пройти через туман", number="8"),
        ]

    def test_promoted_flat_records_marked_as_confirmed_arc(self):
        records = self._group()
        _pass2()._resolve_hierarchical_flat_mismatch(records)

        for rec in records:
            assert rec.proposed_series == "S-T-I-K-S\\Пройти через туман"
            assert rec.series_source == "filename_named_arc", rec.file_path

    def test_survives_pass4_unconfirmed_subseries_collapse(self):
        # Полная проверка взаимодействия с реальным местом отката —
        # Pass4Consensus, тот же самый шаг, что откатывал серию раньше.
        records = self._group()
        _pass2()._resolve_hierarchical_flat_mismatch(records)

        Pass4Consensus(Logger(), settings=_pass2().settings).execute(records)

        for rec in records:
            assert rec.proposed_series == "S-T-I-K-S\\Пройти через туман", rec.file_path
