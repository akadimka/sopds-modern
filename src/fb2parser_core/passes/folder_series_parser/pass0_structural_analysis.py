"""
PASS0: Анализ структуры названия папки для извлечения СЕРИЙ.
Аналог folder_author_parser/pass0_structural_analysis.py но для серий.
"""

import re
from typing import Dict, List, Tuple


def analyze_series_folder_structure(folder_name: str) -> Dict:
    """
    Анализировать структуру папки и выявить элементы (скобки, дефисы, etc).
    
    Результат: словарь с информацией о структуре.
    """
    return {
        'full_name': folder_name,
        'has_parentheses': '(' in folder_name and ')' in folder_name,
        'has_brackets': '[' in folder_name and ']' in folder_name,
        'has_dashes': ' - ' in folder_name or '-' in folder_name,
        'has_quotes': '"' in folder_name or '«' in folder_name,
        'parentheses_content': extract_parentheses_content(folder_name),
        'brackets_content': extract_brackets_content(folder_name),
        'word_count': len(folder_name.split()),
    }


def extract_parentheses_content(text: str) -> List[Tuple[str, int, int]]:
    """Найти все скобки () в тексте."""
    results = []
    for match in re.finditer(r'\(([^)]+)\)', text):
        results.append((match.group(1), match.start(), match.end()))
    return results


def extract_brackets_content(text: str) -> List[Tuple[str, int, int]]:
    """Найти все квадратные скобки [] в тексте."""
    results = []
    for match in re.finditer(r'\[([^\]]+)\]', text):
        results.append((match.group(1), match.start(), match.end()))
    return results
