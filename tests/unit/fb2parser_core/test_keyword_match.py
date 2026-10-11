"""fb2parser_core.keyword_match — маркеры по границам слов (баг №130)."""
import pytest

from fb2parser_core.keyword_match import (
    contains_any_keyword,
    contains_keyword,
    starts_with_keyword,
)


@pytest.mark.parametrize("text, keyword", [
    ("Летописи Разлома", "топ"),       # «топ» внутри «летописи»
    ("Утопия", "топ"),
    ("Топоров Владимир", "топ"),       # короткий маркер — только целое слово
    ("Неоклассика", "классика"),
    ("Непопулярные мнения", "популярные"),
])
def test_keyword_inside_word_does_not_match(text, keyword):
    assert not contains_keyword(text, keyword)


@pytest.mark.parametrize("text, keyword", [
    ("Топ-10 фантастики", "топ"),
    ("Мир Алекса Королёва. Сборник", "сборник"),
    ("В сборнике рассказов", "сборник"),  # окончание у длинного маркера допустимо
    ("Шедевры фантастики", "шедевры фантастики"),
    ("Best Of Asimov", "best of"),
    ("Избранное", "ИЗБРАННОЕ"),
])
def test_keyword_at_word_start_matches(text, keyword):
    assert contains_keyword(text, keyword)


def test_yo_and_e_are_equal():
    assert contains_keyword("Лучшее за год", "лучшее")
    assert contains_keyword("Полное собрание сочинений", "полное собрание")


def test_contains_any_keyword():
    assert contains_any_keyword("Антология ужасов", ["топ", "антология"])
    assert not contains_any_keyword("Летописи Разлома", ["топ", "антология"])
    assert not contains_any_keyword("Летописи Разлома", None)


@pytest.mark.parametrize("text, keyword, expected", [
    ("Сборник авторов", "сборник", True),
    ("«Сборник» авторов", "сборник", True),
    ("Топор войны", "топ", False),
    ("Летописи", "топ", False),
    ("Топ лучших", "топ", True),
])
def test_starts_with_keyword(text, keyword, expected):
    assert starts_with_keyword(text, keyword) is expected
