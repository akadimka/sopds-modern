"""
PASS2: Author Extraction

Extracts author name from folder name based on selected pattern and structural info.
"""

import re
from typing import Optional


def _singularize_surname(surname: str) -> str:
    """Convert plural Russian surname to singular form.
    
    Examples:
        "Живовы" → "Живов"
        "Петровы" → "Петров"
        "Сафины" → "Сафин"
        
    Args:
        surname: Surname (possibly in plural form)
        
    Returns:
        Singular form of surname
    """
    if not surname or len(surname) < 2:
        return surname
    
    # Common plural endings for Russian surnames
    if surname.endswith('ие'):
        # Стругацкие → Стругацкий
        return surname[:-2] + 'ий'
    elif surname.endswith('ые'):
        # Толстые → Толстой
        return surname[:-2] + 'ой'
    elif surname.endswith('ы'):
        # Живовы → Живов
        return surname[:-1]
    elif surname.endswith('и'):
        # Сафины → Сафин
        return surname[:-1]

    # Already singular or doesn't match pattern
    return surname


def extract_author(struct_info: dict, pattern: Optional[str]) -> str:
    """
    Extracts author name based on pattern and structural information.
    
    Args:
        struct_info: Dictionary from pass0_structural_analysis.analyze_structure()
        pattern: Pattern from pass1_pattern_selection.select_pattern()
        
    Returns:
        Author name (surname + name) or empty string
    """
    
    if not pattern:
        return ""
    
    name = struct_info['name']
    paren_contents = struct_info['paren_contents']
    text_after_last = struct_info['text_after_last']
    text_before_first = struct_info['text_before_first']
    
    author = ""

    # Strip square-bracket aliases/pseudonyms from paren content before any processing.
    # Example: "Высоченко [Иринин, Ватный] Александр" → "Высоченко  Александр" → "Высоченко Александр"
    paren_contents = [re.sub(r'\s*\[[^\]]*\]\s*', ' ', c).strip() for c in paren_contents]
    # Also strip from name itself (handles "Author, Author" patterns with brackets in name)
    name = re.sub(r'\s*\[[^\]]*\]\s*', ' ', name).strip()

    if pattern == "SurnamePlural FirstName и SecondName":
        # Format: "Живовы Георгий и Геннадий"
        # Extract and construct: "Живов Георгий; Живов Геннадий"
        if ' и ' in name:
            parts = name.split(' и ')
            if len(parts) == 2:
                first_part = parts[0].strip()  # "Живовы Георгий"
                second_part = parts[1].strip()  # "Геннадий"
                # Extract surname from first part
                first_words = first_part.split()
                if len(first_words) >= 2:
                    plural_surname = first_words[0]
                    singular_surname = _singularize_surname(plural_surname)
                    first_name = ' '.join(first_words[1:])
                    second_name = second_part
                    # Construct with singular surname, separated by "; "
                    author = f"{singular_surname} {first_name}; {singular_surname} {second_name}"
    
    elif pattern == "Author, Author":
        # Both authors separated by comma, normalize to "; " separator
        # "Земляной Андрей, Орлов Борис" → "Земляной Андрей; Орлов Борис"
        author = name.replace(', ', '; ')
    
    elif pattern == "(Surname) (Name)":
        # Both words as is
        author = name.strip()
    
    elif pattern == "Series (Author, Author)":
        # Content of first parentheses (authors) - normalize to '; ' separator
        if paren_contents:
            # Convert ", " to "; " for unified processing in PASS 3
            author = paren_contents[0].strip().replace(', ', '; ')
    
    elif pattern == "Author (CoAuthor)":
        # В библиотечной организации "Псевдоним (Реальное имя)" означает, что
        # в скобках стоит настоящее имя автора, а не соавтор.
        # Возвращаем только основное имя (псевдоним), игнорируем скобки.
        # Example: "Орлов Алекс (Дарищев Вадим)" → "Орлов Алекс"
        if text_before_first:
            author = text_before_first.strip()

    elif pattern == "Series (Author)":
        # LAST parentheses ← KEY for МВП-2 (1) Одиссея (Чернов)
        if paren_contents:
            author = paren_contents[-1].strip()
    
    elif pattern == "(Series) Author":
        # Text AFTER parentheses
        author = text_after_last.strip()
    
    elif pattern == "Author - Folder Name":
        # Text BEFORE dash
        if ' - ' in name:
            author = name.split(' - ')[0].strip()
            # Strip pseudonym in parentheses: "Абрахам Дэниел (Джеймс С.А. Кори)" → "Абрахам Дэниел"
            author = re.sub(r'\s*\([^)]*\)', '', author).strip()
    
    elif pattern == "Author-Collection":
        # "Алексей Вязовский-Сборник произведений" → автор до дефиса
        if '-' in name:
            author = name[:name.index('-')].strip()

    elif pattern == "Author Collection":
        # "Вадим Панов  Собрание сочинений" → первые два слова
        words = name.split()
        author = ' '.join(words[:2])

    elif pattern == "SingleWord Author":
        # Однословный псевдоним/никнейм подтверждён словарём имён — берём как есть
        author = name

    elif pattern == "Series":
        # Fallback: if there are NO parentheses with AUTHORS, don't extract anything
        # This is just a series name, not an author name
        # Only extract if text_before_first has something meaningful (like before parentheses)
        text_before_first = struct_info['text_before_first']
        if text_before_first and struct_info['paren_count'] > 0:
            # Text before brackets: "Максим Шаттам - Собрание сочинений" → extract "Максим Шаттам"
            author = text_before_first.strip()
        else:
            # No parentheses with author info, don't parse the folder name
            author = ""
    
    return author.strip() if author else ""
