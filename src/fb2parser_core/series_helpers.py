"""
Модуль с вспомогательными функциями для обработки серий.

Содержит утилиты для работы с текстом, авторами, паттернами и т.д.
"""

import re
import unicodedata


def _nfc_lower_yo(s: str) -> str:
    """
    NFC-нормализация + lower + ё→е.
    """
    return unicodedata.normalize('NFC', s).lower().replace('\u0451', '\u0435')


def _bl_matches(bl: str, text: str, multi_word_series: bool = False) -> bool:
    """
    Проверить совпадение blacklist слова в тексте.

    Для многословных серий (2+ слов) требуется точное совпадение всей строки.
    Для коротких blacklist записей (< 4 символов) требуются границы слов.
    """
    if multi_word_series:
        return bl == text.strip()
    if len(bl) < 4:
        return bool(re.search(r'(?<![\w\u0430-\u044f\u0451a-z])' + re.escape(bl) + r'(?![\w\u0430-\u044f\u0451a-z])', text, re.IGNORECASE))
    return bl in text

