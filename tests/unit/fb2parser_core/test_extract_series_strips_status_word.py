"""Регрессия для `RegenCSVService._extract_series_from_folder_name()` —
docs/quality-roadmap.md, баг №109 (продолжение).

Реальный случай (Седых Александр / "Артефактор - завершён", "Демон -
завершён", "Повелитель - завершён"): папки серий помечены пометкой
завершённости цикла через дефис ("Серия - завершён"). Пометка через
скобки ("Серия (завершён)") уже корректно стрипалась существующей
логикой (fallback "всё до открывающей скобки") — но дефисный вариант
protekaл в proposed_series целиком ("Артефактор - завершен" вместо
просто "Артефактор"), т.к. слово статуса не входит в название серии.
"""
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path


def _svc():
    return RegenCSVService(_config_path())


class TestDashStatusWordStrippedFromSeriesName:
    def test_zavershen_yo_form_stripped(self):
        assert _svc()._extract_series_from_folder_name("Демон - завершён") == "Демон"

    def test_zavershen_ye_form_stripped(self):
        assert _svc()._extract_series_from_folder_name("Артефактор - завершен") == "Артефактор"

    def test_feminine_grammatical_form_stripped(self):
        # Согласование рода с именем серии ("Сага - завершена").
        assert _svc()._extract_series_from_folder_name("Сага - завершена") == "Сага"

    def test_multiword_series_name_preserved(self):
        assert _svc()._extract_series_from_folder_name(
            "Хранитель (Боги не врут) - завершён"
        ) == "Хранитель (Боги не врут)"


class TestParenthesizedStatusWordStillWorksAsBefore:
    def test_parenthesized_form_unaffected_by_new_dash_rule(self):
        # Sanity: скобочная форма уже работала раньше — фикс не должен
        # был её задеть.
        assert _svc()._extract_series_from_folder_name("Алхимик (завершён)") == "Алхимик"


class TestUnrelatedDashedNameNotTouched:
    def test_dash_with_non_status_tail_untouched(self):
        # "Артефактор - подарок" — обычное составное название через
        # дефис, не статусная пометка. Не должно ложно срабатывать.
        assert _svc()._extract_series_from_folder_name(
            "Артефактор - подарок"
        ) == "Артефактор - подарок"
