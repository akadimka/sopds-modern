"""
Константы для извлечения авторов и серий из различных источников.

Баг №105: раньше модуль также определял `AuthorExtractionPriority`/
`SeriesExtractionPriority`/`ConfidenceLevel`/`FilterReason`/
`ExtractionResult` для приоритетной стратегии извлечения авторов/серий
в `fb2_author_extractor.py`/`author_processor.py`/`series_processor.py` —
вся эта стратегия убрана как мёртвый код (см. docs/quality-roadmap.md,
баг №105), эти 5 классов остались без единого внешнего вызывающего.
Убраны целиком. Ниже — только то, что реально используется:
`is_no_series_folder`/`NO_SERIES_FOLDER_NAMES`/
`FILE_EXTENSION_FOLDER_NAMES` (regen_csv.py, precache.py,
passes/pass1_read_files.py, passes/pass2_series_filename.py).
"""

import re

# Имена папок, совпадающие с расширениями файлов, которые нужно прозрачно пропускать
# при анализе структуры пути.
# Структура "Автор\fb2\Серия\книга.fb2" обрабатывается как "Автор\Серия\книга.fb2".
FILE_EXTENSION_FOLDER_NAMES: frozenset = frozenset({
    'fb2', 'rtf', 'pdf', 'doc', 'docx', 'txt', 'epub',
    'djvu', 'djv', 'mobi', 'azw', 'azw3', 'lit', 'lrf',
    'html', 'htm', 'odt', 'zip', 'rar', '7z',
})
# Нормализованные (нижний регистр, е́→е) имена папок, означающих «без серии».
# Если папка с таким именем встречается в пути, proposed_series должно остаться пустым.
NO_SERIES_FOLDER_NAMES: frozenset = frozenset({
    # вне серий
    'вне серий', 'вне серии',
    # без серий
    'без серии', 'без серий',
    # несерийное
    'несерийное', 'несерийный',
    # внесерийное
    'внесерийное',
    # отдельные произведения
    'отдельные произведения', 'отдельное произведение',
    # standalone
    'standalone',
})


def is_no_series_folder(folder_name: str, extra_names: frozenset = None) -> bool:
    """Return True if the folder/file name means 'books without a series'.

    Comparison is case-insensitive and treats е́ (ё) as е. Matches the phrase
    ANYWHERE in the name (word-boundary match), not just as an exact name —
    "Абрамов Владимир - Вне серий", "Повесть (вне серии).fb2" and a bare
    "Вне серий" folder all count.

    extra_names: optional frozenset of user-defined phrases loaded from config
                 (no_series_folder_names). Built-in NO_SERIES_FOLDER_NAMES
                 always acts as a fallback.
    """
    normalized = folder_name.lower().replace('е́', 'е').replace('ё', 'е')
    for phrase in NO_SERIES_FOLDER_NAMES | (extra_names or frozenset()):
        if re.search(r'(?:^|\W)' + re.escape(phrase) + r'(?:\W|$)', normalized):
            return True
    return False
