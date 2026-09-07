"""Регрессия для `Pass4Consensus.execute()` — docs/quality-roadmap.md,
баг №32.

Реальный случай (замечен пользователем в CSV): "Ефремова Е. S-T-I-K-S.
Иные 3. Трио.fb2" был единственной книгой автора с ТОЧНОЙ строкой
proposed_series="S-T-I-K-S\\Иные" (`filename_named_arc`, подтверждённая
именованная дуга) — соседние тома серии ("Иные.fb2", "Иные 2.fb2") имели
proposed_series="Иные" (плоское, через `author-consensus`, без
"S-T-I-K-S\\"). "Collapsed singleton subseries to base series" — шаг,
откатывающий подсерию в базовую серию, если ТОЧНАЯ строка встречается
только у ОДНОЙ книги автора — считал "S-T-I-K-S\\Иные" уникальной (не
разбирая, что это ТА ЖЕ арка "Иные", просто в другой текстовой форме) и
откатывал её в голый франшизный корень "S-T-I-K-S" — том 3 полностью
терял связь с серией (а после — клир голой франшизы стирал его вообще).

Причина: guard-исключение "папочная иерархия авторитетна без count-
подтверждения" не включало `filename_named_arc` — хотя `_detect_named_arcs()`
УЖЕ требует ≥2 вхождений имени арки, чтобы присвоить эту метку;
повторная проверка count здесь избыточна и ошибочна для этого источника.
"""
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass4_consensus import Pass4Consensus
from fb2parser_web.fb2parser_bridge import _config_path


def _rec(path, series, source, number=""):
    return BookRecord(
        file_path=path, file_title="T", metadata_authors="Елена Ефремова",
        proposed_author="Ефремова Елена", author_source="metadata",
        metadata_series="", proposed_series=series, series_source=source,
        series_number=number,
    )


class TestNamedArcSingletonNotCollapsedToBareRoot:
    def test_sole_named_arc_record_kept_hierarchical(self):
        records = [
            _rec("Ефремова Е. S-T-I-K-S. Иные.fb2", "Иные", "author-consensus", "1"),
            _rec("Ефремова Е. S-T-I-K-S. Иные 2.fb2", "Иные", "author-consensus", "2"),
            _rec("Ефремова Е. S-T-I-K-S. Иные 3. Трио.fb2",
                 "S-T-I-K-S\\Иные", "filename_named_arc", "3"),
        ]
        Pass4Consensus(Logger(), settings=_settings()).execute(records)

        assert records[2].proposed_series == "S-T-I-K-S\\Иные"
        assert records[2].series_source == "filename_named_arc"


def _settings():
    from fb2parser_core.settings_manager import SettingsManager
    return SettingsManager(_config_path())
