"""Общий `roman_to_int` вместо трёх копий (fb2_compiler и две в pass2).
Копии в pass2 не проверяли символы и на чужом символе падали KeyError."""
import pytest

from fb2parser_core.roman_numerals import roman_to_int


@pytest.mark.parametrize("s, expected", [
    ("I", 1), ("iv", 4), (" IX ", 9), ("XIV", 14), ("XL", 40), ("MCMXCIX", 1999),
    ("", None), ("   ", None), ("IVX1", None), ("Том", None),
])
def test_roman_to_int(s, expected):
    assert roman_to_int(s) == expected
