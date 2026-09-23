"""Регрессия для `Pass4Consensus.execute()`'s "FILENAME SEQUENCE +
METADATA DUAL CONFIRMATION" — docs/quality-roadmap.md, баг №109,
"хрупкость каскада" часть 7.

Тот же класс бага, что и в частях 4/6 этого размышления: локальный набор
`STRONG_SERIES_SOURCES = {"filename+meta_confirmed", "filename",
"folder_dataset", "metadata"}` сравнивал `series_source` ТОЧНЫМ
совпадением — молча исключал `'filename_named_arc'` (реальное значение,
18 записей в tests/data/regen_library) и любые составные значения.

Реальный эффект НЕ был полной потерей данных (как в части 6) — другой,
более общий консенсус-механизм ниже по пайплайну обычно всё равно
исправлял текст серии. Но исправлял его с НЕВЕРНОЙ, менее точной
атрибуцией `series_source` (напр. `'folder_meta_consensus'` вместо
`'filename+meta_confirmed'`) — series_source используется downstream
(приоритет источников, decision_log, будущие consumers
`series_source_rank()`), так что неверная атрибуция — тоже реальная
проблема, просто более тонкая.

Починено: заменено на `_is_strong_series_source()` — функция на основе
`evidence.series_source_rank()`, с явным отдельным включением `'metadata'`
(который делит один ранг с `'consensus'` в `series_source_rank()`, но
`'consensus'` — часть `LOW_CONFIDENCE` и не должен считаться сильным
источником для этого механизма).
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_core.settings_manager import SettingsManager
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, series, source, meta_series="", candidate=""):
    return BookRecord(
        file_path=path, file_title="Т", metadata_authors="Автор Авторов",
        proposed_author="Автор Авторов", author_source="filename",
        metadata_series=meta_series, proposed_series=series,
        series_source=source, extracted_series_candidate=candidate,
    )


def _settings():
    return SettingsManager(_config_path())


class TestDualConfirmationRecognizesFilenameNamedArcAsStrong:
    def test_named_arc_source_confirms_low_confidence_sibling(self):
        records = [
            _rec("Автор Авторов - Переписать сценарий 2.fb2",
                 "Переписать сценарий", "filename_named_arc",
                 candidate="Переписать сценарий"),
            _rec("Автор Авторов - Переписать сценарий.fb2",
                 "Неверное значение", "author-consensus",
                 meta_series="Переписать сценарий"),
        ]
        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert records[1].proposed_series == "Переписать сценарий"
        assert records[1].series_source == "filename+meta_confirmed"

    def test_bare_metadata_still_counts_as_strong_but_consensus_does_not(self):
        # Sanity: 'metadata' (явно перечислен отдельно) по-прежнему
        # засчитывается; 'consensus' (в LOW_CONFIDENCE) — не должен стать
        # "сильным" источником через дырявую ранговую эквивалентность с
        # 'metadata' в series_source_rank().
        from fb2parser_core import evidence
        assert evidence.series_source_rank("metadata") == \
            evidence.series_source_rank("consensus")

        records = [
            _rec("Автор Авторов - Отдельная история 2.fb2",
                 "Отдельная история", "metadata",
                 candidate="Отдельная история"),
            _rec("Автор Авторов - Отдельная история.fb2",
                 "Неверное значение", "author-consensus",
                 meta_series="Отдельная история"),
        ]
        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert records[1].proposed_series == "Отдельная история"
        assert records[1].series_source == "filename+meta_confirmed"
