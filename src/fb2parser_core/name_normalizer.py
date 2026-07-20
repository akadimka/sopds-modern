#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Author Name Normalizer - Standalone module for normalizing author names

This module provides a standalone, reusable name normalization logic that can be
used in other projects. It normalizes author names to a standard format:
"Фамилия Имя [Отчество]" (Lastname Firstname [Patronymic])

Usage:
    from name_normalizer import AuthorName
    
    name = AuthorName("Иван Петров")
    print(name.normalized)  # "Петров Иван"
    
    name2 = AuthorName("Петров И.П.")
    print(name2.normalized)  # "Петров И.П."

Configuration:
    The normalizer can use an optional config.json file with the following keys:
    - filename_blacklist: Words/phrases to exclude (folder/series names)
    - author_initials_and_suffixes: Suffixes to ignore (мл, ст, etc.)
    - male_names: Known male first names for order detection
    - female_names: Known female first names for order detection
    
    If config.json is not found, the normalizer works with built-in defaults.

Нормализатор имён авторов - отдельный модуль для нормализации имён авторов

Этот модуль предоставляет отдельную, переиспользуемую логику нормализации имён,
которая может быть использована в других проектах. Нормализует имена авторов
в стандартный формат: "Фамилия Имя [Отчество]"

Конфигурация:
    Нормализатор может использовать опциональный файл config.json со следующими ключами:
    - filename_blacklist: Слова/фразы для исключения (имена папок/серий)
    - author_initials_and_suffixes: Суффиксы для игнорирования (мл, ст, и т.д.)
    - male_names: Известные мужские имена для определения порядка
    - female_names: Известные женские имена для определения порядка
"""

import re
import json
from typing import Optional, Tuple, Set
from pathlib import Path


class AuthorName:
    """Represents a single author name with normalization capabilities.
    
    Обрабатывает одно имя автора с нормализацией.
    
    Handles formats:
    - "Иван Петров" (Firstname Lastname)
    - "Петров Иван" (Lastname Firstname)
    - "Петров Иван Сергеевич" (Lastname Firstname Patronymic)
    - "И. П." (initials)
    - "Гоблин (MeXXanik)" (Pseudonym with real name in parentheses)
    - "MeXXanik Гоблин" (Real name with pseudonym)
    """
    
    # Class-level cache for known initials and suffixes from config
    _known_initials_and_suffixes = None
    _known_names_cache = None  # Cache for known male and female names
    _filename_blacklist_cache = None
    _name_particles_cache = None  # Cache for name particles (де, ван, фон…)
    _config_path = None  # Allow custom config path
    
    def __init__(self, raw_name: str):
        """Initialize with raw author name string.
        
        Args:
            raw_name: Author name in any format
        """
        self.raw_name = raw_name.strip() if raw_name else ""
        # Заменить ё на е для унификации на всех этапах обработки
        self.raw_name = self.raw_name.replace('ё', 'е')
        self.is_valid = self._validate()
        self.parts = self._extract_parts()  # (lastname, firstname, patronymic)
        self.normalized = self._normalize()
    
    @classmethod
    def set_config_path(cls, config_path: str):
        """Set custom config file path for loading name lists.
        
        Установить пользовательский путь к файлу конфигурации.
        
        Args:
            config_path: Path to config.json file
        """
        cls._config_path = Path(config_path)
        # Clear caches to reload with new config
        cls._filename_blacklist_cache = None
        cls._known_initials_and_suffixes = None
        cls._known_names_cache = None
    
    @classmethod
    def _get_config_path(cls) -> Optional[Path]:
        """Get config file path.
        
        Получить путь к файлу конфигурации.
        """
        if cls._config_path:
            return cls._config_path
        
        # Try to find config.json in the same directory as this module
        try:
            module_dir = Path(__file__).parent
            default_config = module_dir / 'config.json'
            if default_config.exists():
                return default_config
        except Exception:
            pass
        
        return None
    
    def _validate(self) -> bool:
        """Check if this is a valid author name (not garbage, numbers, etc).
        
        Проверить, валидное ли это имя автора (не мусор, не цифры, и т.д.).
        
        Invalid names:
        - Empty or single character
        - Contains only numbers
        - Too short (< 2 chars meaningful)
        - Contains URLs, paths, etc.
        - Matches filename blacklist with 60%+ similarity (folder/series names)
        
        Returns: True if name looks like real author name
        """
        if not self.raw_name or len(self.raw_name) < 2:
            return False
        
        # Check if matches blacklist (60%+ similarity)
        if self._contains_blacklist_word(threshold=0.6):
            return False
        
        # Remove punctuation for validation
        clean = re.sub(r'[,;:!?\-–—()[\]{}«»""\'"`]', ' ', self.raw_name)
        clean = clean.strip()
        
        # Check if it's only numbers
        if clean.isdigit():
            return False
        
        # Check if it looks like a path or URL
        if '/' in self.raw_name or '\\' in self.raw_name:
            return False
        
        # Dots are allowed for initials, but not multiple in a row
        if re.search(r'\.{2,}', self.raw_name):
            return False
        
        # Check minimum meaningful content
        words = [w for w in clean.split() if w]
        if len(words) < 1:
            return False
        
        # At least one word must be > 2 characters (not just initials)
        has_word = any(len(w) > 1 for w in words)
        if not has_word:
            return False
        
        return True
    
    @classmethod
    def _get_filename_blacklist(cls) -> Set[str]:
        """Load filename blacklist from config file.
        
        Загрузить blacklist имен файлов из конфига.
        
        Returns: Set of blacklisted words/phrases
        """
        if cls._filename_blacklist_cache is None:
            try:
                config_path = cls._get_config_path()
                if config_path and config_path.exists():
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        blacklist = config.get('filename_blacklist', [])
                        cls._filename_blacklist_cache = set(w.lower() for w in blacklist if w)
                else:
                    cls._filename_blacklist_cache = set()
            except Exception:
                cls._filename_blacklist_cache = set()
        
        return cls._filename_blacklist_cache
    
    def _contains_blacklist_word(self, threshold: float = 0.6) -> bool:
        """Check if author name matches blacklist with 60%+ similarity.
        
        Проверить совпадение с blacklist на 60%+ сходства.
        
        Uses substring inclusion to find matching words/phrases in blacklist.
        If name contains a blacklist word that's 60%+ of the name length,
        it's probably a folder/series name, not an author name.
        
        Args:
            threshold: Inclusion ratio threshold (default 0.6 = 60%)
        
        Returns: True if name contains blacklist entry at 60%+ threshold
        """
        blacklist = self._get_filename_blacklist()
        if not blacklist:
            return False
        
        name_lower = self.raw_name.lower()
        name_len = len(name_lower)
        
        for bl_word in blacklist:
            bl_word_lower = bl_word.lower()
            bl_word_len = len(bl_word_lower)
            
            # Substring check with inclusion ratio threshold
            if bl_word_lower in name_lower:
                inclusion_ratio = bl_word_len / name_len
                if inclusion_ratio >= threshold:
                    return True
        
        return False
    
    @classmethod
    def _get_known_initials_and_suffixes(cls) -> Set[str]:
        """Load known initials and suffixes from config file.
        
        Загрузить известные инициалы и сокращения из конфига.
        
        Returns: Set of known suffixes (e.g., {'мл', 'ст', 'младший', 'старший'})
        """
        if cls._known_initials_and_suffixes is None:
            try:
                config_path = cls._get_config_path()
                if config_path and config_path.exists():
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        cls._known_initials_and_suffixes = set(
                            config.get('author_initials_and_suffixes', [])
                        )
                else:
                    cls._known_initials_and_suffixes = set()
            except Exception:
                cls._known_initials_and_suffixes = set()
        
        return cls._known_initials_and_suffixes
    
    @classmethod
    def _get_known_names(cls) -> Set[str]:
        """Load known male and female names from config file (with normalization).
        
        Загрузить известные мужские и женские имена из конфига с нормализацией.
        
        Returns: Set of known names (lowercase, with ё replaced by е)
        """
        if cls._known_names_cache is None:
            try:
                config_path = cls._get_config_path()
                if config_path and config_path.exists():
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        male_names = config.get('male_names', [])
                        female_names = config.get('female_names', [])
                        # Normalize: lowercase + replace ё with е for consistent matching
                        cls._known_names_cache = set(
                            w.lower().replace('ё', 'е') 
                            for w in (male_names + female_names) if w
                        )
                else:
                    cls._known_names_cache = set()
            except Exception:
                cls._known_names_cache = set()
        
        return cls._known_names_cache
    
    @classmethod
    def _get_name_particles(cls) -> frozenset:
        """Load name particles from config.json (де, ван, фон, ди…)."""
        if cls._name_particles_cache is None:
            try:
                config_path = cls._get_config_path()
                if config_path and config_path.exists():
                    with open(config_path, 'r', encoding='utf-8') as f:
                        config = json.load(f)
                        lst = config.get('name_particles', [])
                        cls._name_particles_cache = frozenset(p.lower() for p in lst)
                else:
                    cls._name_particles_cache = frozenset()
            except Exception:
                cls._name_particles_cache = frozenset()
        return cls._name_particles_cache

    def _extract_parts(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Extract lastname, firstname, patronymic from raw name.
        
        Извлечь фамилию, имя, отчество из исходного имени.
        
        Strategy:
        1. Check for known patterns that should NOT be normalized (exceptions)
           - "Pseudonym (RealName)" patterns like "Гоблин (MeXXanik)"
           - "Surname I.O." format like "Живой А.Я." or "Живой А. Я."
        2. If name has parentheses pattern "X (Y)", extract both parts
        3. Split remaining text into words
        4. Try to identify which is lastname/firstname based on:
           - Russian naming patterns (patronymic ends with -ович, -евич, etc.)
           - Capitalization
           - Word order and known names
        
        Returns: (lastname, firstname, patronymic) tuple with None for missing parts
        """
        if not self.is_valid:
            return (None, None, None)
        
        # EXCEPTION 1: Check for "Surname I.O." format (requires dots for initials)
        pattern_surname_initials = r'^([А-Яа-яЁё]+)\s+([А-Яа-яЁё]\.)\s*([А-Яа-яЁё]\.)?$'
        if re.match(pattern_surname_initials, self.raw_name):
            return (self.raw_name, None, None)
        
        # EXCEPTION 2: Check for "Pseudonym (RealName)" pattern
        if re.search(r'\([^)]+\)', self.raw_name):
            main_part = re.sub(r'\([^)]*\)', '', self.raw_name).strip()
            paren_match = re.search(r'\(([^)]+)\)', self.raw_name)
            paren_content = paren_match.group(1).strip() if paren_match else None
            
            if main_part and paren_content and len(main_part.split()) == 1:
                return (self.raw_name, None, None)
        
        # NORMAL PATH: Extract parts for normalization
        paren_match = re.search(r'\(([^)]+)\)', self.raw_name)
        paren_content = paren_match.group(1).strip() if paren_match else None
        main_part = re.sub(r'\([^)]*\)', '', self.raw_name).strip()
        
        all_text_parts = []
        if main_part:
            all_text_parts.append(main_part)
        # Only merge paren content if main_part is empty or single-word.
        # Multi-word main part like "Абрахам Дэниел (Джеймс С.А. Кори)" should use only main_part
        # (parenthesized content is a pseudonym, not part of the canonical name).
        if paren_content and len(main_part.split()) <= 1:
            all_text_parts.append(paren_content)
        all_text = " ".join(all_text_parts)
        
        words = [w for w in all_text.split() if w and len(w) > 1]
        
        if len(words) == 0:
            return (None, None, None)
        elif len(words) == 1:
            return (words[0], None, None)
        
        known_suffixes = self._get_known_initials_and_suffixes()
        patronymic = None
        remaining_words = words[:]

        # Special case: "Имя Отчество Фамилия" (First Patronymic Surname)
        # If exactly 3 words and the MIDDLE word is a patronymic, handle it explicitly
        # before the generic "check last word" logic picks up the surname as patronymic.
        # Example: "Ольга Ивановна Тарасевич" → (Тарасевич, Ольга, Ивановна)
        if len(words) == 3 and self._is_patronymic(words[1]) and not self._is_patronymic(words[0]):
            return (words[2], words[0], words[1])

        # Check if last word is patronymic
        if self._is_patronymic(remaining_words[-1]):
            patronymic = remaining_words[-1]
            remaining_words = remaining_words[:-1]
        
        # Filter out known suffixes from the end
        while remaining_words and remaining_words[-1].lower() in known_suffixes:
            remaining_words = remaining_words[:-1]
        
        if len(remaining_words) == 0:
            return (None, None, patronymic)
        elif len(remaining_words) == 1:
            return (remaining_words[0], None, patronymic)

        # PARTICLE CHECK: если в имени есть частица (де, ван, фон, ди…),
        # всё начиная с первой частицы — составная фамилия («де л'Эн», «ван дер Берг»).
        # Предшествующие слова — имя/имена.
        # Пример: «Аликс де л'Эн» → lastname='де л\'Эн', firstname='Аликс'
        _particles = self._get_name_particles()
        if _particles:
            for _pi, _pw in enumerate(remaining_words):
                if _pw.lower() in _particles:
                    compound_surname = ' '.join(remaining_words[_pi:])
                    firstname_part = ' '.join(remaining_words[:_pi]) or None
                    return (compound_surname, firstname_part, patronymic)

        if len(remaining_words) == 2:
            known_names = self._get_known_names()
            word0_lower = remaining_words[0].lower()
            word1_lower = remaining_words[1].lower()

            word0_is_known_name = word0_lower in known_names
            word1_is_known_name = word1_lower in known_names

            # A bare initial (single letter, optional dot — "С.", "А") is NEVER a
            # surname, regardless of what the vowel-ratio/suffix heuristics below
            # would say (an initial has ~0 vowels, which used to make it win the
            # "surnames have fewer vowels" tiebreaker). The other word is the
            # surname. Example: "С. Витицкий" → lastname="Витицкий",
            # firstname="С." (was backwards: lastname="С.", firstname="Витицкий").
            _is_initial0 = len(word0_lower.rstrip('.')) == 1
            _is_initial1 = len(word1_lower.rstrip('.')) == 1
            if _is_initial0 and not _is_initial1:
                return (remaining_words[1], remaining_words[0], patronymic)
            elif _is_initial1 and not _is_initial0:
                return (remaining_words[0], remaining_words[1], patronymic)

            if word0_is_known_name and not word1_is_known_name:
                return (remaining_words[1], remaining_words[0], patronymic)
            elif word1_is_known_name and not word0_is_known_name:
                return (remaining_words[0], remaining_words[1], patronymic)
            else:
                # Use heuristic based on Russian surname patterns
                surname_suffixes = (
                    'ов', 'ева', 'ова', 'ев', 'ева', 'ская', 'ский', 'ин', 'ина', 'ын', 'ына',
                    'ан', 'ана', 'ян', 'яна', 'ов', 'ова', 'ер', 'ера', 'ор', 'ора', 'ич', 'иц',
                    'ей', 'ко', 'ли', 'ло', 'ды', 'ца', 'цов', 'иев', 'ович', 'евич', 'овна', 'евна'
                )
                
                word0_ends = word0_lower[-2:] if len(word0_lower) >= 2 else ''
                word1_ends = word1_lower[-2:] if len(word1_lower) >= 2 else ''
                
                word0_is_surname = word0_ends in surname_suffixes
                word1_is_surname = word1_ends in surname_suffixes
                
                if word0_is_surname and not word1_is_surname:
                    return (remaining_words[0], remaining_words[1], patronymic)
                elif word1_is_surname and not word0_is_surname:
                    return (remaining_words[1], remaining_words[0], patronymic)
                else:
                    # Use vowel ratio heuristic: surnames have fewer vowels
                    def count_vowels(word):
                        vowels = 'aeiouAEIOU' + 'аеёиоуыэюяАЕЁИОУЫЭЮЯ'
                        return sum(1 for c in word if c in vowels)
                    
                    word0_vowels = count_vowels(remaining_words[0])
                    word1_vowels = count_vowels(remaining_words[1])
                    
                    word0_vowel_ratio = word0_vowels / len(remaining_words[0]) if remaining_words[0] else 0
                    word1_vowel_ratio = word1_vowels / len(remaining_words[1]) if remaining_words[1] else 0
                    
                    if word0_vowel_ratio < word1_vowel_ratio:
                        return (remaining_words[0], remaining_words[1], patronymic)
                    else:
                        return (remaining_words[1], remaining_words[0], patronymic)
        else:  # 3+ remaining words
            patronymic_candidates = []
            for i, word in enumerate(remaining_words):
                if self._is_patronymic(word):
                    patronymic_candidates.append(i)
            
            if patronymic_candidates:
                patronymic_idx = patronymic_candidates[0]
                if patronymic_idx == 1 and len(remaining_words) >= 3:
                    firstname = remaining_words[0]
                    found_patronymic = remaining_words[patronymic_idx]
                    lastname = remaining_words[-1]
                    return (lastname, firstname, found_patronymic)
                elif patronymic_idx > 1:
                    firstname = ' '.join(remaining_words[:patronymic_idx])
                    found_patronymic = remaining_words[patronymic_idx]
                    lastname = remaining_words[-1]
                    return (lastname, firstname, found_patronymic)
            
            # Handle "К. Роберт Каргилл" → "Каргилл К. Роберт"
            # (initial at position 0: short word ending with dot)
            first_word = remaining_words[0]
            if (len(remaining_words) == 3
                    and len(first_word) <= 2
                    and first_word.endswith('.')):
                firstname = first_word + ' ' + remaining_words[1]
                lastname = remaining_words[2]
                return (lastname, firstname, patronymic)

            middle_word = remaining_words[1]
            if len(middle_word) <= 3 and middle_word.endswith('.'):
                firstname = remaining_words[0] + ' ' + middle_word
                lastname = remaining_words[-1] if len(remaining_words) > 2 else None
                return (lastname, firstname, patronymic)
            else:
                lastname = remaining_words[-1]
                firstname = remaining_words[0]
                
                surname_found_in_middle = False
                for i in range(1, len(remaining_words) - 1):
                    word_lower = remaining_words[i].lower()
                    word_ends = word_lower[-2:] if len(word_lower) >= 2 else ''
                    surname_suffixes = (
                        'ов', 'ева', 'ова', 'ев', 'ева', 'ская', 'ский', 'ин', 'ина', 'ын', 'ына',
                        'ан', 'ана', 'ян', 'яна', 'ов', 'ова', 'ер', 'ера', 'ор', 'ора', 'ич', 'иц'
                    )
                    if word_ends in surname_suffixes:
                        lastname = remaining_words[i]
                        surname_found_in_middle = True
                        break
                
                # If no middle-word surname found, check every word against the known
                # first-name list (not just the first/last word). The one word that is
                # NOT a known first name is by far the strongest surname signal we have —
                # position alone is unreliable, since a middle/second given name can
                # coincide with a known name too.
                # Example: "Брэдбери Рэй Дуглас" — "Рэй" and "Дуглас" are both known
                # first names, "Брэдбери" is not → lastname="Брэдбери",
                # firstname="Рэй Дуглас" (original order preserved), regardless of position.
                if not surname_found_in_middle:
                    known_names = self._get_known_names()
                    unknown_words = [w for w in remaining_words if w.lower() not in known_names]
                    if len(unknown_words) == 1:
                        lastname = unknown_words[0]
                        firstname = ' '.join(w for w in remaining_words if w != lastname)
                    else:
                        # Ambiguous (0, 2+ unknown words) — fall back to the old
                        # position-based heuristic: if NEITHER end word is known, assume
                        # the input is already in ФИ order (Фамилия Имя …), e.g. foreign
                        # names from filenames: "Линдквист Йон Айвиде", "Толкин Джон Рональд".
                        first_is_known = remaining_words[0].lower() in known_names
                        last_is_known = remaining_words[-1].lower() in known_names
                        if not first_is_known and not last_is_known:
                            lastname = remaining_words[0]
                            firstname = ' '.join(remaining_words[1:])

                return (lastname, firstname, patronymic)
    
    @staticmethod
    def _is_patronymic(word: str) -> bool:
        """Check if word looks like Russian patronymic.
        
        Проверить, похоже ли слово на русское отчество.
        
        Patronymic patterns:
        - Ends with -ович, -евич (male)
        - Ends with -овна, -евна (female)
        """
        if not word:
            return False
        
        word_lower = word.lower()
        patronymic_endings = [
            'ович', 'евич',  # Male patronymics
            'овна', 'евна',  # Female patronymics
        ]
        return any(word_lower.endswith(ending) for ending in patronymic_endings)
    
    def _normalize(self) -> str:
        """Normalize to standard format: Lastname Firstname [Patronymic].
        
        Нормализовать в стандартный формат: Фамилия Имя [Отчество].
        Также заменяет ё на е для унификации.
        
        Returns: Normalized name string (or empty if invalid or matches blacklist)
        """
        # Special token: "Коллектив авторов" = anthology, not a person's name
        _raw_lower = self.raw_name.lower()
        if _raw_lower in ('коллектив авторов', 'collective authors', 'various authors'):
            return 'Сборник'

        if not self.is_valid:
            return ""
        
        lastname, firstname, patronymic = self.parts
        
        parts_list = []
        if lastname:
            parts_list.append(lastname)
        if firstname:
            parts_list.append(firstname)
        if patronymic:
            parts_list.append(patronymic)
        
        result = " ".join(parts_list) if parts_list else ""
        
        # Заменить ё на е для унификации
        result = result.replace('ё', 'е')
        
        # Final check: if normalized result matches blacklist with 60%+ similarity, reject it
        if result and self._contains_blacklist_word(threshold=0.6):
            return ""
        
        return result
    
    def completeness_score(self) -> int:
        """Return completeness score: 0=invalid, 1=initials, 2=ФИ, 3=ФИО.
        
        Возвращает оценку полноты: 0=невалидно, 1=инициалы, 2=ФИ, 3=ФИО.
        """
        if not self.is_valid:
            return 0
        
        lastname, firstname, patronymic = self.parts
        score = 0
        if lastname:
            score += 1
        if firstname:
            score += 1
        if patronymic:
            score += 1
        
        return score
    
    def __str__(self) -> str:
        return self.normalized or self.raw_name
    
    def __repr__(self) -> str:
        return f"AuthorName(raw='{self.raw_name}', normalized='{self.normalized}', valid={self.is_valid})"


# Convenience functions for simple usage
def normalize_author_name(name: str) -> str:
    """Normalize a single author name to standard format.
    
    Нормализовать одно имя автора в стандартный формат.
    
    Args:
        name: Author name in any format
    
    Returns:
        Normalized name string ("Фамилия Имя [Отчество]")
    
    Examples:
        >>> normalize_author_name("Иван Петров")
        'Петров Иван'
        
        >>> normalize_author_name("Живой А.Я.")
        'Живой А.Я.'
        
        >>> normalize_author_name("Гоблин (MeXXanik)")
        'Гоблин (MeXXanik)'
    """
    author = AuthorName(name)
    return author.normalized or author.raw_name


def validate_author_name(name: str) -> bool:
    """Check if a name is valid author name (not garbage, paths, etc).
    
    Проверить, валидное ли это имя автора (не мусор, пути, и т.д.).
    
    Args:
        name: Author name string
    
    Returns:
        True if name looks like real author name, False otherwise
    """
    author = AuthorName(name)
    return author.is_valid


if __name__ == '__main__':
    # Simple tests
    test_names = [
        "Иван Петров",
        "Петров Иван",
        "Петров Иван Сергеевич",
        "И. П.",
        "Гоблин (MeXXanik)",
        "Живой А.Я.",
        "компиляция",  # Should be invalid (blacklist)
        "123",  # Should be invalid (numbers only)
    ]
    
    print("Testing AuthorName normalizer:")
    print("-" * 60)
    
    for name in test_names:
        author = AuthorName(name)
        print(f"Raw:        '{name}'")
        print(f"Valid:      {author.is_valid}")
        print(f"Normalized: '{author.normalized}'")
        print(f"Completeness: {author.completeness_score()}")
        print("-" * 60)
