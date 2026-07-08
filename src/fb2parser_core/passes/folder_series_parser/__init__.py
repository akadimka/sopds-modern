"""
Парсер названий папок для СЕРИЙ (аналог folder_author_parser для авторов).
Использует PASS0+PASS1+PASS2 архитектуру как в folder_author_parser.
"""

from .pass0_structural_analysis import analyze_series_folder_structure
from .pass1_pattern_selection import select_series_pattern  
from .pass2_series_extraction import extract_series_from_folder_name
from typing import Tuple, Optional


def parse_series_from_folder_name(
    folder_name: str, 
    known_authors: set = None,
    service_words: list = None,
    collection_keywords: list = None
) -> Tuple[str, str]:
    """
    Парсить название папки и извлечь серию (если это именно серия, не автор).
    
    Использует PASS0, PASS1, PASS2 для анализа структуры папки.
    
    Args:
        folder_name: Имя папки для парсинга
        known_authors: Набор известных авторов для проверки
        service_words: Список служебных слов
        collection_keywords: Ключевые слова коллекций
    
    Returns:
        (series_name, series_source)
        Пример: ("ISCARIOT", "folder_dataset") или ("", "")
    """
    
    # PASS0: Анализ структуры папки (скобки, дефисы, запятые)
    structure = analyze_series_folder_structure(folder_name)
    
    # PASS1: Выбор подходящего паттерна из доступных
    best_pattern = select_series_pattern(structure, folder_name)
    
    # PASS2: Извлечение серии по выбранному паттерну
    if best_pattern:
        series = extract_series_from_folder_name(
            folder_name, 
            best_pattern,
            known_authors=known_authors,
            service_words=service_words,
            collection_keywords=collection_keywords
        )
        if series:
            return (series, "folder_dataset")
    
    return ("", "")
