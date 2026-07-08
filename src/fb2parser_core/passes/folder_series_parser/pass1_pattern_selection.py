"""
PASS1: Выбор подходящего паттерна для извлечения СЕРИЙ из названия папки.
"""

from typing import Dict, Optional


def select_series_pattern(structure: Dict, folder_name: str) -> Optional[str]:
    """
    Выбрать лучший паттерн на основе структуры папки.
    
    Паттерны (в порядке приоритета):
    1. "Series (Author)" - серия в начале, автор в конце в скобках
    2. "[Series]" - серия в квадратных скобках
    3. "Series - Description" - серия разделена дефисом
    4. Просто название папки = серия (если не выглядит как сборник)
    """
    
    # Паттерн 1: "Series (Author)" - серия главная, автор уточняет в скобках
    if structure['has_parentheses']:
        paren_content = structure['parentheses_content']
        if paren_content:
            # Последняя скобка содержит потенциального автора
            last_paren_content = paren_content[-1][0]
            # Проверить если это похоже на автора (мин 1 слово)
            if len(last_paren_content.split()) >= 1:
                return "Series (Author)"
    
    # Паттерн 2: "[Series]" - серия в квадратных скобках
    if structure['has_brackets']:
        return "[Series]"
    
    # Паттерн 3: "Series - Description"
    if structure['has_dashes']:
        parts = folder_name.split(' - ')
        if len(parts) == 2 and len(parts[0].split()) <= 3:  # Первая часть короче
            return "Series - Description"
    
    # Паттерн 4: Просто название (без признаков быть чем-то другим)
    if not structure['has_parentheses'] and not structure['has_dashes']:
        return "Series"
    
    return None
