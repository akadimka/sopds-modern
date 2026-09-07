"""Регрессия для `RegenCSVService._postcheck_clear_universe_keyword_series()`
— docs/quality-roadmap.md, баг №27, часть 3.

Реальный случай: антология-вселенная "S-T-I-K-S" — десятки НИКАК не
связанных авторов пишут отдельные повести в общем сеттинге. Для
большинства из них (Горшенев Герман) `proposed_series` сводился к голому
"S-T-I-K-S" (+ иногда мусорный хвост "В Космосе"/"- Рассказы"/". Ганслер"/
"#") — не настоящая серия, а название сеттинга. Такое значение должно
очищаться целиком, если у него НЕТ подтверждённой именованной дуги
(`Корень\\ИмяАрки`, часть 2 того же бага) — та по-прежнему остаётся
настоящей серией.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(series, author="Автор Тест"):
    return BookRecord(
        file_path=f"{author} - Файл.fb2", file_title="T", metadata_authors=author,
        proposed_author=author, author_source="filename",
        metadata_series="", proposed_series=series, series_source="filename",
        series_number="1", series_number_source="filename",
    )


def _service():
    service = RegenCSVService(_config_path())
    service.settings.get_series_universe_keywords = lambda: ["S-T-I-K-S"]
    return service


class TestClearBareUniverseKeywordSeries:
    def _run(self, records):
        service = _service()
        service.records = records
        service._postcheck_clear_universe_keyword_series()
        return records

    def test_bare_keyword_and_variants_cleared(self):
        recs = [
            _rec("S-T-I-K-S"),
            _rec("S-T-I-K-S #"),
            _rec("S-T-I-K-S В Космосе"),
            _rec("S-T-I-K-S - Рассказы"),
            _rec("S-T-I-K-S. Ганслер"),
        ]
        self._run(recs)
        for r in recs:
            assert r.proposed_series == ""
            assert r.series_source == ""
            assert r.series_number == ""
            assert r.series_number_source == ""

    def test_confirmed_named_arc_root_stripped_not_cleared(self):
        # Реальная арка не удаляется целиком, а обрезается до имени дуги —
        # "S-T-I-K-S\Сварной" → "Сварной" (баг №30: без этого один и тот же
        # подцикл расходился на два представления в библиотеке одновременно).
        recs = [_rec("S-T-I-K-S\\Сварной")]
        self._run(recs)
        assert recs[0].proposed_series == "Сварной"

    def test_unrelated_series_containing_keyword_as_suffix_not_cleared(self):
        # "Магия S-T-I-K-S" — ключевое слово в КОНЦЕ, а не в начале —
        # самостоятельное название серии, не голая франшиза-обёртка.
        recs = [_rec("Магия S-T-I-K-S")]
        self._run(recs)
        assert recs[0].proposed_series == "Магия S-T-I-K-S"

    def test_no_keywords_configured_is_noop(self):
        recs = [_rec("S-T-I-K-S")]
        service = RegenCSVService(_config_path())
        service.settings.get_series_universe_keywords = lambda: []
        service.records = recs
        service._postcheck_clear_universe_keyword_series()
        assert recs[0].proposed_series == "S-T-I-K-S"


class TestClearRunsAfterFinalMetadataRescue:
    """Реальный случай (замечен пользователем): "Богданов. Цепные псы.fb2" —
    после первого фикса части 3 клир применялся ДО финального отката к
    metadata (`_postcheck_metadata_rescue()`, вызывается ПОВТОРНО в конце
    regenerate() как "последний резерв"). metadata_series="S-T-I-K-S"
    буквально совпадает с franchise-keyword — метаданный откат тут же
    подставлял её ОБРАТНО в только что очищенную запись. Порядок вызовов
    в regenerate() исправлен: клир теперь идёт ПОСЛЕДНИМ.
    """

    def test_metadata_rescue_then_clear_leaves_series_empty(self):
        rec = BookRecord(
            file_path="Богданов. Цепные псы.fb2", file_title="«S-T-I-K-S» Цепные псы",
            metadata_authors="Арт Богданов", proposed_author="Богданов Арт",
            author_source="filename+meta_expanded", metadata_series="S-T-I-K-S",
            proposed_series="", series_source="",
        )
        service = _service()
        service.records = [rec]

        # Воспроизводим ТОЧНЫЙ порядок вызовов из regenerate(): сначала
        # финальный откат к метаданным, потом клир голой франшизы.
        service._postcheck_metadata_rescue()
        assert rec.proposed_series == "S-T-I-K-S"  # откат отработал как и раньше

        service._postcheck_clear_universe_keyword_series()
        assert rec.proposed_series == ""
        assert rec.series_source == ""
