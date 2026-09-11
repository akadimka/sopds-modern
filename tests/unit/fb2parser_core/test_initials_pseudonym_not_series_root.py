"""Регрессия для `Pass2SeriesFilename._extract_series_from_filename()` —
корень серии, состоящий целиком из инициалов ("С.", "М.", "С.К.С."), не
может быть настоящим названием серии — это либо инициал автора, либо (баг
№59) псевдоним-аббревиатура из нескольких инициалов без пробелов.

Реальный случай (docs/quality-roadmap.md, баг №59): "С.К.С., Вязовский -
Режим бога" — первые 3 тома изданы под псевдонимом "С.К.С.", остальные —
тем же автором (Вязовский Алексей) под своим именем. Для файла "01. С.К.С.
- Режим бога. Книга 1.fb2" (без своего metadata_series) BlockLevelPatternMatcher
матчил "С.К.С." как КОРЕНЬ иерархической серии для "Режим бога"
("С.К.С.\\Режим бога") — уже существовавший guard (баг №50) отбрасывал
только ОДНУ голую инициаль ("С."), но не несколько инициалов, слепленных
без пробелов подряд.
"""
import logging

from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path


def _extractor():
    return Pass2SeriesFilename(logging.getLogger("test"), config_path=_config_path())


class TestMultiInitialPseudonymNotMistakenForSeriesRoot:
    def test_three_letter_pseudonym_without_metadata_returns_nothing(self):
        candidate = _extractor()._extract_series_from_filename(
            "01. С.К.С. - Режим бога. Книга 1.fb2",
            validate=True,
            metadata_series="",
            proposed_author="Вязовский Алексей",
        )
        assert not candidate
        assert "С.К.С" not in (candidate or "")

    def test_bare_single_initial_still_handled_same_way(self):
        # Баг №50 — уже работавший случай не должен сломаться обобщением guard'а.
        candidate = _extractor()._extract_series_from_filename(
            "Битон М. С. - Хэмиш Макбет 1. Смерть сплетницы.fb2",
            validate=True,
            metadata_series="",
            proposed_author="Битон М. С.",
        )
        assert not candidate

    def test_real_series_name_after_dash_still_recognized(self):
        # Sanity: обобщённый guard не должен ловить настоящие короткие серии.
        candidate = _extractor()._extract_series_from_filename(
            "Иванов Пётр - Сага 1. Начало.fb2",
            validate=True,
            metadata_series="",
            proposed_author="Иванов Пётр",
        )
        assert candidate == "Сага"
