"""Римские цифры в номерах томов («Том IV», «Книга II-III»)."""
from typing import Optional

_VALUES = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}


def roman_to_int(s: str) -> Optional[int]:
    """Римское число → int; None для пустой строки или недопустимого символа."""
    s = s.upper().strip()
    if not s:
        return None
    result = 0
    prev = 0
    for ch in reversed(s):
        v = _VALUES.get(ch)
        if v is None:
            return None
        result += v if v >= prev else -v
        prev = v
    return result if result > 0 else None
