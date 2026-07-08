"""
PASS2: Извлечение СЕРИИ по выбранному паттерну.
Использует AuthorName из существующего кода ТОЛЬКО для проверок.
"""

import re
from typing import Optional
from ...name_normalizer import AuthorName


def extract_series_from_folder_name(
    folder_name: str,
    pattern: str,
    known_authors: set = None,
    service_words: list = None,
    collection_keywords: list = None
) -> Optional[str]:
    """
    Извлечь серию по выбранному паттерну.
    
    Использует AuthorName чтобы убедиться что результат - это НЕ автор!
    (чтобы серия не совпала с именем автора)
    
    Args:
        folder_name: Имя папки для парсинга
        pattern: Выбранный паттерн ("Series (Author)", "[Series]", etc)
        known_authors: Набор известных авторов для проверки (опционально)
        service_words: Служебные слова для фильтрации (опционально)
        collection_keywords: Ключевые слова коллекций для фильтрации (опционально)
    
    Returns:
        Название серии или пустая строка если не смогли извлечь
    """
    
    series = ""
    
    if pattern == "Series (Author)":
        # "Series Name (John Doe)" → "Series Name"
        match = re.match(r'^(.+?)\s*\([^)]+\)\s*$', folder_name)
        if match:
            series = match.group(1).strip()
    
    elif pattern == "[Series]":
        # "[Series Name]" → "Series Name"
        match = re.search(r'\[([^\]]+)\]', folder_name)
        if match:
            series = match.group(1).strip()
    
    elif pattern == "Series - Description":
        # "Series - Some Description" → "Series"
        parts = folder_name.split(' - ')
        series = parts[0].strip()
    
    elif pattern == "Series":
        # Вся папка это серия
        series = folder_name.strip()
    
    if not series:
        return ""
    
    # ✅ ПРОВЕРКА: Убедиться что это НЕ похоже на автора!
    # Используем AuthorName чтобы проверить
    try:
        # Если это похоже на автора - отвергаем как серию
        author_name = AuthorName(series, [])
        if author_name.is_valid_author():
            return ""  # Это похоже на автора, отвергаем как серию
    except Exception:
        pass  # Если ошибка при парсинге - значит это вероятно серия
    
    # ✅ ФИЛЬТР: Исключить очевидные сборники
    if not collection_keywords:
        # Fallback: загрузить из конфига если не передан как параметр
        try:
            from settings_manager import SettingsManager
            settings = SettingsManager()
            collection_keywords = settings.get_list('collection_keywords')
        except Exception:
            # Final fallback если конфиг недоступен
            collection_keywords = ["сборник", "антология", "коллекция", "архив", "разное", "другое"]
    
    series_lower = series.lower()
    if any(word.lower() in series_lower for word in collection_keywords):
        return ""
    
    return series
