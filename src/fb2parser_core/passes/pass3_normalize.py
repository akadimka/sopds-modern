"""
PASS 3: Normalize author names to standard format.
"""

import unicodedata
from typing import List, Optional
from ..author_normalizer_extended import AuthorNormalizer
from ..settings_manager import SettingsManager


def _nfc_yo_to_ye(s: str) -> str:
    """NFC + ё→е: единообразие написания серий и авторов."""
    return unicodedata.normalize('NFC', s).replace('\u0451', '\u0435')




def _strip_diacritics(s: str) -> str:
    """Remove stress accent marks only (U+0301 acute), preserving й, ё, etc."""
    return unicodedata.normalize('NFC',
        ''.join(c for c in unicodedata.normalize('NFD', s)
                if c != '́'))
class Pass3Normalize:
    """PASS 3: Normalize author names to standard format.
    
    Transform author names from various formats to standard "Фамилия Имя" format:
    - "Иван Петров" → "Петров Иван"
    - "А.Михайловский; А.Харников" → "Михайловский А., Харников А." (multi-author)
    """
    
    def __init__(self, logger, settings=None):
        """Initialize PASS 3.
        
        Args:
            logger: Logger instance
            settings: Optional shared SettingsManager
        """
        self.logger = logger
        try:
            self.settings = settings or SettingsManager('config.json')
        except:
            self.settings = None
        self.normalizer = AuthorNormalizer(self.settings)
    
    def execute(self, records: List) -> None:
        """Execute PASS 3: Normalize author names.
        
        Transform author format and handle multi-author cases.
        Handles both separators: '; ' (from folder parsing) and ', ' (from filename/metadata).
        
        Args:
            records: List of BookRecord objects to process
        """
        print("[PASS 3] Normalizing author names...")
        
        normalized_count = 0

        # Build set of pinned author names from author_surname_conversions values.
        # These are used verbatim and must not be reordered by normalize_format.
        _conversions = (self.settings.get_author_surname_conversions() if self.settings else None) or {}
        _pinned_authors = {v.lower() for v in _conversions.values()}

        for record in records:
            if not record.proposed_author or record.proposed_author == "Сборник":
                continue

            # Skip normalization for authors pinned via author_surname_conversions.
            # These are typically Latin pseudonyms or fixed-form names that must not
            # be reordered (e.g. "Myrmice Orlyett", "Solveig Ericson").
            if record.proposed_author.lower() in _pinned_authors:
                continue

            # Apply conversions by KEY — catches metadata-sourced authors that were
            # never processed by folder-cache lookup (e.g. "Т и Д Зимины" → "Зимины Т. и Д.").
            if record.proposed_author in _conversions:
                record.proposed_author = _conversions[record.proposed_author]
                normalized_count += 1
                continue

            # Дедупликация авторов: "Гаусс Максим, Гаусс Максим" → "Гаусс Максим"
            sep = '; ' if '; ' in record.proposed_author else ', '
            _parts = [a.strip() for a in record.proposed_author.replace(';', ',').split(',')]
            _seen: list = []
            for _p in _parts:
                if _p and _p.lower() not in [x.lower() for x in _seen]:
                    _seen.append(_p)
            if len(_seen) < len(_parts):
                record.proposed_author = sep.join(_seen)

            # Если среди авторов есть "Коллектив авторов" → это антология, ставим Сборник
            _authors_to_check = record.proposed_author.replace(';', ',')
            if any(a.strip().lower().replace('ё', 'е') == 'коллектив авторов'
                   for a in _authors_to_check.split(',')):
                record.proposed_author = "Сборник"
                continue

            original = record.proposed_author

            # Check for multi-author cases with different separators
            # '; ' comes from folder_author_parser (temporary separator)
            # ', ' comes from filename or metadata
            # 
            # Metadata handling strategy:
            # - folder_dataset: never use metadata (folder extraction is authoritative)
            # - filename (multi-author): never use metadata (don't replace with list)
            # - filename (single-word): allow metadata (for expanding incomplete names)
            # - metadata source: always use metadata (for normalization/conversions)
            
            # Special case: filename extraction of shared surname (like "Белаш", "Каменские")
            # If proposed_author is single word (surname only) and metadata has multiple
            # authors with this surname, restore them all
            if (record.author_source == "filename" and
                len(record.proposed_author.strip().split()) == 1 and
                record.metadata_authors):
                # Check if metadata authors share the same surname
                surname_candidate = record.proposed_author.strip()
                # Handle both separators: '; ' and ', '
                if '; ' in record.metadata_authors:
                    metadata_authors_list = [a.strip() for a in record.metadata_authors.split('; ')]
                elif ', ' in record.metadata_authors:
                    metadata_authors_list = [a.strip() for a in record.metadata_authors.split(', ')]
                else:
                    metadata_authors_list = [record.metadata_authors.strip()]
                
                def extract_surname_root(name: str):
                    """Extract surname root for fuzzy matching.
                    
                    Handles Russian surname variations:
                    - "Каменские" → "Камен"
                    - "Каменский" → "Камен"
                    - "Каменская" → "Камен"
                    """
                    words = name.split()
                    if not words:
                        return name.lower()
                    
                    # Get last word (likely surname after normalization or as-is from filename)
                    surname = words[-1] if len(words) > 1 else words[0]
                    surname_lower = surname.lower()
                    
                    # Remove common Russian surname endings
                    for ending in ('ские', 'ский', 'ского', 'скому', 'ским', 'ске',
                                   'ская', 'скую', 'ской',
                                   'ое', 'ого', 'ому', 'ым', 'ом',
                                   'ий', 'ие', 'ого', 'ому', 'ым', 'ом'):
                        if surname_lower.endswith(ending):
                            return surname_lower[:-len(ending)]
                    
                    return surname_lower
                
                matching_authors = []
                candidate_root = extract_surname_root(surname_candidate)

                for a in metadata_authors_list:
                    # Check if surname root matches
                    # Support both exact word match and root-based matching
                    author_words = a.split()
                    author_root = extract_surname_root(a)
                    
                    # Match if: exact word found OR root matches
                    if surname_candidate in author_words or author_root == candidate_root:
                        matching_authors.append(a)
                
                # If multiple authors with this surname in metadata, restore them
                if len(matching_authors) > 1:
                    # Normalize each author to "Фамилия Имя" format and sort by surname
                    normalized_authors = []
                    for author in matching_authors:
                        normalized = self.normalizer.normalize_format(author)
                        normalized_authors.append(normalized)
                    
                    # Sort by surname (first word for Russian names after normalization)
                    def get_surname_key(author_str):
                        words = author_str.split()
                        return words[0].lower() if words else author_str.lower()
                    
                    normalized_authors.sort(key=get_surname_key)
                    record.proposed_author = '; '.join(normalized_authors)
                    # Skip second normalization pass — authors are already in correct format
                    record.skip_normalization = True
                else:
                    record.skip_normalization = False
            else:
                record.skip_normalization = False
            
            if record.author_source == "folder_dataset":
                # For folder_dataset with multi-author, use metadata to fix incomplete names
                # Example: "Белаш Александр, Людмила" + metadata → "Белаш Александр, Белаш Людмила"
                has_separator = ', ' in record.proposed_author or '; ' in record.proposed_author
                if has_separator:
                    metadata_for_normalization = record.metadata_authors
                else:
                    metadata_for_normalization = ""
            elif record.author_source in ("filename", "filename+meta_expanded"):
                # For filename / filename+meta_expanded: use metadata strategy depends on structure
                has_separator = ', ' in record.proposed_author or '; ' in record.proposed_author
                
                if has_separator:
                    # Co-authors from filename: DO use metadata to expand surnames to full names
                    # Example: "Демидова, Конторович" + metadata → can become "Демидова Нина, Конторович Александр"
                    metadata_for_normalization = record.metadata_authors
                else:
                    # Single author from filename: only use metadata if incomplete (single word)
                    # This handles surname-only extractions that might be restored in lines 63-92
                    author_words = len(record.proposed_author.strip().split())
                    if author_words == 1:
                        # Single incomplete name - can use metadata for expansion
                        metadata_for_normalization = record.metadata_authors
                    else:
                        # Full author name already extracted - don't override with metadata
                        metadata_for_normalization = ""
            else:
                metadata_for_normalization = record.metadata_authors
            
            # folder_multiauthor: не расширяем из metadata, но порядок слов нормализуем.
            # "Евгений Красницкий" → "Красницкий Евгений", "Красницкий и другие" — без изменений.
            if record.author_source == 'folder_multiauthor':
                normalized = self.normalizer.normalize_format(record.proposed_author, "")

            elif '; ' in record.proposed_author or ', ' in record.proposed_author:
                # Determine separator
                sep = '; ' if '; ' in record.proposed_author else ', '
                # Only normalize if not restored from metadata (which are already correct)
                if not getattr(record, 'skip_normalization', False):
                    normalized = self.normalizer.normalize_format(record.proposed_author, metadata_for_normalization)
                else:
                    # Already restored from metadata with correct format, don't transform
                    normalized = record.proposed_author
            else:
                # Single author — normalize
                # If metadata contains a noble/foreign particle (де, van, фон…),
                # normalize_format will reorder the name wrongly (e.g. "Берньер Луи де").
                # Skip normalize_format entirely and use the metadata form directly.
                _PARTICLES_EARLY = frozenset({
                    'де', 'ди', 'дю', 'ду', 'да', 'дер', 'ден', 'дель', 'дела', 'делла',
                    'дос', 'дас', 'ван', 'фон', 'ля', 'ле', 'ла',
                    'de', 'di', 'du', 'da', 'der', 'den', 'van', 'von',
                    'la', 'le', 'les', 'del', 'della', 'dos', 'das',
                })
                _meta_words_lower = [
                    w.lower() for w in metadata_for_normalization.split()
                ] if metadata_for_normalization else []
                _prop_words_lower = [w.lower() for w in record.proposed_author.split()]
                _has_particle = (
                    any(w in _PARTICLES_EARLY for w in _meta_words_lower) or
                    any(w in _PARTICLES_EARLY for w in _prop_words_lower)
                )
                if _has_particle and record.metadata_authors:
                    # Use the first author from metadata as-is (natural order).
                    # Covers both cases:
                    #   "Берньер" + meta "Луи де Берньер" → "Луи де Берньер"
                    #   "де Виган Дельфин" (from filename) + meta "Дельфин де Виган" → "Дельфин де Виган"
                    _meta_natural = record.metadata_authors.split(';')[0].strip()
                    normalized_candidate = _meta_natural if _meta_natural else record.proposed_author
                else:
                    normalized_candidate = self.normalizer.normalize_format(record.proposed_author, metadata_for_normalization)

                # For filename-sourced multi-word authors: the block extractor already guarantees
                # ФИ order (Фамилия first). If normalize_format reordered the words (first word
                # changed), the heuristics fired incorrectly — keep original ФИ order instead.
                # Examples: "Линдквист Йон Айвиде", "Феррандис Хуан Франсиско"
                if (record.author_source in ("filename", "filename+meta_expanded", "folder_dataset")
                        and normalized_candidate
                        and len(record.proposed_author.split()) >= 2
                        and metadata_for_normalization == ""):
                    orig_first = record.proposed_author.split()[0].lower().replace('ё', 'е')
                    norm_first = normalized_candidate.split()[0].lower().replace('ё', 'е')
                    if orig_first != norm_first:
                        # normalize_format tried to reorder — check if metadata confirms the new order.
                        # Example: "Чжэён Пак" (filename) → normalized "Пак Чжэен":
                        #   metadata_authors="Пак Чжэен", meta_first="пак" == norm_first="пак" → accept.
                        # Example: "Линдквист Йон Айвиде" → normalized "Айвиде …":
                        #   metadata confirms "Линдквист …" → meta_first != norm_first → keep original.
                        _meta_first = (record.metadata_authors or "").strip().split()[0].lower().replace('ё', 'е') \
                            if record.metadata_authors else ""
                        if _meta_first and _meta_first == norm_first:
                            # Metadata confirms the reorder — trust normalization (includes ё→е)
                            normalized = normalized_candidate
                        else:
                            # Keep original ФИ order; still apply ё→е and conversions
                            normalized = self.normalizer.apply_conversions(
                                record.proposed_author
                            ).replace('ё', 'е')
                    else:
                        normalized = normalized_candidate
                else:
                    normalized = normalized_candidate
            
            if normalized and normalized != record.proposed_author:
                orig_was_single = ' ' not in record.proposed_author.strip()
                record.proposed_author = normalized
                normalized_count += 1
                # If a single-word filename surname was expanded using metadata → mark provenance
                if (orig_was_single
                        and ' ' in normalized
                        and record.author_source == 'filename'
                        and metadata_for_normalization):
                    record.author_source = 'filename+meta_expanded'

        # Капитализация: каждое слово в proposed_author начинается с заглавной буквы.
        # Исключения: "Соавторство", "Сборник" — уже корректны.
        # Частицы имён (де, ван, фон…) всегда остаются строчными.
        _PARTICLES_LOWER = frozenset({
            'де', 'ди', 'дю', 'ду', 'да', 'дер', 'ден', 'дель', 'дела', 'делла',
            'дос', 'дас', 'ван', 'фон', 'ля', 'ле', 'ла', 'о',
            'de', 'di', 'du', 'da', 'der', 'den', 'van', 'von',
            'la', 'le', 'les', 'del', 'della', 'dos', 'das',
        })
        for record in records:
            if not record.proposed_author:
                continue
            if record.proposed_author in ("Сборник", "Соавторство", "[unknown]"):
                continue
            _ROMAN_CHARS = frozenset('IVXLCDM')
            def _fix_word(w):
                if not w:
                    return w
                if w.lower() in _PARTICLES_LOWER:
                    return w
                # Слово целиком в верхнем регистре и не аббревиатура (нет точек, длиннее 1 символа)
                if w == w.upper() and len(w) > 1 and '.' not in w:
                    # Римские цифры (I, V, X, L, C, D, M) оставляем в верхнем регистре
                    if all(c in _ROMAN_CHARS for c in w):
                        return w
                    return w[0] + w[1:].lower()
                return w[0].upper() + w[1:]
            capitalized = ' '.join(_fix_word(w) for w in record.proposed_author.split(' '))
            if capitalized != record.proposed_author:
                record.proposed_author = capitalized

        # Удалить двоеточия из имён авторов (в т.ч. китайское «：» U+FF1A).
        # Формат «作者：牛顿不秃顶» содержит метку «Автор:»; берём часть ПОСЛЕ двоеточия.
        import re as _re_colon
        for record in records:
            if not record.proposed_author:
                continue
            for colon_char in ('：', ':'):
                if colon_char in record.proposed_author:
                    record.proposed_author = record.proposed_author.split(colon_char, 1)[1].strip()
                    break

        # Санитизация: убрать символы, недопустимые в именах папок Windows/Linux,
        # а также случайные знаки "=", которые иногда попадают из метаданных.
        # Windows-запрещённые: \ / : * ? " < > |   Linux-запрещённые: /
        import re as _re_san
        _SANITIZE_RE = _re_san.compile(r'[\\/:*?"<>=|]')
        for record in records:
            if record.proposed_author:
                sanitized = _SANITIZE_RE.sub('', record.proposed_author).strip()
                if sanitized != record.proposed_author:
                    record.proposed_author = sanitized

        # Повторная дедупликация ПОСЛЕ нормализации:
        # нормализация может привести "Максим Гаусс" → "Гаусс Максим",
        # создав дубль если рядом уже было "Гаусс Максим".
        for record in records:
            if not record.proposed_author or record.proposed_author in ("Сборник", "Соавторство"):
                continue
            if ', ' not in record.proposed_author and '; ' not in record.proposed_author:
                continue
            sep = '; ' if '; ' in record.proposed_author else ', '
            _parts = [a.strip() for a in record.proposed_author.replace(';', ',').split(',')]
            _seen: list = []
            for _p in _parts:
                if _p and _p.lower() not in [x.lower() for x in _seen]:
                    _seen.append(_p)
            if len(_seen) < len(_parts):
                record.proposed_author = sep.join(_seen)

        # ФИНАЛЬНАЯ НОРМАЛИЗАЦИЯ ФОРМАТА:
        # Правило: ТОЛЬКО "Фамилия Имя" для каждого автора, ТОЛЬКО ", " между соавторами.
        # 1. Заменяем "; " → ", "
        # 2. Обрезаем отчество (3-е и последующие слова в имени автора)
        _SKIP = {"Сборник", "Соавторство", "[unknown]"}
        for record in records:
            if not record.proposed_author or record.proposed_author in _SKIP:
                continue
            # Нормализуем разделитель: всегда ", "
            raw = record.proposed_author.replace('; ', ', ')
            # Разбиваем по соавторам
            authors = [a.strip() for a in raw.split(', ') if a.strip()]
            # Каждый автор — не более 2 слов (Фамилия Имя), отчество отбрасываем.
            # Одиночная буква (инициал) всегда заканчивается точкой: "А" → "А."
            # Многобуквенное слово получает точку ТОЛЬКО если это аббревиатура:
            #   - Все символы — согласные («Дж», «Мл», «Ст») — паттерн сокращения
            #   - Или слово есть в списке known_initials_and_suffixes из конфига
            # Слова с гласными («Оз», «Ли», «Ян») — это имена/фамилии, точка не нужна.
            _RU_VOWELS = frozenset('аеёиоуыэюяАЕЁИОУЫЭЮЯ')
            _LAT_VOWELS = frozenset('aeiouAEIOU')
            _ALL_VOWELS = _RU_VOWELS | _LAT_VOWELS
            import re as _re_dot
            _known_abbr = {w.lower() for w in (self.settings.get_list('author_initials_and_suffixes') or [])} \
                if self.settings else set()

            def _is_abbreviation(word: str) -> bool:
                """True если слово — инициал или аббревиатура (должна заканчиваться точкой)."""
                w = word.rstrip('.')
                if not w:
                    return False
                # 1 буква — однозначно инициал
                if len(w) == 1 and w[0].isupper():
                    return True
                # В списке — однозначно аббревиатура (Мл, Ст, Дж и т.д.)
                if w.lower() in _known_abbr:
                    return True
                # Все символы — согласные (нет гласных) — типичный паттерн аббревиатуры
                if (len(w) >= 2 and w[0].isupper()
                        and not any(c in _ALL_VOWELS for c in w)):
                    return True
                return False

            trimmed = []
            for auth in authors:
                words = auth.split()
                if len(words) > 2:
                    # Names with particles (де, ван, фон, ла, …) form compound surnames
                    # and must NOT be truncated — "де ла Мотт Андерс" must stay intact.
                    _SURNAME_PARTICLES = frozenset({
                        'де', 'ди', 'дю', 'ду', 'да', 'дер', 'ден', 'дель', 'дела', 'делла',
                        'дос', 'дас', 'ван', 'фон', 'ля', 'ле', 'ла',
                        'de', 'di', 'du', 'da', 'der', 'den', 'van', 'von',
                        'la', 'le', 'les', 'del', 'della', 'dos', 'das',
                    })
                    words_lower = [w.lower() for w in words]
                    if any(w in _SURNAME_PARTICLES for w in words_lower):
                        pass  # compound surname — keep all words as-is
                    elif '(' in auth:
                        pass  # pseudonym suffix "(Real Name)" — keep as-is, truncation breaks brackets
                    else:
                        # Skip truncation for co-author expressions like "Аркадий и Борис Стругацкие"
                        # where "и" is a connector word, not part of a single person's name.
                        words_lower_set = {w.lower() for w in words}
                        if 'и' in words_lower_set:
                            pass  # keep as-is — co-author expression, handled elsewhere
                        else:
                            # Try AuthorName normalization and check what the 3rd+ word
                            # actually IS via .parts (lastname, firstname, patronymic),
                            # rather than just counting words. A real Russian patronymic
                            # (Иванов Иван Иванович) is meant to be dropped — but a plain
                            # extra given-name word that AuthorName correctly folded into
                            # firstname (e.g. "Брэдбери Рэй Дуглас" → lastname="Брэдбери",
                            # firstname="Рэй Дуглас", patronymic=None) must NOT be chopped
                            # to the first two words — that would silently drop "Дуглас".
                            try:
                                from name_normalizer import AuthorName as _AN
                            except ImportError:
                                from ..name_normalizer import AuthorName as _AN
                            _an = _AN(auth)
                            if _an.is_valid:
                                _lastname, _firstname, _patronymic = _an.parts
                                if _patronymic:
                                    # Настоящее отчество — отбрасываем, оставляем "Фамилия Имя"
                                    auth = f"{_lastname} {_firstname}" if _lastname and _firstname else _an.normalized
                                else:
                                    # Никакого отчества нет — все слова уже корректно
                                    # распределены между фамилией и именем, не обрезаем.
                                    auth = _an.normalized
                            else:
                                auth = ' '.join(words[:2])
                fixed = []
                for w in auth.split():
                    if _re_dot.match(r'^[А-ЯЁA-Z][а-яёa-zA-Z]?\.?$', w) and _is_abbreviation(w):
                        fixed.append(w.rstrip('.') + '.')
                    else:
                        fixed.append(w)
                trimmed.append(' '.join(fixed))
            # Дедупликация
            seen: list = []
            for a in trimmed:
                if a.lower() not in [x.lower() for x in seen]:
                    seen.append(a)
            result = ', '.join(seen)
            if result != record.proposed_author:
                record.proposed_author = result

        # Нормализация ё→е в proposed_series:
        # Разные FB2-файлы одной серии могут иметь ё в одних и е в других,
        # что приводит к расхождению proposed_series ("Тёмные звёзды" vs "Темные звезды").
        for record in records:
            if record.proposed_series:
                normalized_series = _nfc_yo_to_ye(record.proposed_series)
                if normalized_series != record.proposed_series:
                    record.proposed_series = normalized_series

        # Снять комбинированные диакритические знаки (знаки ударения е́ → е) из имён авторов.
        for record in records:
            if record.proposed_author:
                stripped = _strip_diacritics(record.proposed_author)
                if stripped != record.proposed_author:
                    record.proposed_author = stripped

        self.logger.log(f"[PASS 3] Normalized {normalized_count} author names")
