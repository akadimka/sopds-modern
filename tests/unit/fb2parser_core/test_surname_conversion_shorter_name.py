"""Замена имени автора (author_surname_conversions) на более короткое имя
(2026-11-06, слияние вариантов имён в Фантастике).

Защита от двойного применения пропускала замену, если новое имя «уже есть»
в строке. У вариантов с лишними инициалами и вторыми именами («Поселягин
Владимир Г» → «Поселягин Владимир», «Хайнлайн Роберт Энсон» → «Хайнлайн
Роберт») новое имя всегда содержится в старом — замена не срабатывала, и
синхронизация снова клала книги в папку варианта.
"""
from types import SimpleNamespace

import pytest

from fb2parser_core.author_normalizer_extended import AuthorNormalizer


def _normalizer(conversions):
    norm = AuthorNormalizer.__new__(AuthorNormalizer)
    norm.settings = SimpleNamespace(get_author_surname_conversions=lambda: conversions)
    return norm


@pytest.mark.parametrize("variant, canonical", [
    ("Поселягин Владимир Г", "Поселягин Владимир"),
    ("Хайнлайн Роберт Энсон", "Хайнлайн Роберт"),
    ("Р. Мартин Джордж Р", "Мартин Джордж"),
])
def test_variant_with_extra_words_is_shortened(variant, canonical):
    norm = _normalizer({variant: canonical})
    assert norm.apply_conversions(variant) == canonical
    assert norm.apply_conversions(canonical) == canonical               # каноническое не трогаем


def test_longer_canonical_name_is_applied_once():
    norm = _normalizer({"Робинсон Ким": "Робинсон Ким Стэнли"})
    assert norm.apply_conversions("Робинсон Ким") == "Робинсон Ким Стэнли"
    assert norm.apply_conversions("Робинсон Ким Стэнли") == "Робинсон Ким Стэнли"   # не «… Стэнли Стэнли»


def test_each_coauthor_is_converted():
    norm = _normalizer({"Поселягин Владимир Г": "Поселягин Владимир", "Нина Демидова": "Демидова Нина"})
    assert norm.apply_conversions("Поселягин Владимир Г, Нина Демидова") == "Поселягин Владимир, Демидова Нина"
