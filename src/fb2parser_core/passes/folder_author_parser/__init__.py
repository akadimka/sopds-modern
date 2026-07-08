"""
Folder Author Parser - Modular Edition

PASS0 + PASS1 + PASS2 architecture for parsing author names from folder names.

Reference: REGEN_CSV_ARCHITECTURE.md - Section 3.1 (Folder Hierarchy)
"""

from .pass0_structural_analysis import analyze_structure
from .pass1_pattern_selection import select_pattern
from .pass2_author_extraction import extract_author

# Blacklist categories загружаются из конфига в функциях


def parse_author_from_folder_name(folder_name: str,
                                   male_names: set = None,
                                   female_names: set = None) -> str:
    """
    Parses author name from a folder name using PASS0+PASS1+PASS2 architecture.
    
    Returns empty string if:
    - folder_name is in the blacklist (category folders like "Серия", "Сборник")
    - folder_name doesn't match any known pattern
    - folder_name is too generic/not an author name
    
    Args:
        folder_name: The folder name to parse
        
    Returns:
        Author name (surname + name) or empty string
        
    Examples:
        "Волков Тим" → "Волков Тим"
        "Живовы Георгий и Геннадий" → "Живовы Георгий; Живовы Геннадий"
        "МВП-2 (1) Одиссея (Чернов)" → "Чернов"
        "Защита Периметра (Абенд Эдвард)" → "Абенд Эдвард"
        "Максим Шаттам - Собрание сочинений" → "Максим Шаттам"
        "Иван Петров, Сергей Иванов" → "Иван Петров; Сергей Иванов"
        "(Боевой отряд) Петров И." → "Петров И."
        "Серия" → ""  (blacklist)
        "Демонолог" → ""  (single word)
    """
    
    if not folder_name or not folder_name.strip():
        return ""
    
    name = folder_name.strip()
    
    # ==================== Check blacklist ====================
    # Загружаем категории из конфига
    try:
        from settings_manager import SettingsManager
        settings = SettingsManager()
        blacklist_starts = settings.get_list('collection_keywords') + ['Unknown', 'Various']
    except Exception:
        # Fallback если конфиг недоступен
        blacklist_starts = [
            'Серия', 'Сборник', 'Коллекция', 'Антология', 'Цикл', 'Подборка',
            'Архив', 'Разное', 'Другое', 'Unknown', 'Various'
        ]
    
    name_lower = name.lower()
    for word in blacklist_starts:
        if name_lower.startswith(word.lower()):
            return ""  # This is a category, not an author
    
    # ==================== PRE-PASS: Strip dot-as-word-separator ====================
    # Folders like "Бах. Ричард" use ". " as a separator between surname and first name.
    # Replace ". " with " " when the word before the dot:
    #   - is >= 3 chars long (not a single-letter initial)
    #   - ends in a lowercase letter (not an abbreviation like "Дж." or "МИФ.")
    #   - contains no internal dots (not "Дж.Дж." or "С.Дж.")
    import re as _re_pre
    def _strip_dot_separator(s: str) -> str:
        def _repl(m: '_re_pre.Match') -> str:
            word = m.group(1)
            nxt = m.group(2)
            if len(word) >= 3 and word[-1].islower() and '.' not in word:
                return word + ' ' + nxt
            return m.group(0)
        return _re_pre.sub(r'(\S+)\. ([А-ЯЁA-Z])', _repl, s)
    name = _strip_dot_separator(name)

    # ==================== PASS0: Structural Analysis ====================
    struct_info = analyze_structure(name)
    
    # ==================== PASS1: Pattern Selection ====================
    pattern = select_pattern(struct_info,
                              male_names=male_names or set(),
                              female_names=female_names or set())

    # ==================== PASS2: Author Extraction ====================
    author = extract_author(struct_info, pattern)
    
    return author


__all__ = [
    'parse_author_from_folder_name',
    'analyze_structure',
    'select_pattern',
    'extract_author',
]
