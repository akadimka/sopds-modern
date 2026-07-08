"""
Модуль с вспомогательными функциями для обработки серий.

Содержит утилиты для работы с текстом, авторами, паттернами и т.д.
"""

import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Any, Optional


def _nfc_lower_yo(s: str) -> str:
    """
    NFC-нормализация + lower + ё→е.
    """
    return unicodedata.normalize('NFC', s).lower().replace('\u0451', '\u0435')


def _norm_for_prefix(s: str) -> str:
    """
    Нормализация для префиксного сравнения авторов.
    """
    MIXED_SCRIPT_NORM = str.maketrans('ZzАВЕКМНОРСТХ', 'ззАВЕКМНОРСТХ')  # Latin→Cyrillic lookalikes
    return _nfc_lower_yo(s).translate(MIXED_SCRIPT_NORM)


def _strip_author_suffix(s: str) -> str:
    """
    Убрать суффиксы вида (Автор) / [Автор] из названия серии.
    """
    s = re.sub(r'\s*\([^)]*\)\s*$', '', s).strip()
    s = re.sub(r'\s*\[[^\]]*\]\s*$', '', s).strip()
    return s


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


def _strip_author_from_stem(stem: str, author: str) -> str:
    """
    Убрать имя автора с начала stem. Пробует 'Author. ' и 'Author - '.
    """
    sl = _nfc_lower_yo(stem)
    for sep in ('. ', ' - '):
        prefix = _nfc_lower_yo(author) + sep
        if sl.startswith(prefix):
            return stem[len(prefix):]
    # Попробовать reversed "Фамилия Имя" → "Имя Фамилия"
    parts = author.split()
    if len(parts) == 2:
        rev = f"{parts[1]} {parts[0]}"
        for sep in ('. ', ' - '):
            prefix = _nfc_lower_yo(rev) + sep
            if sl.startswith(prefix):
                return stem[len(prefix):]
    return stem


def _title_phrases(title: str, min_words: int = 2) -> List[str]:
    """
    Все N-граммы с начала строки (убирая конечный номер).
    """
    t = re.sub(r'\s+\d+(\s*\.\s*.*)?$', '', title).strip()
    words = t.split()
    result = []
    for n in range(len(words), min_words - 1, -1):
        phrase = ' '.join(words[:n])
        if len(phrase) >= 3:
            result.append(phrase)
    return result


def extract_series_base_from_filename(filename: str) -> Optional[str]:
    """
    Извлечь базовое название серии из имени файла.

    Args:
        filename: Имя файла без расширения

    Returns:
        Базовое название серии или None
    """
    # Убрать расширение если есть
    stem = Path(filename).stem if '.' in filename else filename

    # Простые паттерны: "Author - Series N" или "Series N"
    # Найти позицию серии
    dash_pos = stem.find(' - ')
    if dash_pos >= 0:
        after_dash = stem[dash_pos + 3:].strip()
        # Убрать номер в конце
        base = re.sub(r'\s+\d+\s*$', '', after_dash).strip()
        return base if base else None

    # "Author. Series"
    dot_pos = stem.find('. ')
    if dot_pos >= 0:
        after_dot = stem[dot_pos + 2:].strip()
        base = re.sub(r'\s+\d+\s*$', '', after_dot).strip()
        return base if base else None

    # Если начинается с серии
    if stem and not any(char.isdigit() for char in stem[:10]):  # Не начинается с цифр
        base = re.sub(r'\s+\d+\s*$', '', stem).strip()
        return base if base else None

    return None


def is_series_collection_folder(folder_name: str) -> bool:
    """
    Проверить, является ли папка коллекцией серий.

    Args:
        folder_name: Название папки

    Returns:
        True если это папка серий
    """
    lower = folder_name.lower()
    return 'серия' in lower or 'series' in lower


def has_service_marker(text: str, service_words: List[str]) -> bool:
    """
    Проверить наличие сервисных слов в тексте.

    Args:
        text: Текст для проверки
        service_words: Список сервисных слов

    Returns:
        True если найдено хотя бы одно
    """
    text_lower = text.lower()
    return any(marker in text_lower for marker in service_words)


def get_folder_depth(filepath: str) -> int:
    """
    Получить глубину папки для файла.

    Args:
        filepath: Полный путь к файлу

    Returns:
        Глубина (количество папок)
    """
    parts = Path(filepath).parts
    # Исключая корень и файл
    return len(parts) - 2 if len(parts) > 1 else 0