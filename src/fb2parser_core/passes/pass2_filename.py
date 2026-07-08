"""
PASS 2: Extract authors from file names.

Uses structural analysis to match filename against all known patterns
in config.json author_series_patterns_in_files, then extracts author
based on best pattern match.

⚠️ CRITICAL RULE: Folder hierarchy extraction (folder_dataset source) is AUTHORITATIVE
and takes absolute priority over all other sources including filename extraction.
Files with author_source="folder_dataset" are NEVER modified in this pass.
This reflects the user's explicit folder structure which is the most reliable source.
"""

from typing import List, Optional
from pathlib import Path
from .file_structural_analysis import analyze_file_structure, score_pattern_match

try:
    from name_normalizer import validate_author_name
except ImportError:
    from ..name_normalizer import validate_author_name


class Pass2Filename:
    """PASS 2: Extract authors from filenames.
    
    CRITICAL RULE: Files with author_source="folder_dataset" are NEVER modified.
    Folder hierarchy extraction is the most reliable source and takes absolute priority.
    Only files without folder_dataset source (empty, metadata, filename, etc.) can be processed.
    """
    
    # Words that indicate the extracted text is NOT an author name
    # IMPORTANT: Use COMPLETE meaningful keywords only, avoid words that are common surnames
    # e.g. "романов/романа/романы" is too dangerous (conflicts with surname "Романов")
    NON_AUTHOR_KEYWORDS = {
        'трилогия', 'дилогия', 'пенталогия', 'тетралогия',
        'сборник', 'авторский', 'авторская', 'авторское',
        'цикл', 'цикла', 'циклов',
        'серия', 'серии', 'сборка',
        'компиляция', 'сборка', 'сборки',
        # Removed: 'романы', 'романа', 'романов' - too common in surnames
        'книг', 'книга', 'книги',
        'издание', 'издания', 'переиздание',
    }
    
    def __init__(self, settings, logger, work_dir: Optional[Path] = None, male_names: set = None, female_names: set = None):
        """Initialize PASS 2.
        
        Args:
            settings: SettingsManager instance
            logger: Logger instance
            work_dir: Working directory with FB2 files (optional, for metadata validation)
            male_names: Set of known male names for author validation (optional)
            female_names: Set of known female names for author validation (optional)
        """
        self.settings = settings
        self.logger = logger
        self.work_dir = Path(work_dir) if work_dir else None
        self.service_words = settings.get_service_words() if hasattr(settings, 'get_service_words') else []
        self.collection_keywords = settings.get_list('collection_keywords') or []  # Load from config
        self.male_names = male_names or set()
        self.female_names = female_names or set()
        self.name_particles = (
            settings.get_name_particles()
            if hasattr(settings, 'get_name_particles')
            else frozenset({'де', 'ди', 'дю', 'ду', 'да', 'дер', 'ден', 'дель', 'дела',
                            'делла', 'дэлла', 'дос', 'дас', 'дэ', 'ван', 'фон',
                            'ля', 'ле', 'ла', 'мак', 'о'})
        )
        self.patterns = self._load_patterns()
        # Precomputed lowercase set of collection keywords for fast lookup in _looks_like_author_name
        self._collection_kw_lower = {k.lower() for k in self.collection_keywords}
        # Author cache: maps abbreviated/partial names to full names
        # e.g., {"А. Живой" -> "Живой Алексей", "Живой" -> "Живой Алексей"}
        self.author_cache = {}
    
    def _load_patterns(self) -> List[dict]:
        """Load author_series_patterns_in_files from config."""
        try:
            return self.settings.get_author_series_patterns_in_files()
        except:
            return []
    
    def _extract_surname(self, author_name: str) -> str:
        """Extract surname from author name.
        
        Handles formats:
        - "Surname Name" -> "Surname"
        - "Name Surname" -> "Surname"  (if starts with lowercase, reverse order)
        - "Surname" -> "Surname"
        
        For Cyrillic names, typically surname comes first.
        
        Args:
            author_name: Full author name
            
        Returns:
            Surname or first word if unclear
        """
        if not author_name or not author_name.strip():
            return ""
        
        parts = author_name.strip().split()
        if not parts:
            return ""
        
        # For Cyrillic names, surname is typically first
        # Return first part as surname (most reliable)
        return parts[0]
    
    def _sort_coauthors_by_surname(self, authors_str: str) -> str:
        """Sort comma-separated authors by surname (alphabetically).
        
        Args:
            authors_str: Authors separated by ', '
            
        Returns:
            Authors sorted by surname, still separated by ', '
        """
        if not authors_str or ', ' not in authors_str:
            return authors_str
        
        authors = [a.strip() for a in authors_str.split(', ')]
        if len(authors) <= 1:
            return authors_str
        
        # Sort by surname (first word)
        try:
            sorted_authors = sorted(authors, key=lambda x: self._extract_surname(x).lower())
            return ', '.join(sorted_authors)
        except Exception as e:
            self.logger.log(f"[PASS 2] WARNING: Failed to sort co-authors '{authors_str}': {e}")
            return authors_str
    
    def _add_to_author_cache(self, extracted: str, expanded: str) -> None:
        """Add author mapping to cache.
        
        Args:
            extracted: Original extracted name (may be abbreviated)
            expanded: Full expanded name
        """
        if not extracted or not expanded:
            return
        
        extracted_lower = extracted.lower().strip()
        expanded_lower = expanded.lower().strip()
        
        # Prefer longer (more complete) forms — never downgrade to a shorter one
        if extracted_lower != expanded_lower:
            existing = self.author_cache.get(extracted_lower)
            if not existing or len(expanded.split()) >= len(existing.split()):
                self.author_cache[extracted_lower] = expanded
    
    def _build_author_cache_from_extraction(self, author_str: str) -> None:
        """Build cache from successfully extracted author(s).
        
        For each author name (even in co-author lists), cache the full name
        and also cache the surname alone for future abbreviation expansion.
        
        Examples:
        - "Живой Алексей" -> cache "живой алексей" and "живой"
        - "Живой Алексей, Прозоров Александр" -> cache both authors and surnames
        
        Args:
            author_str: Successfully extracted author string (may contain multiple authors)
        """
        if not author_str:
            return
        
        # Split by comma-space to handle co-authors
        authors = [a.strip() for a in author_str.split(', ')]
        
        for author in authors:
            if not author:
                continue
            
            author_lower = author.lower().strip()

            # Cache the full name
            self.author_cache[author_lower] = author
            
            # Also cache each word (surname, name) separately.
            # Prefer longer (more complete) forms — overwrite shorter existing entries.
            parts = author.split()
            for part in parts:
                if len(part) > 2:  # Skip very short parts (initials like "А.")
                    part_lower = part.lower()
                    existing = self.author_cache.get(part_lower)
                    if not existing or len(author.split()) > len(existing.split()):
                        self.author_cache[part_lower] = author
    
    def prebuild_author_cache(self, records: List) -> None:
        """Pre-scan all records to build author cache BEFORE main processing.

        This ensures that even if a file has bad/missing metadata, its authors
        can be resolved from sibling files in the same folder that DO have good metadata.

        Strategy: for each record, use record.metadata_authors and cache all author names
        (full name, surname, each word) so they're available during execute().

        Args:
            records: List of BookRecord objects
        """
        print("[PASS 2] Pre-building author cache from FB2 metadata...")
        cached_count = 0

        # Two-pass caching: regular metadata first, then folder_dataset authors last.
        # folder_dataset comes from the folder name (highest-priority source) and must
        # NOT be overwritten by anthology/multi-author metadata that may contain
        # longer but unrelated "Волков Вадим Викторович" style names.
        regular_records = [r for r in records if getattr(r, 'author_source', '') != 'folder_dataset']
        folder_records  = [r for r in records if getattr(r, 'author_source', '') == 'folder_dataset']

        def _cache_author_str(fb2_authors_str: str, is_folder_source: bool) -> None:
            nonlocal cached_count
            if not fb2_authors_str:
                return
            try:
                fb2_authors = [a.strip() for a in fb2_authors_str.split(';') if a.strip()]
                for author in fb2_authors:
                    if not author or ',' in author:
                        continue
                    # Normalize to "Surname First" format before caching.
                    # This prevents "First Middle Last" metadata (e.g. "Кристофер Джон Сэнсом")
                    # from being stored under the first-name key ("кристофер"), which would
                    # incorrectly expand unrelated single-word authors (e.g. "Кристофер" = Пол Кристофер).
                    from name_normalizer import AuthorName as _AN2
                    _an = _AN2(author)
                    normalized_author = _an.normalized if (_an.is_valid and _an.normalized) else author
                    # Only use normalized form if it successfully reordered to "Surname First"
                    # (i.e. normalization produced a different result). Keep original for cache
                    # key coverage but use normalized as the stored value.
                    canonical = normalized_author if normalized_author != author else author
                    # Reduce to 2-word Surname First form for word-keyed cache entries.
                    # 3-word forms (with patronymic) cause false upgrades: "Тарасевич Ольга"
                    # would be incorrectly upgraded to "Тарасевич Ольга Ивановна".
                    if len(canonical.split()) >= 3 and _an.is_valid and _an.parts[2]:
                        canonical = ' '.join(canonical.split()[:2])

                    author_lower = author.lower().strip()
                    self.author_cache[author_lower] = canonical
                    author_words = canonical.split()
                    for idx, part in enumerate(author_words):
                        if len(part) > 2:
                            part_lower = part.lower()
                            if idx == 0:
                                candidate = canonical
                            else:
                                rest = [w for i, w in enumerate(author_words) if i != idx]
                                candidate = part + ' ' + ' '.join(rest)
                            existing = self.author_cache.get(part_lower)
                            if is_folder_source:
                                # folder_dataset always wins — overwrite unconditionally
                                self.author_cache[part_lower] = candidate
                            elif not existing or len(candidate.split()) > len(existing.split()):
                                self.author_cache[part_lower] = candidate
                    cached_count += 1
            except Exception:
                pass

        # Pass 1: regular metadata (longer name wins among equals).
        # Skip anthology/multi-author files: if a file has > 2 authors in metadata
        # it is likely an anthology and caching its authors would pollute surname lookups.
        _ANTHOLOGY_THRESHOLD = 2
        for record in regular_records:
            fb2_str = getattr(record, 'metadata_authors', '') or ''
            if not fb2_str:
                continue
            author_count = len([a for a in fb2_str.split(';') if a.strip()])
            if author_count > _ANTHOLOGY_THRESHOLD:
                continue  # skip anthologies
            _cache_author_str(fb2_str, is_folder_source=False)

        # Pass 2: folder_dataset proposed_author — always overwrites (highest priority)
        for record in folder_records:
            author = getattr(record, 'proposed_author', '') or ''
            if author:
                _cache_author_str(author, is_folder_source=True)

        print(f"[PASS 2] Pre-cache built: {len(self.author_cache)} entries from {cached_count} authors")

    def _validate_and_expand_author(self, extracted_author: str, metadata_authors_str: Optional[str]) -> str:
        """Validate and potentially expand author name using FB2 metadata and cache.

        Strategy:
        1. Check author cache first (compiled from previous files)
        2. If not in cache, try to expand from FB2 metadata
        3. Compare with FB2 metadata authors to find matching record
        4. If found with better form (fuller name), use and cache that instead

        SPECIAL CASE: If extracted is single word (surname) and metadata has multiple
        authors with this surname, DON'T expand here - leave for PASS 3 restoration.

        Args:
            extracted_author: Author name extracted from filename
            metadata_authors_str: Authors string from record.metadata_authors (already in memory)

        Returns:
            Validated/expanded author name or original if not found
        """
        if not extracted_author:
            return extracted_author

        extracted_lower = extracted_author.lower().strip()


        # STEP 1: Check author cache first (knowledge from other files).
        # BUT: for single-word extractions (surname only), the cache entry might have come
        # from a DIFFERENT author who shares the same surname (e.g. "Стайн Роберт" vs
        # "Стайн Гарт"). Always prefer the FB2 file's own metadata over such cross-file
        # cache hits when the extracted form is a single word.
        cache_hit = self.author_cache.get(extracted_lower)
        is_single_word = len(extracted_author.split()) == 1
        # Don't use cache at all when extracted contains " и " (joined-names expression).
        # The JOINED-NAMES guard in execute() needs the original multi-author form;
        # any cache hit here would return a mangled single-author canonical, bypassing it.
        import re as _re_ji
        _is_joined_names = bool(_re_ji.search(r'\s+[иИ]\s+', extracted_author))
        if cache_hit and not is_single_word and not _is_joined_names:
            return cache_hit
        # For single-word: fall through to FB2 check; use cache only as final fallback

        # STEP 2: Try metadata if available
        fb2_authors_str = metadata_authors_str if (metadata_authors_str and
                                                    metadata_authors_str != '[unknown]') else ''
        if fb2_authors_str:
            try:
                # Parse FB2 authors (separated by '; ')
                fb2_authors = [a.strip() for a in fb2_authors_str.split(';') if a.strip()]

                # SPECIAL CASE: If extracted is single word and metadata has multiple co-authors
                # with this word, DON'T expand - leave surname-only for PASS 3 restoration
                if len(extracted_author.split()) == 1 and len(fb2_authors) > 1:
                    # Check if this single word matches multiple FB2 authors
                    # Use both exact word match and root-based matching (handles Russian endings:
                    # "Стругацкие" root "стругацки" matches "Стругацкий" root "стругацки")
                    def _surname_root_p2(s):
                        for ending in ('ские', 'ский', 'ского', 'скому', 'ским', 'ске',
                                       'ская', 'скую', 'ской',
                                       'ое', 'ого', 'ому', 'ым', 'ом',
                                       'ий', 'ие', 'ого', 'ому', 'ым', 'ом'):
                            if s.endswith(ending):
                                return s[:-len(ending)]
                        return s
                    extracted_root = _surname_root_p2(extracted_lower)
                    matching_count = 0
                    for fb2_author in fb2_authors:
                        fb2_words = fb2_author.lower().split()
                        if extracted_lower in fb2_words:
                            matching_count += 1
                        elif any(_surname_root_p2(w) == extracted_root for w in fb2_words):
                            matching_count += 1

                    # If matches multiple authors, don't expand
                    if matching_count > 1:
                        return extracted_author  # Return surname-only, let PASS 3 handle restoration

                # Exact match - return as is
                for fb2_author in fb2_authors:
                    if fb2_author.lower() == extracted_lower:
                        self._add_to_author_cache(extracted_author, fb2_author)
                        return fb2_author  # Return FB2 version (better normalization)

                # Partial match - check if extracted is substring of any FB2 author
                # This handles cases like "Демченко" matching "Демченко Антон" (single-word expansion)
                # BUT: Do NOT allow reversed word order like "Гулевич Александр" → "Александр Гулевич"
                for fb2_author in fb2_authors:
                    fb2_lower = fb2_author.lower()
                    extracted_words_list = extracted_lower.split()
                    fb2_words_list = fb2_lower.split()

                    # Only expand if:
                    # 1. Extracted is SINGLE WORD (legitimate expansion like "Демченко" → "Демченко Антон")
                    # 2. OR first words match AND same number of words (order preserved in both)
                    if len(extracted_words_list) == 1:
                        # Single word expansion - check if it's in FB2 author
                        if extracted_lower in fb2_words_list:
                            # Put the matched surname FIRST (ФИ convention).
                            # FB2 metadata often stores names in ИФ order ("Хуан Франсиско Феррандис"),
                            # but canonical format is ФИ ("Феррандис Хуан Франсиско").
                            # EXCEPTION: if the metadata contains a noble particle (де, van, фон…),
                            # do NOT reorder — the particle belongs next to its word.
                            # "Луи де Берньер" must stay "Луи де Берньер", not "Берньер Луи де".
                            _PARTICLES_P2 = frozenset({
                                'де', 'ди', 'дю', 'ду', 'да', 'дер', 'ден', 'дель', 'дела', 'делла',
                                'дос', 'дас', 'ван', 'фон', 'ля', 'ле', 'ла',
                                'de', 'di', 'du', 'da', 'der', 'den', 'van', 'von',
                                'la', 'le', 'les', 'del', 'della', 'dos', 'das',
                            })
                            _has_particle = any(w in _PARTICLES_P2 for w in fb2_words_list)
                            match_idx = fb2_words_list.index(extracted_lower)
                            if match_idx > 0 and not _has_particle:
                                rest = [w for i, w in enumerate(fb2_author.split()) if i != match_idx]
                                # "X и Y Surname" pattern — co-author expression, don't reorder
                                if 'и' in {w.lower() for w in rest}:
                                    return extracted_author
                                reordered = fb2_author.split()[match_idx] + ' ' + ' '.join(rest)
                                self._add_to_author_cache(extracted_author, reordered)
                                self._last_meta_expanded = True
                                return reordered
                            self._add_to_author_cache(extracted_author, fb2_author)
                            self._last_meta_expanded = True
                            return fb2_author  # Use fuller name from FB2
                    elif (len(extracted_words_list) == len(fb2_words_list) and
                          extracted_words_list[0] == fb2_words_list[0]):
                        # Multi-word with matching first word (likely normalized case variation)
                        self._add_to_author_cache(extracted_author, fb2_author)
                        return fb2_author
                    # Otherwise: different number of words OR reversed order → skip (don't match)

                # PARTICLE MATCHING: if extracted contains a name particle (де, ван, фон…),
                # check if the "particle tail" (from first particle onwards) appears verbatim in
                # any metadata author. This catches compound surnames like:
                #   "Жиро де л Эн" (filename) vs "Аликс де л'Эн" (metadata)
                # Both share the tail "делэн" after apostrophe+space normalization.
                if self.name_particles:
                    _apo = str.maketrans({"'": "", "\u2019": "", "\u02bc": ""})
                    for fb2_author in fb2_authors:
                        fb2_norm = fb2_author.lower().translate(_apo).replace(' ', '')
                        for i, w in enumerate(extracted_lower.split()):
                            if w in self.name_particles:
                                tail = ' '.join(extracted_lower.split()[i:]).translate(_apo).replace(' ', '')
                                if tail and tail in fb2_norm:
                                    self.logger.log(
                                        f"[PASS 2] Particle-tail match: '{extracted_author}' → "
                                        f"'{fb2_author}' (tail='{tail}')"
                                    )
                                    self._add_to_author_cache(extracted_author, fb2_author)
                                    return fb2_author
                                break  # only check from the FIRST particle

            except Exception as e:
                self.logger.log(f"[PASS 2] WARNING: Failed to validate author against metadata: {e}")
        
        # FB2 lookup found nothing — use cross-file cache hit if available (single-word fallback)
        # Don't use co-author expressions ("X и Y Surname") as cache expansions for single-word
        # extractions — Pass 3 multi-author restoration handles these correctly.
        # Also skip cache when extracted itself is a joined-names expression (handled by JOINED-NAMES guard).
        if cache_hit and not _is_joined_names:
            _cache_words = {w.lower() for w in cache_hit.split()}
            if is_single_word and 'и' in _cache_words:
                pass  # skip — co-author expression; let Pass 3 restore
            else:
                return cache_hit

        # No match anywhere, return original extraction
        return extracted_author
    
    def _looks_like_author_name(self, text: str) -> bool:
        """Check if text looks like an author name (structural validation).
        
        Args:
            text: Text to check
        
        Returns:
            True if looks like author name, False otherwise
        """
        if not text or len(text) < 2:
            return False
        
        # Check for trailing punctuation
        if text.endswith('.') or text.endswith(','):
            return False
        
        # Check for leading numbers - patterns like "1-3 Name" are not author names
        if text[0].isdigit():
            return False
        
        # Check if it starts with Cyrillic or Latin letter (required for author names)
        first_char = text[0]
        if not first_char.isalpha():
            return False
        
        # Check for non-author keywords - but only as WHOLE WORDS, not substrings
        # This prevents "Романов" from matching "романов" in "романы"
        text_lower = text.lower().strip()
        text_words = set(text_lower.split())
        
        for keyword in self.NON_AUTHOR_KEYWORDS:
            if keyword in text_words:
                return False
        
        # Check that it has at least one letter (not just numbers)
        has_letter = any(c.isalpha() for c in text)
        if not has_letter:
            return False
        
        # SBORNIK DETECTION: Verify extracted text looks like author name, not collection title
        # This prevents collection titles like "Боевая фантастика" from being extracted as authors
        # Strategy:
        # 1. Single word (just surname) → always allow (e.g., "Демченко")
        # 2. Multiple words → require at least one known first name (e.g., "Демченко Антон")
        # This way surnames like "Демченко" pass, but collection titles don't
        text_normalized = text.lower()
        text_words = text_normalized.split()
        
        if len(text_words) > 1:  # Multi-word - likely "FirstName LastName" or "Title Words"
            # EXCEPTION: if ALL words start with uppercase AND ≤3 words AND none is a
            # collection/genre keyword → treat as proper name (proper-name typography).
            # Handles foreign/exotic names absent from Russian dictionaries:
            # "Ровена Бергман", "Линдквист Йон Айвиде", "Тисако Вакатакэ" etc.
            text_words_orig = text.split()
            all_capitalized = all(w[0].isupper() for w in text_words_orig if w)
            if all_capitalized and len(text_words) <= 3:
                if not any(w in self._collection_kw_lower for w in text_words):
                    return True  # Proper-name pattern: all words capitalised, ≤3 words

            # Require at least one known first name OR a known name particle (de, van, von…)
            # to filter out collection titles like "Боевая фантастика".
            # Particles count as valid name-components (they are part of proper names).
            if self.male_names or self.female_names:
                has_known_name = any(
                    word in self.male_names
                    or word in self.female_names
                    or word in self.name_particles
                    for word in text_words
                )
                if not has_known_name:
                    return False  # Not an author name - likely a collection title
        # Single word always passes (it's a surname, which is valid author name)
        
        return True
    
    def _is_incomplete_name(self, author: str) -> bool:
        """Check if author name is incomplete (only surname, initials, etc).
        
        Examples of incomplete:
        - "Живой" (single word/surname only)
        - "Кумин" (single word)
        - "Михеев М." (surname + initial)
        - "М. Живой" (initial + surname)
        
        Examples of complete:
        - "Живой Алексей" (surname + first name)
        - "Демченко Антон" (surname + first name)
        
        Args:
            author: Author name to check
            
        Returns:
            True if incomplete, False if complete
        """
        if not author:
            return True
        
        words = author.split()
        
        # Single word → incomplete (only surname or initial)
        if len(words) == 1:
            return True
        
        # Two words: check if any is just initial (single letter + dot or single letter)
        # "Кумин. И" or "М. Кумин" or "И М" → incomplete
        # "Живой Алексей" → complete
        has_short = any(
            (len(w) == 1 and w.isalpha()) or  # Single letter like "И"
            (len(w) == 2 and w[1] == '.' and w[0].isalpha())  # Initial like "И."
            for w in words
        )
        
        if has_short:
            # Есть короткое слово — но если есть хотя бы ДВА полных слова (len > 2),
            # это скорее всего формат "Фамилия Имя М." (отчество-инициаль) → полное имя.
            full_words = [w for w in words if len(w) > 2]
            if len(full_words) >= 2:
                return False  # "Форд Джон М." — полное имя с инициалью отчества
            return True  # At least one word is short → incomplete name
        
        # All words are full words → complete
        return False
    
    def _expand_initial_surnames(self, author_str: str, metadata_authors: str) -> tuple:
        """Expand 'Initial.Surname' tokens (like 'Г.Диксон') using metadata authors.

        Handles multi-author strings like "Гаррисон Гарри, Г.Диксон" — expands any
        token that matches the pattern Letter.Surname against the metadata author list.

        Args:
            author_str: Author string, possibly multi-author like "Гаррисон Гарри, Г.Диксон"
            metadata_authors: Raw metadata authors string ("Гордон Диксон; Гарри Гаррисон")

        Returns:
            Tuple (expanded_str, was_expanded)
        """
        if not metadata_authors:
            return author_str, False

        # Parse metadata authors (separated by "; " or ";")
        meta_list = [a.strip() for a in metadata_authors.replace('; ', ';').split(';') if a.strip()]

        # Split input by ", " to handle each author token separately
        parts = [p.strip() for p in author_str.split(',')]
        expanded_parts = []
        was_expanded = False

        for part in parts:
            if not part:
                continue

            # Detect "X.Surname" pattern (single uppercase letter + dot + surname)
            # e.g., "Г.Диксон", "H.Harrison"
            dot_idx = part.find('.')
            if (dot_idx == 1 and part[0].isupper() and
                    len(part) > 2 and part[2:3].isupper()):
                initial = part[0].lower()
                surname = part[dot_idx + 1:].lower()
                # Find metadata author with this surname AND a name starting with initial
                found = None
                for meta_author in meta_list:
                    meta_words = meta_author.lower().split()
                    if (any(w == surname for w in meta_words) and
                            any(w.startswith(initial) and w != surname for w in meta_words)):
                        found = meta_author
                        break
                if found:
                    expanded_parts.append(found)
                    was_expanded = True
                    continue

            expanded_parts.append(part)

        result = ', '.join(expanded_parts)
        return result, was_expanded

    def _try_expand_from_metadata(self, incomplete_author: str, metadata_authors: str) -> str:
        """Try to expand incomplete author name from metadata.
        
        If incomplete_author is like "Живой" or "Кумин. И", find the full version
        in metadata_authors and return it.
        
        Args:
            incomplete_author: Short name from filename ("Кумин" or "Михеев М.")
            metadata_authors: Full authors string from FB2 metadata ("Вячислав Кумин; ...")
            
        Returns:
            Full author name if found, otherwise original incomplete_author
        """
        if not metadata_authors:
            return incomplete_author
        
        # Extract surnames from incomplete_author
        incomplete_parts = incomplete_author.split()
        
        # Take first word (usually surname) for matching
        surname_candidate = incomplete_parts[0].lower()
        
        # Split metadata into individual authors (separated by "; " or ", ")
        meta_authors = [a.strip() for a in metadata_authors.replace('; ', '|').replace(', ', '|').split('|')]
        
        matched_authors = []
        for meta_author in meta_authors:
            if not meta_author:
                continue

            meta_words = meta_author.split()

            # Try to find matching surname in metadata
            # E.g., if looking for "Кумин", check if metadata has "...Кумин..."
            for idx, word in enumerate(meta_words):
                if word.lower() == surname_candidate:
                    # Found match!
                    # Always put the matched surname FIRST (ФИ convention).
                    # Metadata stores names in ИФ order ("Хуан Франсиско Феррандис"),
                    # but our canonical format is ФИ ("Феррандис Хуан Франсиско").
                    if idx == 0:
                        matched_authors.append(meta_author)
                    else:
                        rest = [w for i, w in enumerate(meta_words) if i != idx]
                        # "X и Y Surname" co-author expression — don't reorder, return as-is
                        if 'и' in {w.lower() for w in rest}:
                            matched_authors.append(meta_author)
                        else:
                            matched_authors.append(word + ' ' + ' '.join(rest))
                    break  # одна запись metadata → один совпавший автор

        if len(matched_authors) == 1:
            return matched_authors[0]
        elif len(matched_authors) > 1:
            # Несколько авторов с одной фамилией (например "Белаш Александр" и "Белаш Людмила").
            # Возвращаем всех через ", " — Pass 3 нормализует каждого отдельно.
            return ', '.join(matched_authors)

        # No clear match found - return original
        return incomplete_author
    
    def _is_collection(self, file_path: str, file_title: str) -> bool:
        """Return True when the file is a true anthology/collection.

        Criterion: the filename (without extension) OR the book title contains
        at least one of the collection_keywords from config.json.
        Co-authored books with 3+ authors but without those keywords are NOT
        considered collections — they are 'Соавторство'.

        Args:
            file_path: Relative file path (may contain directory separators)
            file_title: Book title extracted from FB2 metadata (may be empty)

        Returns:
            True  → assign "Сборник"
            False → assign "Соавторство"
        """
        if not self.collection_keywords:
            return False

        # Check in filename (basename, no extension) only — NOT in book title,
        # because publishers sometimes add "(сборник)" to the title of an ordinary
        # co-authored book, which should still be classified as "Соавторство".
        filename_noext = file_path.replace('\\', '/').split('/')[-1].rsplit('.', 1)[0].lower()

        for kw in self.collection_keywords:
            kw_l = kw.lower()
            if kw_l in filename_noext:
                return True

        return False

    def _count_authors(self, authors_str: str) -> int:
        """Count number of authors in metadata authors string.
        
        Authors are separated by "; " (fb2 metadata) or ", " (filename/metadata)
        
        Args:
            authors_str: String with authors (e.g. "Author 1; Author 2; Author 3")
            
        Returns:
            Number of authors found
        """
        if not authors_str or authors_str == "[unknown]":
            return 0
        
        # Count authors separated by "; " or ", "
        if "; " in authors_str:
            return len([a for a in authors_str.split("; ") if a.strip()])
        elif ", " in authors_str:
            return len([a for a in authors_str.split(", ") if a.strip()])
        else:
            return 1 if authors_str.strip() else 0
    
    def execute(self, records: List) -> None:
        """Execute PASS 2: Extract authors from filenames.
        
        CRITICAL RULE: folder_dataset source is AUTHORITATIVE and NEVER OVERWRITTEN!
        
        Folder hierarchy extraction indicates the user explicitly created folder structure
        for this author. This is the most reliable source and takes absolute priority.
        
        Processing order for each record:
        1. If author_source == "folder_dataset" → SKIP (never override, keep as-is)
        2. Try to extract author from filename using structural analysis
        3. If extraction succeeds → set as author, source="filename" (OVERRIDES metadata)
        4. If extraction fails → keep what was (metadata or empty)
        
        Also builds author cache during execution for use with abbreviations.
        
        Args:
            records: List of BookRecord objects to process
        """
        print("[PASS 2] Extracting authors from filenames (structural analysis)...")
        
        processed_count = 0
        skipped_count = 0
        error_count = 0
        test_count = 0
        
        for i, record in enumerate(records):
            # Папка — абсолютный приоритет. folder_dataset пропускается всегда,
            # кроме случая needs_filename_fallback (папка не дала автора).
            if (record.author_source == "folder_dataset" and
                    not getattr(record, 'needs_filename_fallback', False)):
                skipped_count += 1
                continue

            # CHECK: Is this file a collection/anthology or co-authored book?
            # Rule: 3+ authors in metadata → either "Сборник" (if keywords in filename/title)
            #                               or "Соавторство" (regular multi-author book)
            # IMPORTANT: не перезаписываем folder_dataset — папка авторитетнее метаданных.
            if record.author_source != "folder_dataset":
                author_count = self._count_authors(record.metadata_authors)
                if author_count >= 3:
                    if self._is_collection(record.file_path, getattr(record, 'file_title', '')):
                        record.proposed_author = "Сборник"
                    else:
                        record.proposed_author = "Соавторство"
                    record.author_source = "collection"
                    record.needs_filename_fallback = False
                    processed_count += 1
                    continue  # Skip regular filename parsing for collections/co-authored
            
            # Try to extract from filename (NOT full path!)
            # Handle both Windows (\) and Unix (/) path separators
            filename = record.file_path.replace('\\', '/').split('/')[-1]  # Get basename only
            filename_without_ext = filename.rsplit('.', 1)[0]  # Remove extension
            
            self._last_meta_expanded = False
            author = self._extract_author_from_filename(
                filename_without_ext,
                file_title=getattr(record, 'file_title', '') or '',
                metadata_authors_str=getattr(record, 'metadata_authors', '') or '',
            )

            if author:
                # Successfully extracted from filename
                # IMPORTANT: NEVER OVERWRITE folder_dataset source!
                # Folder hierarchy extraction is AUTHORITATIVE and should never be changed
                if record.author_source != "folder_dataset":
                    # Check if extracted author is incomplete (single name, initials, etc.)
                    expanded_author = author
                    use_hybrid_source = self._last_meta_expanded

                    # STEP 1: Expand "Initial.Surname" tokens (e.g., "Г.Диксон" → "Гордон Диксон")
                    if record.metadata_authors:
                        new_author, was_init_expanded = self._expand_initial_surnames(author, record.metadata_authors)
                        if was_init_expanded:
                            expanded_author = new_author
                            use_hybrid_source = True

                    # STEP 2: Traditional incomplete name expansion (single word, pure initial, etc.)
                    # Также обрабатываем формат «Фамилия И. Фамилия2» / «Фамилия Р. Отчество» —
                    # _is_incomplete_name считает их полными (2 полных слова), но инициал
                    # посередине означает что имя требует расширения через метаданные.
                    import re as _re_mi
                    _has_mid_initial = bool(
                        record.metadata_authors and
                        _re_mi.search(r'\s[А-ЯЁA-Z]\.\s', expanded_author)
                    )
                    if not use_hybrid_source and (self._is_incomplete_name(expanded_author) or _has_mid_initial):
                        if record.metadata_authors:
                            expanded = self._try_expand_from_metadata(expanded_author, record.metadata_authors)
                            if expanded and expanded != expanded_author:
                                expanded_author = expanded
                                use_hybrid_source = True  # Mark as hybrid source

                    # GLUED-INITIALS GUARD: если извлечённый автор начинается с 2+ заглавных
                    # букв, слитно сросшихся с фамилией (например "АКТроицкий" = "А.К." + "Троицкий"),
                    # это скорее всего инициалы без разделителей. Если meta даёт одного автора — берём его.
                    import re as _re_gi
                    if (expanded_author and
                            _re_gi.match(r'^[А-ЯЁ]{2,}[А-ЯЁ][а-яё]', expanded_author) and
                            record.metadata_authors and
                            self._count_authors(record.metadata_authors) == 1):
                        record.proposed_author = record.metadata_authors
                        record.author_source = "metadata"
                        record.needs_filename_fallback = False
                        processed_count += 1
                        continue

                    # JOINED-NAMES GUARD: если извлечённый автор содержит " и " или " И "
                    # (русский союз между именами двух авторов, например "Альвтеген Альбин и Карин"),
                    # и мета даёт ровно 2 авторов — берём мету и нормализуем каждого отдельно.
                    if (expanded_author and
                            _re_gi.search(r'\s+[иИ]\s+', expanded_author) and
                            record.metadata_authors and
                            self._count_authors(record.metadata_authors) == 2):
                        sep = '; ' if '; ' in record.metadata_authors else ', '
                        meta_parts = [a.strip() for a in record.metadata_authors.split(sep) if a.strip()]
                        if len(meta_parts) == 2:
                            from author_normalizer_extended import AuthorNormalizer as _AN
                            _norm = _AN(self.settings)
                            normalized_pair = [
                                _norm.normalize_format(a) for a in meta_parts
                            ]
                            record.proposed_author = ', '.join(normalized_pair)
                            record.author_source = "metadata"
                            record.needs_filename_fallback = False
                            processed_count += 1
                            self._build_author_cache_from_extraction(record.proposed_author)
                            continue

                    # TITLE-AS-AUTHOR GUARD: если извлечённый автор является частью
                    # заголовка книги, он скорее всего не автор (например "Дух Рождества"
                    # из файла "Дух Рождества. 101 история...").
                    # Если при этом metadata_authors содержит одного автора → используем его.
                    file_title = getattr(record, 'file_title', '') or ''
                    if (file_title and expanded_author and
                            expanded_author.lower() in file_title.lower() and
                            record.metadata_authors and
                            self._count_authors(record.metadata_authors) == 1):
                        record.proposed_author = record.metadata_authors
                        record.author_source = "metadata"
                        record.needs_filename_fallback = False
                        processed_count += 1
                        continue

                    # No folder_dataset - use filename extraction
                    # This OVERRIDES metadata (FILE -> METADATA priority)
                    record.proposed_author = expanded_author
                    record.author_source = "filename+meta_expanded" if use_hybrid_source else "filename"
                    record.needs_filename_fallback = False  # Clear the fallback flag since we found something
                    processed_count += 1

                    # BUILD AUTHOR CACHE: Track this extraction for future abbreviation expansion
                    # This helps expand abbreviated names in subsequent files
                    # e.g., if we extract "Живой Алексей", cache that we've seen this full form
                    self._build_author_cache_from_extraction(expanded_author)
                # else: Already has folder_dataset source - NEVER override it, keep existing
            else:
                # Filename extraction failed (author is empty)
                # Fallback: Use metadata if available (with hybrid source)
                if (record.author_source != "folder_dataset" and 
                    record.metadata_authors and 
                    not record.proposed_author):  # Only if not already set
                    # Use metadata as fallback, mark as hybrid (filename attempt + metadata fallback)
                    if self._count_authors(record.metadata_authors) >= 3:
                        if self._is_collection(record.file_path, getattr(record, 'file_title', '')):
                            record.proposed_author = "Сборник"
                        else:
                            record.proposed_author = "Соавторство"
                        record.author_source = "collection"
                    else:
                        record.proposed_author = record.metadata_authors
                        record.author_source = "metadata"  # Couldn't extract from filename
                    record.needs_filename_fallback = False
                    processed_count += 1
                # else: keep existing (might be metadata or empty)
        
        print(f"[PASS 2] Extracted {processed_count} authors from filenames, skipped {skipped_count} folder_dataset records, errors: {error_count}")

        # SECOND PASS: upgrade short author forms to longer ones now in cache.
        # Needed because processing order is unpredictable: a file with short surname-only
        # extraction ("Робертс Грегори") may have been processed before another file that
        # extracted the full 3-word form ("Робертс Грегори Дэвид") and populated the cache.
        upgraded = 0
        for record in records:
            if record.author_source in ("folder_dataset", "collection", "metadata"):
                continue
            if not record.proposed_author:
                continue
            author_words = record.proposed_author.split()
            author_lower = record.proposed_author.lower().strip()

            # Try exact-key cache lookup first
            cached = self.author_cache.get(author_lower)

            # If no exact match (or exact match is not longer), try surname-only lookup.
            # This upgrades e.g. "Фальконес Ильдефонсо" → "Фальконес Ильдефонсо де Сьерра"
            # when the cache has key "фальконес" → full 3-word form.
            if (not cached or len(cached.split()) <= len(author_words)) and len(author_words) >= 1:
                first_lower = author_words[0].lower()
                if first_lower != author_lower:  # avoid re-checking single-word authors
                    surname_cached = self.author_cache.get(first_lower)
                    if (surname_cached
                            and len(surname_cached.split()) > len(author_words)
                            and surname_cached.split()[0].lower() == first_lower):
                        # Guard: if record has metadata and the cached longer form
                        # contains non-surname words NOT found in metadata, it's a
                        # different person sharing the same surname — don't upgrade.
                        # Example: "Дэвис Анна" (meta="Анна Дэвис") must not be
                        # upgraded to "Дэвис Дж. Мэдисон" from another Дэвис book.
                        _meta = (getattr(record, 'metadata_authors', '') or '').lower()
                        if _meta:
                            _new_non_surname = [
                                w for w in surname_cached.lower().split()[1:]
                                if len(w) > 2 and '.' not in w
                            ]
                            if any(w not in _meta for w in _new_non_surname):
                                surname_cached = None
                        # Don't upgrade when current author already has a first name that
                        # differs from the cached first name — it's a different person.
                        # E.g. "Михайлов Дем" must not be upgraded to "Михайлов Руслан Алексеевич".
                        if surname_cached and len(author_words) >= 2 and len(surname_cached.split()) >= 2:
                            _cur_fn = author_words[1].lower().replace('ё', 'е').rstrip('.')
                            _cached_fn = surname_cached.split()[1].lower().replace('ё', 'е').rstrip('.')
                            if len(_cur_fn) > 2 and _cur_fn != _cached_fn and not _cached_fn.startswith(_cur_fn):
                                surname_cached = None
                        if surname_cached:
                            cached = surname_cached

            if cached and len(cached.split()) > len(author_words):
                # Don't upgrade single-word surname to co-author expression ("X и Y Surname").
                # Such expressions contain standalone "и" and should be handled by Pass 3
                # multi-author restoration instead of being propagated via cache.
                if len(author_words) == 1 and 'и' in {w.lower() for w in cached.split()}:
                    continue
                record.proposed_author = cached
                upgraded += 1
        if upgraded:
            self.logger.log(f"[PASS 2] Upgraded {upgraded} short author names to longer cached forms")
    
    def _extract_by_pattern(self, filename: str, pattern: str, struct: dict) -> str:
        """Extract author from filename based on matched pattern.
        
        Args:
            filename: Full filename without extension
            pattern: Matched pattern string
            struct: Analyzed structure
        
        Returns:
            Author name or empty string
        """
        
        author = ""
        
        # Pattern: "(Author) - Title"
        if pattern == "(Author) - Title":
            if ' - ' in filename:
                before_dash = filename.split(' - ', 1)[0]
                author = before_dash.strip().strip('()')
                # If author contains a dot, take only the part before it
                if '. ' in author:
                    author = author.split('. ', 1)[0].strip()
        
        # Pattern: "Author - Title"
        elif pattern == "Author - Title":
            if ' - ' in filename:
                author = filename.split(' - ', 1)[0].strip()
                # If author contains a dot, take only the part before it (e.g., "Жеребьёв. Я" -> "Жеребьёв")
                if '. ' in author:
                    author = author.split('. ', 1)[0].strip()
        
        # Pattern: "Author - Series.Title"
        elif pattern == "Author - Series.Title":
            if ' - ' in filename:
                author = filename.split(' - ', 1)[0].strip()
                # If author contains a dot, take only the part before it
                if '. ' in author:
                    author = author.split('. ', 1)[0].strip()
        
        # Pattern: "Author. Title"
        elif pattern == "Author. Title":
            if '. ' in filename:
                author = filename.split('. ', 1)[0].strip()
        
        # Pattern: "Title (Author)"
        elif pattern == "Title (Author)":
            if '(' in filename and ')' in filename:
                start = filename.rfind('(')
                end = filename.rfind(')')
                if start < end:
                    author = filename[start+1:end].strip()
        
        # Pattern: "Title - (Author)"
        elif pattern == "Title - (Author)":
            if ' - (' in filename:
                parts = filename.split(' - (')
                if len(parts) == 2:
                    author = parts[1].rstrip(')').strip()
                    # If author contains a dot, take only the part before it
                    if '. ' in author:
                        author = author.split('. ', 1)[0].strip()
        
        # Pattern: "Author. Series. Title"
        elif pattern == "Author. Series. Title":
            if '. ' in filename:
                author = filename.split('. ', 1)[0].strip()
        
        # Pattern: "Author, Author. Title (Series)"
        elif pattern == "Author, Author. Title (Series)":
            if ', ' in filename:
                # Extract both authors separated by comma
                before_period = filename.split('. ', 1)[0].strip()
                # If the last word is a single uppercase letter (initial like "А"),
                # the dot was consumed as the author/title separator — reattach it.
                _bp_words = before_period.split()
                if _bp_words and len(_bp_words[-1]) == 1 and _bp_words[-1][0].isupper():
                    before_period = before_period + '.'
                authors = [a.strip() for a in before_period.split(', ')]
                author = ', '.join(authors)  # Return both: "Author1, Author2"
        
        # Pattern: "Author. Title. (Series)"
        elif pattern == "Author. Title. (Series)":
            if '. ' in filename:
                author = filename.split('. ', 1)[0].strip()
        
        # Pattern: "Author - Title (Series)" (NO service words)
        elif pattern == "Author - Title (Series)":
            if ' - ' in filename:
                author = filename.split(' - ', 1)[0].strip()
                # If author contains a dot, take only the part before it
                if '. ' in author:
                    author = author.split('. ', 1)[0].strip()
        
        # Pattern: "Author - Title. Title (Series)" (with dot in title)
        elif pattern == "Author - Title. Title (Series)":
            if ' - ' in filename:
                author = filename.split(' - ', 1)[0].strip()
                # If author contains a dot, take only the part before it
                if '. ' in author:
                    author = author.split('. ', 1)[0].strip()
        
        # Pattern: "Author. Title (Series)" (NO service words)
        elif pattern == "Author. Title (Series)":
            if '. ' in filename:
                author = filename.split('. ', 1)[0].strip()
        
        # Pattern: "Author, Author - Title (Series)" (NO service words)
        elif pattern == "Author, Author - Title (Series)":
            if ', ' in filename:
                # Extract both authors separated by comma
                before_dash = filename.split(' - ', 1)[0].strip()
                # If any author contains a dot, take only the part before it
                authors = []
                for a in before_dash.split(', '):
                    a = a.strip()
                    if '. ' in a:
                        a = a.split('. ', 1)[0].strip()
                    authors.append(a)
                author = ', '.join(authors)  # Return both: "Author1, Author2"
        
        # Pattern: "Author - Title (Series. service_words)"
        elif pattern == "Author - Title (Series. service_words)":
            if ' - ' in filename:
                author = filename.split(' - ', 1)[0].strip()
                # If author contains a dot, take only the part before it
                if '. ' in author:
                    author = author.split('. ', 1)[0].strip()
        
        # Pattern: "Author. Title (Series. service_words)"
        elif pattern == "Author. Title (Series. service_words)":
            if '. ' in filename:
                author = filename.split('. ', 1)[0].strip()
        
        # Pattern: "Author, Author - Title (Series. service_words)"
        elif pattern == "Author, Author - Title (Series. service_words)":
            if ', ' in filename:
                # Extract both authors separated by comma
                before_dash = filename.split(' - ', 1)[0].strip()
                # If any author contains a dot, take only the part before it
                authors = []
                for a in before_dash.split(', '):
                    a = a.strip()
                    if '. ' in a:
                        a = a.split('. ', 1)[0].strip()
                    authors.append(a)
                author = ', '.join(authors)  # Return both: "Author1, Author2"
        
        # Return only if non-empty and valid author name
        if author and len(author) > 2:
            return author
        return ""
    
    def _clean_filename_for_extraction(self, filename: str) -> str:
        """Remove blacklist markers from filename before pattern matching.
        
        CRITICAL: Blacklist markers like "(СИ)" add extra blocks to the filename structure,
        which breaks block-count-based pattern matching. They must be removed BEFORE tokenization.
        
        This affects how blocks are counted:
        - "Автор - Название (СИ)" has 2 blocks IF we remove "(СИ)" first
        - "Автор - Название (СИ)" has 3 blocks if we keep "(СИ)" as a separate block
        
        Args:
            filename: Original filename
            
        Returns:
            Filename with blacklist markers removed
        """
        import re
        
        cleaned = filename
        
        # Remove blacklist elements from the END of filename
        # Start from the end and remove matching blacklist patterns
        # Only remove if they appear at END of string (after all meaningful content)
        
        # Pattern 1: "(СИ)" or variations at the end
        cleaned = re.sub(r'\s*\(СИ\)\s*$', '', cleaned, flags=re.IGNORECASE)

        # Pattern 2: Collection/anthology markers at the end — "(сборник)", "(антология)" etc.
        # These are informational tags that do NOT represent a separate meaningful block.
        cleaned = re.sub(r'\s*\(сборник[^)]*\)\s*$', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*\(антология[^)]*\)\s*$', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*\(omnibus[^)]*\)\s*$', '', cleaned, flags=re.IGNORECASE)

        # Pattern 3: Other known meta-patterns that shouldn't create extra blocks
        # Remove tags/meta in parens at the end
        cleaned = re.sub(r'\s*\([^)]*(?:издание|изд\.)[^)]*\)\s*$', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*\(пер\.\s*[^)]*\)\s*$', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*\(перевод[^)]*\)\s*$', '', cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r'\s*\(пер\)\s*$', '', cleaned, flags=re.IGNORECASE)

        # Strip pseudonym/note in parens that appears BEFORE the first ". " separator.
        # "Фамилия Имя (Псевдоним). Название" → "Фамилия Имя. Название"
        # Without this, the parens create an extra block that breaks 2-block patterns.
        _m = re.match(r'^(.+?)\s*\([^)]+\)(\s*\..+)', cleaned)
        if _m:
            cleaned = _m.group(1).strip() + _m.group(2)

        # Normalize guillemet quotes «...» → "..." so block scorer handles them correctly.
        # «Z» scores 0.0 in block matcher; "Z" scores correctly.
        cleaned = cleaned.replace('«', '"').replace('»', '"')

        return cleaned.strip()
    
    def _extract_author_from_filename(self, filename: str, file_title: Optional[str] = None,
                                       metadata_authors_str: Optional[str] = None) -> str:
        """Extract author name from filename using BLOCK-LEVEL pattern matching.

        Algorithm:
        1. CLEAN filename from blacklist markers (like "(СИ)") to preserve block count
        2. Tokenize cleaned filename into blocks (delimited by ' - ', '. ', parens)
        3. Score each pattern against blocks via BlockLevelPatternMatcher
        4. Take best-score match (threshold 0.6)
        5. TITLE-AS-AUTHOR GUARD: compare extracted candidate against book title from
           record.file_title. When two patterns tie (e.g. "Author - Title" vs
           "Title - Author" both score 0.73 for a two-block filename), the first pattern
           in config.json wins by default. If that first-winner extracts the book title
           as "author", retry without patterns whose name starts with "Title" → correct
           pattern wins the retry.
        6. VALIDATE extracted name (_looks_like_author_name + validate_author_name)
        7. EXPAND abbreviated/incomplete names via metadata and author cache

        Args:
            filename: Filename without extension
            file_title: Book title from record.file_title (already in memory, optional)
            metadata_authors_str: Authors string from record.metadata_authors (already in memory, optional)

        Returns:
            Author name or empty string
        """
        if not filename:
            return ""
        
        try:
            # Import block-level matcher
            try:
                from block_level_pattern_matcher import BlockLevelPatternMatcher
            except ImportError:
                from ..block_level_pattern_matcher import BlockLevelPatternMatcher
            import re
            
            # CRITICAL: Remove blacklist markers from filename BEFORE pattern matching
            # "(СИ)" at the end creates an extra block that breaks pattern matching!
            cleaned_filename = self._clean_filename_for_extraction(filename)
            
            # Create matcher with service words and known author names
            matcher = BlockLevelPatternMatcher(
                service_words=list(self.service_words),
                male_names=self.male_names,
                female_names=self.female_names
            )
            
            # Find best pattern match using block-level comparison on CLEANED filename
            best_score, best_pattern, author, series = matcher.find_best_pattern_match(cleaned_filename, self.patterns)
            
            # Need minimum score threshold to proceed
            if best_score < 0.6:  # Threshold for block matching
                #self.logger.log(f"[PASS 2] Block score too low: {best_score:.2f} < 0.6 for '{filename}'")
                return ""
            
            # Validate extracted author
            if not author or not author.strip():
                #self.logger.log(f"[PASS 2] No author block extracted for '{filename}'")
                return ""
            
            author = author.strip()

            # INITIAL-DOT RESTORE: block tokenizer uses '. ' as delimiter, so
            # a trailing initial like "А" in "Райро А. Угроза..." loses its dot.
            # If the last token of the extracted author is a single uppercase letter,
            # re-attach the dot (it was the block separator, not end-of-author).
            _auth_words = author.split()
            if _auth_words and len(_auth_words[-1]) == 1 and _auth_words[-1][0].isupper():
                author = author + '.'

            # TITLE-AS-AUTHOR GUARD: <book-title> can NEVER be an author.
            # This catches tie-breaking mistakes: e.g. "Алдерман Наоми - Сила" scores equally
            # for "Author - Title" and "Title - Author"; if the winning candidate equals the
            # real book title, the pattern order chose wrong — reject it and try again
            # without that candidate pattern.
            book_title = file_title or ''
            if book_title:
                try:
                    # Strip trailing [...] noise (e.g. "[litres]", "[СИ]") before comparing.
                    _book_title_clean = re.sub(r'\s*\[.*?\]\s*$', '', book_title.strip()) if book_title else ''
                    # Also strip trailing (...) noise (e.g. "(ЛП)", "(СИ)", "(альт. перевод)")
                    # so "Спасение (ЛП)" → "Спасение" can be matched against extracted author.
                    _book_title_no_parens = re.sub(r'\s*\([^)]*\)\s*$', '', _book_title_clean).strip()
                    # Normalise ё→е before comparing so "Звёздочка" matches "Звездочка"
                    def _yo(s: str) -> str:
                        return s.lower().replace('ё', 'е')
                    _title_matches_author = (
                        (_book_title_clean and _yo(_book_title_clean) == _yo(author)) or
                        (_book_title_no_parens and _yo(_book_title_no_parens) == _yo(author))
                    )
                    if _title_matches_author:
                        self.logger.log(
                            f"[PASS 2] Rejected author '{author}' — matches book-title "
                            f"(pattern='{best_pattern}'). Retrying without Title-first patterns."
                        )
                        # Retry: exclude patterns whose name starts with "Title"
                        filtered_patterns = [
                            p for p in self.patterns
                            if not (p.get('pattern', '') if isinstance(p, dict) else p).startswith('Title')
                        ]
                        best_score, best_pattern, author, series = matcher.find_best_pattern_match(
                            cleaned_filename, filtered_patterns
                        )
                        if best_score < 0.6 or not author or not author.strip():
                            return ""
                        author = author.strip()
                        # Sanity check: still the book title?
                        if author.lower() in (_book_title_clean.lower(), _book_title_no_parens.lower()):
                            return ""
                except Exception:
                    pass
            
            # Handle comma-separated authors (co-authorship)
            if ', ' in author:
                authors = [a.strip() for a in author.split(', ')]
                validated_authors = []
                
                for single_author in authors:
                    looks_like = self._looks_like_author_name(single_author)
                    is_valid = validate_author_name(single_author) if single_author else False
                    if single_author and looks_like and is_valid:
                        expanded = self._validate_and_expand_author(single_author, metadata_authors_str)
                        validated_authors.append(expanded)
                    elif single_author:
                        validated_authors.append(single_author)
                
                if validated_authors:
                    author = ', '.join(validated_authors)
                    self.logger.log(f"[PASS 2] ✓ Extracted '{author}' from '{filename}' (block-level)")
                    return author
                # else: validation failed, fall through
            
            # Single author case
            if author and self._looks_like_author_name(author) and validate_author_name(author):
                author = self._validate_and_expand_author(author, metadata_authors_str)
                self.logger.log(f"[PASS 2] ✓ Extracted '{author}' from '{filename}' (block-level)")
                return author
            else:
                #self.logger.log(f"[PASS 2] Block extraction failed validation for '{author}' from '{filename}'")
                return ""
        
        except ImportError as e:
            self.logger.log(f"[PASS 2] ImportError: BlockLevelPatternMatcher - {e}")
            return ""
        except Exception as e:
            import traceback
            self.logger.log(f"[PASS 2] Block-level matching error for '{filename}': {e}")
            traceback.print_exc()
            return ""
        
        self.logger.log(f"[PASS 2 DEBUG] No pattern match {'(score=' + str(best_score) + ')' if best_score > 0 else ''}")
        
        return ""
