"""Поиск слов-маркеров (collection_keywords и т.п.) в тексте по границам слов.

Баг №130: проверка «kw in text» находила «топ» внутри «ле-топ-иси», и серия
«Летописи Разлома» отбрасывалась как сборник. Маркер должен начинаться
с начала слова; хвост допускается («сборник» → «сборнике»), кроме коротких
маркеров (до 4 букв: «топ», «хиты») — они совпадают только со словом целиком,
иначе «топ» ловил бы «топор» и «Топь».
"""
import re
from functools import lru_cache

_SHORT_KEYWORD_LEN = 4


def _norm(s: str) -> str:
    return s.lower().replace('ё', 'е')


@lru_cache(maxsize=1024)
def _keyword_re(keyword: str, anchored: bool) -> re.Pattern:
    kw = _norm(keyword.strip())
    tail = r'(?!\w)' if len(kw) <= _SHORT_KEYWORD_LEN else ''
    head = r'^\W*' if anchored else r'(?<!\w)'
    return re.compile(head + re.escape(kw) + tail)


def contains_keyword(text: str, keyword: str) -> bool:
    """Есть ли маркер `keyword` в `text` (без учёта регистра и ё/е)."""
    if not text or not keyword or not keyword.strip():
        return False
    return bool(_keyword_re(keyword, False).search(_norm(text)))


def contains_any_keyword(text: str, keywords) -> bool:
    return any(contains_keyword(text, kw) for kw in (keywords or ()))


def starts_with_keyword(text: str, keyword: str) -> bool:
    """Начинается ли `text` с маркера `keyword` (по тем же правилам границ)."""
    if not text or not keyword or not keyword.strip():
        return False
    return bool(_keyword_re(keyword, True).match(_norm(text)))
