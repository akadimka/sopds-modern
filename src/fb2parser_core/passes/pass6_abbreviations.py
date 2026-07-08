"""
PASS 6: Expand author abbreviations to full names.
"""

from typing import List, Dict, Optional
import re
from ..author_normalizer_extended import AuthorNormalizer
from ..settings_manager import SettingsManager
from ..logger import Logger


_APOSTROPHE_VARIANTS = str.maketrans({
    '‘': "'",  # LEFT SINGLE QUOTATION MARK
    '’': "'",  # RIGHT SINGLE QUOTATION MARK
    'ʼ': "'",  # MODIFIER LETTER APOSTROPHE
    '´': "'",  # ACUTE ACCENT
    '`': "'",  # GRAVE ACCENT / BACKTICK
    'ʹ': "'",  # MODIFIER LETTER PRIME
})

def _norm_apos(s: str) -> str:
    """Normalize all apostrophe/quote variants to straight apostrophe for key comparison."""
    return s.translate(_APOSTROPHE_VARIANTS)


class Pass6Abbreviations:
    """PASS 6: Expand author abbreviations to full names.
    
    Build a dictionary of full author names and use it to expand abbreviations:
    - "Фамилия И." → "Фамилия Имя"
    - "И.Фамилия" → "Имя Фамилия" 
    - "А.Михайловский, А.Харников" → "Александр Михайловский, Александр Харников" (multi-author)
    """
    
    def __init__(self, logger, settings=None):
        """Initialize PASS 6.
        
        Args:
            logger: Logger instance
            settings: Optional shared SettingsManager
        """
        self.logger = logger
        self.py_logger = logger  # Reference to system logger
        try:
            self.settings = settings or SettingsManager('config.json')
        except:
            self.settings = None
        self.normalizer = AuthorNormalizer(self.settings)
    
    def execute(self, records: List) -> None:
        """Execute PASS 6: Expand abbreviations and incomplete author names.
        
        Two-pass algorithm:
        1. First pass: Build complete authors_map from ALL records
        2. Second pass: Expand abbreviations and incomplete names using full map
        
        This allows forward references: a file can be expanded using information
        from files that appear later in the list.
        
        Two operations:
        1. Expand abbreviations: "Петров И." → "Петров Иван"
        2. Expand incomplete names: "Живой" → "Живой Алексей" (using cache from other files)
        
        Args:
            records: List of BookRecord objects to process
        """
        print("[PASS 6] Expanding abbreviations and incomplete names...")

        # PRE-PASS: Expand single-word surnames using the record's OWN metadata_authors.
        # Handles cases like «О'Рейлли» (filename) + metadata «Брайан О'Рейлли» → «О'Рейлли Брайан».
        # Must run per-record (not via global authors_map) because different books can have
        # authors with the same surname but different first names (e.g. Джон vs Мэгги О'Фаррелл).
        meta_expand_count = 0
        for record in records:
            if not record.proposed_author or record.proposed_author == "Сборник":
                continue
            if not record.metadata_authors:
                continue
            if 'filename' not in record.author_source:
                continue

            # Parse metadata_authors into normalized list once per record
            sep_m = '; ' if '; ' in record.metadata_authors else (', ' if ', ' in record.metadata_authors else None)
            meta_parts_raw = record.metadata_authors.split(sep_m) if sep_m else [record.metadata_authors]
            meta_normalized_list = [
                self.normalizer.normalize_format(m.strip())
                for m in meta_parts_raw if m.strip()
            ]

            def _try_expand_single(proposed_word: str) -> str | None:
                """Expand a single-word surname using record metadata.
                Tries exact match first, then prefix match (gender inflections like Савенко→Савенкова).
                Returns expanded name or None."""
                pw_norm = _norm_apos(proposed_word.lower())
                for meta_norm in meta_normalized_list:
                    if not meta_norm:
                        continue
                    meta_words = meta_norm.split()
                    if not meta_words or len(meta_words) < 2:
                        continue
                    meta_surname = _norm_apos(meta_words[0].lower())
                    # Exact match
                    if meta_surname == pw_norm:
                        return _norm_apos(meta_norm)
                    # Prefix match: "савенко" → "савенкова" (len >= 5, unambiguous)
                    if len(pw_norm) >= 5 and meta_surname.startswith(pw_norm):
                        return _norm_apos(meta_norm)
                return None

            # Single-word proposed_author
            if ' ' not in record.proposed_author:
                expanded = _try_expand_single(record.proposed_author)
                if expanded:
                    record.proposed_author = expanded
                    record.author_source = record.author_source + '+meta_expanded'
                    meta_expand_count += 1
                continue

            # Multi-author: check each part individually
            sep_a = '; ' if '; ' in record.proposed_author else (', ' if ', ' in record.proposed_author else None)
            if not sep_a:
                continue
            author_parts = record.proposed_author.split(sep_a)
            changed = False
            new_parts = []
            for part in author_parts:
                part = part.strip()
                # Only expand single-word parts (no space = no first name yet)
                if ' ' not in part:
                    expanded = _try_expand_single(part)
                    if expanded:
                        new_parts.append(expanded)
                        changed = True
                        continue
                new_parts.append(part)
            if changed:
                record.proposed_author = sep_a.join(new_parts)
                record.author_source = record.author_source + '+meta_expanded'
                meta_expand_count += 1
        if meta_expand_count:
            print(f"[PASS 6] Expanded {meta_expand_count} single-word surnames using record metadata")
            self.logger.log(f"[PASS 6] Expanded {meta_expand_count} single-word surnames via metadata")

        # PASS 1: Build complete authors map from ALL records
        print("[PASS 6]   Building author cache from all records...")
        authors_map = self._build_authors_map(records)

        # PASS 2: Expand abbreviations and incomplete names
        expanded_count = 0

        for record in records:
            if record.proposed_author == "Сборник":
                continue

            original = record.proposed_author

            # Check for multi-author case with both separators ('; ' from folder, ', ' from filename)
            if '; ' in record.proposed_author:
                authors = record.proposed_author.split('; ')
                expanded_authors = [self._expand_author(a, authors_map) for a in authors]
                record.proposed_author = '; '.join(expanded_authors)
            elif ', ' in record.proposed_author:
                authors = record.proposed_author.split(', ')
                expanded_authors = [self._expand_author(a, authors_map) for a in authors]
                record.proposed_author = ', '.join(expanded_authors)
            else:
                candidate = self._expand_author(original, authors_map)
                # Don't upgrade via authors_map if expansion adds a first name that
                # contradicts the record's own metadata (different person with same surname).
                # E.g. folder "Скиф" → authors_map has "Скиф Анна" (different author).
                if candidate != original and len(original.split()) == 1:
                    # folder_dataset: имя папки авторитетно — не расширяем через authors_map
                    # ни при каких условиях (в т.ч. когда metadata содержит расширение),
                    # чтобы все файлы папки имели одинакового автора.
                    if record.author_source == 'folder_dataset':
                        candidate = original
                    else:
                        meta = (getattr(record, 'metadata_authors', '') or '').lower()
                        if meta:
                            # Verify the new first name is in metadata
                            new_words = [w.lower() for w in candidate.split()[1:] if len(w) > 2]
                            if new_words and not any(w in meta for w in new_words):
                                candidate = original  # metadata contradicts expansion
                record.proposed_author = candidate

            if record.proposed_author != original:
                expanded_count += 1
        
        self.logger.log(f"[PASS 6] Expanded {expanded_count} author names")

        # Дедупликация: убрать повторяющихся авторов в proposed_author
        # (возникает когда псевдоним и реальное имя расширяются в одно и то же)
        dedup_count = 0
        for record in records:
            if not record.proposed_author or record.proposed_author == "Сборник":
                continue
            sep = '; ' if '; ' in record.proposed_author else (', ' if ', ' in record.proposed_author else None)
            if sep:
                parts = record.proposed_author.split(sep)
                seen_lower: list = []
                unique: list = []
                for p in parts:
                    key = p.strip().lower().replace('ё', 'е')
                    if key not in seen_lower:
                        seen_lower.append(key)
                        unique.append(p.strip())
                if len(unique) < len(parts):
                    record.proposed_author = sep.join(unique)
                    dedup_count += 1
        if dedup_count:
            self.logger.log(f"[PASS 6] Deduplicated {dedup_count} author strings")

        # Применяем author_surname_conversions к финальному значению автора
        # (покрывает случаи когда парсер вернул Верн. Жюль вместо Верн Жюль)
        _conversions = (self.settings.get_author_surname_conversions() if self.settings else None) or {}
        conv_count = 0
        for record in records:
            if not record.proposed_author or not _conversions:
                continue
            if record.proposed_author in _conversions:
                new_val = _conversions[record.proposed_author]
                if new_val != record.proposed_author:
                    record.proposed_author = new_val
                    conv_count += 1
        if conv_count:
            self.logger.log(f"[PASS 6] Applied surname conversions to {conv_count} author values")

        # Сортировка соавторов по фамилии (алфавитный порядок)
        sort_count = 0
        for record in records:
            if not record.proposed_author or record.proposed_author == "Сборник":
                continue
            sep = '; ' if '; ' in record.proposed_author else (', ' if ', ' in record.proposed_author else None)
            if not sep:
                continue
            parts = [p.strip() for p in record.proposed_author.split(sep)]
            if len(parts) < 2:
                continue
            # Не сортируем выражения вида "Аркадий и Борис Стругацкие" (нет разделителя → уже одна строка)
            sorted_parts = sorted(parts, key=lambda x: x.split()[0].lower() if x.split() else x.lower())
            if sorted_parts != parts:
                record.proposed_author = sep.join(sorted_parts)
                sort_count += 1
        if sort_count:
            self.logger.log(f"[PASS 6] Sorted co-authors alphabetically in {sort_count} records")

        # Финальная проверка: серия не может совпадать с автором
        cleared_count = 0
        for record in records:
            if record.proposed_series and record.proposed_author:
                series_norm = record.proposed_series.strip().lower().replace('ё', 'е')
                author_norm = record.proposed_author.strip().lower().replace('ё', 'е')
                if series_norm == author_norm:
                    record.proposed_series = ""
                    record.series_source = ""
                    cleared_count += 1
        if cleared_count:
            self.logger.log(f"[PASS 6] Cleared {cleared_count} series values that matched author name")

    def _expand_author(self, author: str, authors_map: Dict[str, List[str]]) -> str:
        """Expand a single author name using authors_map.
        
        Handles two cases:
        1. Abbreviations: "Петров И." → "Петров Иван"
        2. Incomplete names: "Живой" → "Живой Алексей" (single word)
        
        Selects the FULLEST name (most words) from alternatives for better quality.
        
        Args:
            author: Single author name (not multi-author)
            authors_map: Dictionary {surname.lower(): [full_names]}
            
        Returns:
            Expanded author name or original if no expansion found
        """
        author = author.strip()
        if not author:
            return author
        
        # Try abbreviation expansion first (has priority).
        # Пропускаем если автор содержит скобочный суффикс вида "(Реальное Имя)" —
        # это намеренное дополнение из author_surname_conversions, не аббревиатура.
        if '.' in author and '(' not in author:
            return self.normalizer.expand_abbreviation(author, authors_map)
        
        # Check if this is an incomplete name (single word)
        words = author.split()
        if len(words) == 1:
            # Single word - try to expand using authors_map
            # Normalize apostrophe variants so О'Фаррелл (U+2019) matches О`Фаррелл (backtick)
            surname_lower = _norm_apos(words[0].lower())
            if surname_lower in authors_map:
                # Found matching surnames - pick the FULLEST name (most words)
                full_names = authors_map[surname_lower]
                best_name = max(full_names, key=lambda x: len(x.split()))

                if len(best_name.split()) > 1:  # Only expand if found a fuller version
                    return best_name

            # Prefix fallback: "Савенко" → "Савенкова" (gender inflection, min prefix 5 chars)
            # Only when exactly one key matches to avoid ambiguity
            if len(surname_lower) >= 5:
                prefix_matches = [
                    k for k in authors_map
                    if k.startswith(surname_lower) and k != surname_lower
                ]
                if len(prefix_matches) == 1:
                    full_names = authors_map[prefix_matches[0]]
                    best_name = max(full_names, key=lambda x: len(x.split()))
                    if len(best_name.split()) > 1:
                        return best_name

        # No expansion needed or found
        return author
    
    
    def _build_authors_map(self, records: List) -> Dict[str, List[str]]:
        """Build dictionary of full author names for abbreviation expansion.
        
        Result: {"петров": ["Петров Иван", "Петров Сергей"]}
        Key is surname in lowercase, values are full names.
        
        Args:
            records: List of BookRecord objects
            
        Returns:
            Dictionary {surname.lower(): [full_names]}
        """
        authors_map: Dict[str, List[str]] = {}
        seen = set()      # нормализованные строки — дедупликация результатов
        seen_raw = set()  # сырые строки — пропускаем normalize_format если уже видели
        norm_cache: Dict[str, str] = {}  # raw → normalized, избегаем повторных вызовов

        def _add(normalized: str) -> None:
            """Добавить нормализованного автора в authors_map."""
            if not normalized or normalized in seen:
                return
            parts = normalized.split()
            if not parts:
                return
            # Normalize apostrophe variants → straight apostrophe for consistent key lookup
            key = _norm_apos(parts[0].lower())
            if key:
                authors_map.setdefault(key, []).append(normalized)
                seen.add(normalized)

        def _normalize_cached(raw: str) -> str:
            if raw not in norm_cache:
                norm_cache[raw] = self.normalizer.normalize_format(raw)
            return norm_cache[raw]

        # Collect from proposed_author (already processed)
        for record in records:
            if record.proposed_author and record.proposed_author != "Сборник":
                author = record.proposed_author
                if ', ' in author:
                    for single_author in author.split(', '):
                        single_author = single_author.strip()
                        if single_author and '.' not in single_author:
                            _add(single_author)
                else:
                    if '.' not in author:
                        _add(author)

            # Collect from metadata_authors (original source - best for abbreviation expansion)
            if record.metadata_authors and record.metadata_authors != "Сборник":
                author = record.metadata_authors
                sep = ', ' if ', ' in author else ('; ' if '; ' in author else None)
                if sep:
                    for single_author in author.split(sep):
                        single_author = single_author.strip()
                        if single_author and single_author not in seen_raw:
                            seen_raw.add(single_author)
                            _add(_normalize_cached(single_author))
                else:
                    if author not in seen_raw:
                        seen_raw.add(author)
                        _add(_normalize_cached(author))

        return authors_map
