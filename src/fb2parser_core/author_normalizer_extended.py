#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Author Normalizer Extended - PASS 3, 5, 6 functions for CSV regeneration

Модуль обработки авторов для PASS 3, 5, 6 системы регенерации CSV.
Функции для:
- PASS 3: Нормализация формата авторов (Имя Фамилия → Фамилия Имя)
- PASS 5: Применение конвертаций фамилий (после консенсуса)
- PASS 6: Раскрытие аббревиатур (И.Петров → Иван Петров)

Все функции работают с BookRecord dataclass и используют SettingsManager для конфигурации.
"""

import re
from typing import List, Dict, Set, Optional, Callable, Any
from collections import Counter
from dataclasses import dataclass, field

try:
    from settings_manager import SettingsManager
    from logger import Logger
    from name_normalizer import AuthorName
except ImportError:
    from .settings_manager import SettingsManager
    from .logger import Logger
    from .name_normalizer import AuthorName


@dataclass
class BookRecord:
    """Запись о книге с прогрессивным заполнением на разных PASS.
    
    Evolves through the PASS system:
    - PASS 1: Initialized with author and series determined by priority
    - PASS 3: proposed_author normalized format
    - PASS 4: proposed_author may change due to consensus, author_source = "consensus"
    - PASS 5: proposed_author may be reconverted
    - PASS 6: proposed_author abbreviations expanded
    """
    file_path: str              # Путь к FB2 файлу (относительно library_path)
    file_title: str             # Название книги из title-info
    metadata_authors: str       # Исходные авторы из FB2 XML (неизменяемое!)
    proposed_author: str        # Предложенный автор (эволюционирует через PASS)
    author_source: str          # Источник: "folder_dataset", "filename", "metadata", "consensus"
    metadata_series: str = ""   # Оригинальная серия из FB2 XML (неизменяемое!)
    proposed_series: str = ""   # Предложенная серия (эволюционирует через PASS)
    series_source: str = ""     # Источник серии: "folder_dataset", "filename", "metadata", "consensus"
    metadata_genre: str = ""    # Жанры из <genre> тегов (через запятую)
    file_path_normalized: str = ""  # Опционально: нормализованный путь
    
    def to_tuple(self):
        """Convert record to tuple for GUI table display."""
        return (
            self.file_path,
            self.metadata_authors,
            self.proposed_author,
            self.author_source,
            self.metadata_series,
            self.proposed_series,
            self.series_source,
            self.file_title,
            self.metadata_genre
        )


class AuthorNormalizer:
    """Helper class for author normalization operations."""
    
    def __init__(self, settings: Optional[SettingsManager] = None):
        """Initialize with SettingsManager.
        
        Args:
            settings: SettingsManager instance, loads from config.json if None
        """
        self.settings = settings or SettingsManager('config.json')
        self.logger = Logger()
        self._init_author_name()
    
    def _init_author_name(self):
        """Initialize AuthorName with the path to app_settings.json.

        male_names/female_names/author_initials_and_suffixes/name_particles
        live in app_settings.json, not config.json (see settings_manager.py's
        _MACHINE_KEYS split) — AuthorName._get_known_names() etc. read the
        given file directly with json.load(), so it must point there.

        Previously this read `self.settings._config_path`, an attribute that
        never existed on SettingsManager (the real one is `config_path`), so
        hasattr() was always False and this silently fell back to the
        literal string 'config.json' — meaning the known-names list was
        always empty and every author's Ф/И word order was decided by the
        much weaker suffix/vowel-ratio fallback heuristics instead.
        """
        config_path = self.settings.app_settings_path if hasattr(self.settings, 'app_settings_path') else 'config.json'
        AuthorName.set_config_path(config_path)
    
    def _apply_to_each_author(self, author: str, fn, separator: str = ', ') -> str:
        """Применить fn к каждому автору в строке с несколькими авторами.
        
        Если author содержит separator — сплитует, применяет fn к каждому, джойнит обратно.
        Иначе применяет fn напрямую.
        
        Args:
            author: Строка с одним или несколькими авторами
            fn: Функция (single_author: str) -> str
            separator: Разделитель авторов (по умолчанию ', ')
            
        Returns:
            Обработанная строка
        """
        if separator in author:
            return separator.join(
                fn(a.strip()) for a in author.split(separator) if a.strip()
            )
        return fn(author)

    def normalize_format(self, author: str, metadata_authors: str = "") -> str:
        """Нормализовать формат автора.
        
        "Иван Петров" → "Петров Иван"
        Если несколько авторов разделены '; ' или ', ' → нормализует каждого и разделяет запятой
        "А.Михайловский; А.Харников" → "Михайловский А., Харников А."
        "Дмитрий Зурков, Игорь Черепнев" → "Зурков Дмитрий, Черепнев Игорь"
        
        Если автор содержит неполное ФИ (только имя), использует metadata_authors для восстановления.
        Пример: "Белаш Александр; Людмила" + metadata_authors="Людмила Белаш; Александр Белаш"
        → "Белаш Александр; Людмила Белаш" (восстановлена фамилия для второго)
        
        Используется в PASS 3.
        
        Args:
            author: Имя автора в любом формате
            metadata_authors: Авторы из метаданных (для восстановления неполных ФИ)
            
        Returns:
            Нормализованное имя
        """
        if not author or author == "Сборник":
            return author

        # Ранний выход: строки вида "Фамилия Имя и другие" не нормализуем — они уже финальные
        if re.search(r'\s+(?:и\s+другие|и\s+др\.?|et\s+al\.?)\s*$', author, re.IGNORECASE):
            return author

        # Parse metadata_authors first
        metadata_authors_list = []
        if metadata_authors:
            # Normalize metadata_authors to list (handle both '; ' and ', ')
            meta_str = metadata_authors.replace('; ', ',').replace(';', ',')
            metadata_authors_list = [a.strip() for a in meta_str.split(',') if a.strip()]
        
        # Determine separator: '; ' (from folder_author_parser) or ', ' (from filename/metadata)
        separator = None
        if '; ' in author:
            separator = '; '
        elif ', ' in author:
            separator = ', '
        
        # Проверить есть ли несколько авторов в author или восстановить из metadata
        if separator:
            authors = author.split(separator)
            normalized_authors = []
            
            for single_author in authors:
                single_author = single_author.strip()
                if single_author:
                    # Проверить если это неполное ФИ (одно слово)
                    author_words = single_author.split()
                    if len(author_words) == 1 and metadata_authors_list:
                        # Одно слово - это имя, нужно найти фамилию из metadata
                        single_word = author_words[0]
                        # Ищем в metadata авторов, где это слово есть
                        for meta_author in metadata_authors_list:
                            meta_words = meta_author.split()
                            if single_word in meta_words:
                                # Используем полное ФИ из metadata
                                single_author = meta_author
                                break
                    
                    name_obj = AuthorName(single_author)
                    normalized = name_obj.normalized if name_obj.is_valid else single_author
                    
                    # If not normalized despite is_valid=True, try manual swap as fallback
                    # This handles cases where AuthorName recognizes the name as valid but doesn't swap
                    if normalized == single_author and len(single_author.split()) == 2:
                        # Name wasn't swapped - might be "Name Surname" format not recognized
                        words = single_author.split()
                        last_word = words[-1]
                        if last_word.lower().endswith(('ов', 'ев', 'ич', 'ович', 'ский', 'цкий', 'ова', 'ева', 'ина', 'янь', 'ень', 'ань', 'ш')):
                            # Looks like "Name Surname", swap to "Surname Name"
                            normalized = f"{last_word} {words[0]}"
                    
                    normalized_authors.append(normalized)
            
            # Sort authors alphabetically by surname, then by name
            def get_surname_key(author_str):
                words = author_str.split()
                if len(words) <= 1:
                    return (author_str.lower(), "")  # Return tuple consistently
                # After normalization in PASS 3, first word is ALWAYS the surname
                # No need to detect by endings - just use first word
                surname = words[0].lower()
                rest = ' '.join(words[1:]).lower()
                return (surname, rest)
            
            normalized_authors.sort(key=get_surname_key)
            # Объединить через запятую
            return ', '.join(normalized_authors)
        
        # Одиночный автор - проверить версию в metadata
        name_obj = AuthorName(author)
        normalized = name_obj.normalized if name_obj.is_valid else author

        # Если AuthorName выбросил ВСЕ инициалы (вернул только фамилию) — вернуть исходный.
        # Пример: "Умиралиев А" → AuthorName → "Умиралиев" (инициал выброшен) → вернуть "Умиралиев А".
        # НЕ применять если результат всё равно содержит инициал — значит AuthorName корректно
        # переставил порядок и убрал лишний инициал ("А. А Умиралиев" → "Умиралиев А." — правильно).
        # ФИНАЛЬНАЯ НОРМАЛИЗАЦИЯ в Pass 3 добавит точку к инициалу без точки.
        if len(normalized.split()) < len(author.split()):
            has_initial_in_result = bool(re.search(r'\b[А-ЯЁA-Z]\.?\b', normalized))
            if not has_initial_in_result:
                normalized = author

        # Если автор — одно слово (только фамилия) и metadata содержит ровно одного
        # автора с этой фамилией + инициал/имя → используем более полную версию.
        # Пример: proposed="Мосов", metadata="Мосов А" → normalized="Мосов А."
        if len(author.split()) == 1 and metadata_authors_list and len(metadata_authors_list) == 1:
            meta_full = metadata_authors_list[0]
            meta_words = meta_full.split()
            if len(meta_words) > 1:
                author_lower = author.lower().replace('ё', 'е')
                # Фамилия может быть первым или последним словом в строке метаданных
                first_lower = meta_words[0].lower().replace('ё', 'е').rstrip('.')
                last_lower = meta_words[-1].lower().replace('ё', 'е').rstrip('.')
                if first_lower == author_lower or last_lower == author_lower:
                    meta_name_obj = AuthorName(meta_full)
                    candidate = meta_name_obj.normalized if meta_name_obj.is_valid else meta_full
                    # Если AuthorName выбросил инициалы — использовать сырые данные из metadata.
                    # Pass 3 ФИНАЛЬНАЯ НОРМАЛИЗАЦИЯ добавит точку к инициалу.
                    if len(candidate.split()) < len(meta_words):
                        candidate = meta_full
                    normalized = candidate

        # Если в metadata есть несколько авторов И основной автор совпадает с одним из них
        # → используем всех авторов из metadata (восстановление потерянных соавторов)
        if metadata_authors_list and len(metadata_authors_list) > 1:
            # Проверить: есть ли слова из author в metadata авторах?
            author_words = set(author.lower().split())
            metadata_normalized = []
            
            # Нормализовать всех авторов из metadata
            for meta_author in metadata_authors_list:
                meta_words = set(meta_author.lower().split())
                # Если есть пересечение слов - это тот же автор
                if author_words & meta_words:  # intersection
                    # Используем всех авторов из metadata
                    for meta_author_full in metadata_authors_list:
                        meta_name_obj = AuthorName(meta_author_full)
                        meta_normalized = meta_name_obj.normalized if meta_name_obj.is_valid else meta_author_full
                        metadata_normalized.append(meta_normalized)
                    # Sort authors alphabetically by surname (first component after normalization)
                    metadata_normalized.sort()
                    return ', '.join(metadata_normalized)
        
        return normalized
    
    def apply_conversions(self, author: str) -> str:
        """Применить conversions к имени автора.
        
        "Гоблин (MeXXanik)" → "Гоблин MeXXanik"
        Если несколько авторов через запятую → применяет к каждому
        
        Используется в PASS 1, 5.
        
        Args:
            author: Имя автора
            
        Returns:
            Имя с применёнными conversions
        """
        if not author or author == "Сборник":
            return author
        
        conversions = self.settings.get_author_surname_conversions()

        def _convert_single(single: str) -> str:
            import re as _re
            result = single
            for pattern, replacement in conversions.items():
                # Skip if replacement is already present — prevents double-application
                # when pass3 and pass5 both call apply_conversions on the same string.
                if replacement in result:
                    continue
                # Используем границы слов чтобы «Бирюк» не срабатывал внутри «Бирюков»
                result = _re.sub(
                    r'(?<![\w])' + _re.escape(pattern) + r'(?![\w])',
                    replacement,
                    result
                )
            return result

        return self._apply_to_each_author(author, _convert_single)
    
    def expand_abbreviation(self, author: str, authors_map: Dict[str, List[str]]) -> str:
        """Раскрыть аббревиатуру в имени автора.
        
        "И.Петров" → "Иван Петров" (если найдено в authors_map)
        Если несколько авторов через запятую → раскрывает каждого
        
        Используется в PASS 6.
        
        Args:
            author: Имя автора с возможной аббревиатурой
            authors_map: Словарь {фамилия.lower(): [полные имена]}
            
        Returns:
            Имя с раскрытой аббревиатурой или исходное имя
        """
        if not author or "." not in author:
            return author

        return self._apply_to_each_author(
            author,
            lambda a: self._expand_single_abbreviation(a, authors_map)
        )
    
    def _expand_single_abbreviation(self, author: str, authors_map: Dict[str, List[str]]) -> str:
        """Раскрыть аббревиатуру в одном имени автора.
        
        Args:
            author: Одно имя автора ("А.Харников", "А. Харников", и т.д.)
            authors_map: Словарь {фамилия.lower(): [полные имена]}
                         Ключи и значения в формате "Фамилия Имя"
            
        Returns:
            Раскрытое имя или исходное
        """
        if not author or "." not in author:
            return author
        
        # Паттерн для поиска "X.Фамилия" или "Фамилия X." или "X. Фамилия" или "Фамилия X."
        pattern = r'([А-Я]\.)\s*([А-ЯЁа-яё]+)|([А-ЯЁа-яё]+)\s*([А-Я]\.)'
        match = re.search(pattern, author)
        
        if not match:
            return author
        
        # Определить фамилию и инициал
        if match.group(2):
            # Формат: "И.Фамилия" или "И. Фамилия"
            initial = match.group(1)[0]  # 'А'
            surname = match.group(2)       # 'Харников'
        else:
            # Формат: "Фамилия И." или "Фамилия И."
            surname = match.group(3)       # 'Харников'
            initial = match.group(4)[0]   # 'А'
        
        surname_lower = surname.lower()
        
        # Первый попыт: найти в авторах где фамилия - первое слово, имя начинается с инициала
        if surname_lower in authors_map:
            full_names = authors_map[surname_lower]
            # Two passes: prefer non-abbreviated (no dots after surname) over abbreviated forms.
            # This ensures "Умиралиев Арман Аскаржанович" wins over "Умиралиев А. А."
            best_abbr = None
            for full_name in full_names:
                parts = full_name.split()
                # full_name = "Харников Александр" (Фамилия Имя)
                if len(parts) >= 2:
                    # Проверяем первая часть - фамилия
                    if parts[0].lower() == surname_lower and parts[1][0].upper() == initial:
                        if '.' in ' '.join(parts[1:]):
                            # Still abbreviated — save as fallback
                            if best_abbr is None:
                                best_abbr = full_name
                            continue
                        return full_name  # Non-abbreviated form wins immediately
            # Only abbreviated forms found — use the first one as fallback (if it's not same as input)
            if best_abbr and best_abbr != author:
                return best_abbr
        
        # Второй попыт: найти в авторах где имя - первое слово (обратный порядок)
        # Может быть "Александр Харников"
        if initial.lower() in authors_map:
            full_names = authors_map[initial.lower()]
            for full_name in full_names:
                parts = full_name.split()
                # Проверяем есть ли фамилия в конце
                if len(parts) >= 2 and parts[-1].lower() == surname_lower:
                    return full_name
        
        return author
