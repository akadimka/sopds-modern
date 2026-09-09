"""Регрессия для `RegenCSVService._postcheck_clear_universe_keyword_series()`
— унификация серии с хвостовым квалификатором ("Серия (фанфик)") против
голого имени серии у другой записи того же автора.

Реальный случай (Вязовский Алексей / "Режим бога"): 12 файлов одной саги,
но `<sequence name="...">` в метаданных FB2 расставлен непоследовательно —
часть томов помечена "Режим бога (фанфик)", часть — просто "Режим бога".
Без унификации это две разные серии в каталоге/компиляторе вместо одной.

`series_universe_keywords` уже существовал для похожей задачи (франшиза-
обёртка типа "S-T-I-K-S"), но матчил только ПРЕФИКС/голое имя — хвостовой
квалификатор в СКОБКАХ на конце имени серии не обрабатывал вовсе.
"""
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(series, author="Вязовский Алексей", title="T"):
    return BookRecord(
        file_path=f"{author} - {title}.fb2", file_title=title, metadata_authors=author,
        proposed_author=author, author_source="metadata",
        metadata_series=series, proposed_series=series, series_source="metadata",
        series_number="1", series_number_source="metadata",
    )


def _service(keywords):
    service = RegenCSVService(_config_path())
    service.settings.get_series_universe_keywords = lambda: keywords
    return service


class TestQualifierSuffixUnifiedWithBareSiblingSeries:
    def _run(self, records, keywords=("(фанфик)",)):
        service = _service(list(keywords))
        service.records = records
        service._postcheck_clear_universe_keyword_series()
        return records

    def test_qualified_series_unified_with_bare_sibling(self):
        recs = [
            _rec("Режим бога"),
            _rec("Режим бога (фанфик)"),
            _rec("Режим бога"),
            _rec("Режим бога (фанфик)"),
        ]
        self._run(recs)
        assert all(r.proposed_series == "Режим бога" for r in recs)

    def test_bracket_form_also_recognized(self):
        recs = [_rec("Режим бога"), _rec("Режим бога [фанфик]")]
        self._run(recs)
        assert recs[1].proposed_series == "Режим бога"

    def test_keyword_without_own_parens_still_works(self):
        # Ключевое слово задано БЕЗ собственных скобок ("фанфик", как
        # франшизы вроде "S-T-I-K-S") — тоже должно матчиться в скобках
        # на конце имени серии.
        recs = [_rec("Режим бога"), _rec("Режим бога (фанфик)")]
        self._run(recs, keywords=("фанфик",))
        assert recs[1].proposed_series == "Режим бога"

    def test_no_bare_sibling_left_untouched(self):
        # Нет ни одной записи ТОГО ЖЕ автора с голым именем серии —
        # квалификатор может быть значимой частью уникального названия,
        # не трогаем.
        recs = [_rec("Режим бога (фанфик)"), _rec("Режим бога (фанфик)")]
        self._run(recs)
        assert all(r.proposed_series == "Режим бога (фанфик)" for r in recs)

    def test_different_author_bare_series_not_used_as_match(self):
        recs = [
            _rec("Режим бога", author="Другой Автор"),
            _rec("Режим бога (фанфик)", author="Вязовский Алексей"),
        ]
        self._run(recs)
        assert recs[1].proposed_series == "Режим бога (фанфик)"

    def test_hierarchical_series_not_touched(self):
        recs = [
            _rec("Режим бога"),
            _rec("Режим бога (фанфик)\\Подсерия"),
        ]
        self._run(recs)
        assert recs[1].proposed_series == "Режим бога (фанфик)\\Подсерия"
