"""Регрессия — docs/quality-roadmap.md, баг №30.

Реальный случай (замечен пользователем в CSV): "Елисеев Алексей -
S-T-I-K-S. Пройти через туман 2/3/4/7/8/9.fb2" получали иерархическую
серию "S-T-I-K-S\\Пройти через туман" (франшиза-обёртка + арка), а тома
5/6 того же самого цикла — голое "Пройти через туман" (без франшизы,
подтверждено иначе — см. баг №28). Один и тот же реальный подцикл
расходился на ДВА разных представления в библиотеке одновременно; базовая
безномерная книга цикла вообще оставалась без серии.

Фикс: `_postcheck_clear_universe_keyword_series()` теперь обрезает
franchise-обёртку не только у голых значений (баг №27, часть 3), но и у
иерархических: "Корень\\ИмяАрки" → "ИмяАрки", если "Корень" совпадает с
`series_universe_keywords`. `_postcheck_link_base_arc_book_into_named_
series()` вызывается ПОВТОРНО после этой обрезки и обобщён — принимает
корень уже ПЛОСКОЙ (не только иерархической) `filename_named_arc`-записи,
чтобы связать безномерную книгу-1 с теперь единым плоским именем цикла.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, series, series_source, number="", title=None):
    return BookRecord(
        file_path=path, file_title=title or path, metadata_authors="Алексей Елисеев",
        proposed_author="Елисеев Алексей", author_source="filename",
        metadata_series="", proposed_series=series, series_source=series_source,
        series_number=number, series_number_source=series_source if series_source else "",
    )


def _service(records):
    service = RegenCSVService(_config_path())
    service.settings.get_series_universe_keywords = lambda: ["S-T-I-K-S"]
    service.records = records
    return service


class TestUniverseKeywordUnifiesNamedArcWithFlatConfirmedSeries:
    def test_hierarchical_and_flat_variants_converge(self):
        recs = [
            _rec("Елисеев - S-T-I-K-S. Пройти через туман 2.fb2",
                 "S-T-I-K-S\\Пройти через туман", "filename_named_arc", "2",
                 title="Пройти через туман 2"),
            _rec("Елисеев - S-T-I-K-S. Пройти через туман 5.fb2",
                 "Пройти через туман", "filename", "5",
                 title="S-T-I-K-S. Пройти через туман V"),
            _rec("Елисеев - S-T-I-K-S. Пройти через туман.fb2",
                 "", "", "", title="Пройти через туман"),
        ]
        service = _service(recs)
        service._postcheck_clear_universe_keyword_series()
        service._postcheck_link_base_arc_book_into_named_series()

        assert recs[0].proposed_series == "Пройти через туман"
        assert recs[1].proposed_series == "Пройти через туман"
        assert recs[2].proposed_series == "Пройти через туман"
        assert recs[2].series_number == "1"
