"""
PRECACHE Phase: Build author folder hierarchy before PASS 1.
"""

from pathlib import Path
from typing import Dict, Tuple, Optional, Set
from .passes.folder_author_parser import parse_author_from_folder_name
from .extraction_constants import FILE_EXTENSION_FOLDER_NAMES


class Precache:
    """PRECACHE: Recursively scan folder hierarchy and cache author folders."""
    
    def __init__(self, work_dir: Path, settings, logger, folder_parse_limit: int):
        """Initialize PRECACHE.
        
        Args:
            work_dir: Working directory to scan
            settings: SettingsManager instance
            logger: Logger instance
            folder_parse_limit: Maximum depth for folder parsing
        """
        self.work_dir = work_dir
        self.settings = settings
        self.logger = logger
        self.folder_parse_limit = folder_parse_limit
        self.author_folder_cache: Dict[Path, Tuple[str, str]] = {}
        self.male_names: Set[str] = set()
        self.female_names: Set[str] = set()
        self._load_name_sets()
    
    def _load_name_sets(self) -> None:
        """Load male and female name lists from config (convert to lowercase for consistent validation)."""
        try:
            # Load names and convert to lowercase for case-insensitive validation
            self.male_names = set(name.lower() for name in self.settings.get_male_names())
            self.female_names = set(name.lower() for name in self.settings.get_female_names())
            print(f"[PRECACHE] Loaded {len(self.male_names)} male names, "
                  f"{len(self.female_names)} female names for validation")
        except Exception as e:
            self.logger.log(f"[PRECACHE] Failed to load name sets: {e}")
            print(f"[PRECACHE] WARNING: Failed to load name sets: {e}")
    
    def _contains_valid_name(self, author_name: str) -> bool:
        """Check if author_name contains at least one valid person name.
        
        Handles both full names ("Олег Сапфир") and abbreviated names ("О.Сапфир" or "А.Михайловский").
        
        Args:
            author_name: Author name to validate (e.g., "Олег Сапфир" or "А.Михайловский")
            
        Returns:
            True if at least one word is found in male_names or female_names, OR
            if the name contains surname-like pattern (Initial.Surname)
        """
        if not author_name:
            return False
        
        # Check for abbreviated name pattern: "А.Михайловский" or "А. Михайловский"
        # Pattern: single capital letter (NOT preceded by another Cyrillic letter — i.e. a real initial,
        # not the last letter of an acronym like "МИФ") followed by optional dot/space and a surname.
        # Negative lookbehind (?<![а-яёА-Я]) prevents "Ф" in "МИФ." from matching as an initial.
        import re
        if re.search(r'(?<![а-яёА-Я])[А-Я]\.*\s*[А-Я][а-яё]+', author_name):
            return True  # Matches abbreviated name pattern
        
        # Split author name into words
        words = author_name.split()
        
        # Check if any word is in our name sets
        for word in words:
            word_clean = word.strip('.,;:!?').lower()  # Remove punctuation and convert to lowercase
            # Normalise ё→е so "Пётр" matches "петр" in the name list
            word_norm = word_clean.replace('ё', 'е')
            if word_clean in self.male_names or word_clean in self.female_names:
                return True
            if word_norm in self.male_names or word_norm in self.female_names:
                return True
        return False

    def execute(self, filter_paths=None) -> Dict[Path, Tuple[str, str]]:
        """Execute PRECACHE: Build author folder cache.

        Args:
            filter_paths: Optional list/set of absolute Path objects. When provided,
                          only these folders (and their subtrees) are scanned. The
                          parent chain up to work_dir is also scanned to detect
                          author_subfolder_collections context, but NOT cached as
                          authors themselves — only the subtrees of filter_paths are.

        Returns:
            Dictionary {folder_path: (author_name, confidence)}
        """
        # Ensure stdout can handle Cyrillic folder names on any platform/encoding.
        import sys
        try:
            if sys.stdout.encoding and sys.stdout.encoding.lower().replace('-', '') not in ('utf8', 'utf8bom'):
                sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, Exception):
            pass

        print("[PRECACHE] Building author folder hierarchy...")
        
        conversions = self.settings.get_author_surname_conversions()
        collection_names = {
            s.lower() for s in (self.settings.get_author_subfolder_collections() or [])
        }
        genre_prefixes = [p.lower() for p in (self.settings.get_genre_folder_prefixes() or [])]
        import re as _re_pat
        author_patterns = [
            _re_pat.compile(p)
            for p in (self.settings.get_author_folder_name_patterns() or [])
        ]

        def scan_folder_hierarchy(folder: Path, depth: int = 0,
                                   inside_author_folder: bool = False,
                                   force_author: bool = False,
                                   inside_genre_folder: bool = False,
                                   _no_recurse: bool = False) -> Optional[Tuple[str, str]]:
            """Recursively scan folders and cache authors.

            Args:
                inside_author_folder: True when we are already inside a confirmed author
                    folder. In this case subfolders are SERIES, not authors — we must
                    not attempt to parse them as author names.
            """

            # Never process work_dir itself — but cache it as author if it qualifies.
            # This ensures that when scanning e.g. "Волков Тим/" directly, ALL files
            # inside get folder_dataset = "Волков Тим" with no exceptions.
            if folder == self.work_dir:
                wd_name = folder.name
                wd_name_for_parse = conversions.get(wd_name, wd_name)
                if wd_name in conversions:
                    wd_author = wd_name_for_parse
                else:
                    wd_author = parse_author_from_folder_name(
                        wd_name_for_parse,
                        male_names=self.male_names,
                        female_names=self.female_names,
                    )
                wd_is_author = bool(wd_author and self._contains_valid_name(wd_author))
                if wd_is_author:
                    # Cache work_dir as author so Pass1 assigns folder_dataset to ALL files
                    self.author_folder_cache[folder] = (wd_author, "high")
                    print(f"[CACHE] Work_dir is AUTHOR: {wd_name} → '{wd_author}'")
                try:
                    for subdir in folder.iterdir():
                        if subdir.is_dir() and not subdir.name.startswith('.'):
                            scan_folder_hierarchy(subdir, depth + 1,
                                                  inside_author_folder=wd_is_author)
                except (PermissionError, OSError):
                    pass
                return None

            if depth > self.folder_parse_limit:
                return None

            folder_name = folder.name
            if not folder_name or folder_name.startswith('.'):
                return None

            # Прозрачно пропускаем папки с именами-расширениями (fb2, pdf, epub…)
            # Структура "Автор\fb2\Серия" обрабатывается как "Автор\Серия".
            if folder_name.lower() in FILE_EXTENSION_FOLDER_NAMES:
                try:
                    for subdir in folder.iterdir():
                        if subdir.is_dir() and not subdir.name.startswith('.'):
                            # depth не увеличивается, inside_author_folder наследуется
                            scan_folder_hierarchy(subdir, depth, inside_author_folder)
                except (PermissionError, OSError):
                    pass
                return None

            # Если мы уже внутри авторской папки — эта папка является серией, не автором.
            # Не пытаемся её парсить и не рекурсируем глубже в поисках авторов.
            if inside_author_folder:
                return None

            # Check cache
            if folder in self.author_folder_cache:
                return self.author_folder_cache[folder]

            # Apply conversions to folder name
            folder_name_to_parse = conversions.get(folder_name, folder_name)

            # If folder name is explicitly in conversions, use the value verbatim —
            # this pins Latin pseudonyms like "Myrmice Orlyett" without word reordering.
            if folder_name in conversions:
                author_name = folder_name_to_parse
            else:
                # Apply PASS0+PASS1+PASS2 structural analysis
                author_name = parse_author_from_folder_name(
                    folder_name_to_parse,
                    male_names=self.male_names,
                    female_names=self.female_names,
                )

            # Check if folder contains FB2 files
            has_fb2_files = False
            try:
                for item in folder.iterdir():
                    if item.is_file() and (
                        item.suffix.lower() == '.fb2'
                        or item.name.lower().endswith('.fb2.zip')
                    ):
                        has_fb2_files = True
                        break
            except (PermissionError, OSError):
                pass

            # Дети genre-папок — это серии внутри цикла, не авторы.
            # Исключение: если сама папка — авторская коллекция (in collection_names),
            # её подпапки всё равно являются авторами — рекурсируем с force_author=True.
            if inside_genre_folder:
                if folder_name.lower() in collection_names:
                    try:
                        for subdir in folder.iterdir():
                            if subdir.is_dir() and not subdir.name.startswith('.'):
                                scan_folder_hierarchy(subdir, depth + 1, force_author=True)
                    except (PermissionError, OSError):
                        pass
                return None

            # Пропускаем жанровые/издательские папки — они не являются авторами
            if any(folder_name.lower().startswith(p) for p in genre_prefixes):
                try:
                    for subdir in folder.iterdir():
                        if subdir.is_dir() and not subdir.name.startswith('.'):
                            scan_folder_hierarchy(subdir, depth + 1,
                                                  inside_genre_folder=True)
                except (PermissionError, OSError):
                    pass
                return None

            # Паттерны вида "NN. Серия - Автор" → берём capture group 1 как автора
            for pat in author_patterns:
                m = pat.match(folder_name)
                if m:
                    pattern_author = m.group(1).strip()
                    if pattern_author and depth > 0:
                        result = (pattern_author, 'high')
                        self.author_folder_cache[folder] = result
                        print(f"[CACHE] Pattern match: {folder.name} → '{pattern_author}'")
                        try:
                            for subdir in folder.iterdir():
                                if subdir.is_dir() and not subdir.name.startswith('.'):
                                    scan_folder_hierarchy(subdir, depth + 1,
                                                          inside_author_folder=True)
                        except (PermissionError, OSError):
                            pass
                        return result
                    break

            # force_author: папка внутри коллекции — всегда автор, без проверки словаря
            # folder_name in conversions: явно пинённый псевдоним (самомапинг) — тоже без валидации
            # (fb2 могут быть в подпапках серии, а не напрямую)
            # Паттерн «Серия (Фамилия)»: одно кириллическое слово в скобках в конце —
            # ловит "Воин Грёзы (Широков)" где фамилия без имени не в словаре имён.
            import re as _re_isau
            _paren_surname = _re_isau.match(
                r'^.+\(([А-ЯЁ][а-яёА-ЯЁ\-]{2,}(?:\s[А-ЯЁ][а-яёА-ЯЁ\-]{2,})?)\)\s*$',
                folder_name,
            )
            is_author = (
                force_author
                or folder_name in conversions
                or (has_fb2_files and author_name and self._contains_valid_name(author_name))
                or (has_fb2_files and bool(_paren_surname))
            )

            if is_author:
                if force_author and folder_name not in conversions:
                    # Для force_author: всегда стрипим скобки ПЕРЕД парсингом
                    import re as _re
                    clean = _re.sub(r'\s*\(.*?\)', '', folder_name).strip()
                    author_name = parse_author_from_folder_name(
                        clean, male_names=self.male_names, female_names=self.female_names)
                    if not author_name and '-' in clean:
                        author_name = parse_author_from_folder_name(
                            clean.replace('-', ' '),
                            male_names=self.male_names, female_names=self.female_names)
                    # Если парсер вернул строку как есть и она содержит дефис —
                    # пробуем без дефиса ("Андреев-Александр Владимирович" → "Андреев Александр Владимирович").
                    if '-' in clean and (not author_name or author_name == clean):
                        nodash = clean.replace('-', ' ')
                        if self._contains_valid_name(nodash):
                            author_name = nodash
                    # Если распарсенное имя не содержит известных имён (псевдоним на латинице
                    # вроде "Bel Jonson") — использовать имя папки как есть
                    if author_name and not self._contains_valid_name(author_name):
                        author_name = clean
                    if not author_name:
                        author_name = clean
                elif not author_name:
                    import re as _re
                    author_name = _re.sub(r'\s*\(.*?\)', '', folder_name).strip()
                if depth > 0:
                    result = (author_name, "high")
                    self.author_folder_cache[folder] = result
                    print(f"[CACHE] Added HIGH: {folder.name} → '{author_name}'")
                try:
                    for subdir in folder.iterdir():
                        if subdir.is_dir() and not subdir.name.startswith('.'):
                            scan_folder_hierarchy(subdir, depth + 1,
                                                  inside_author_folder=True)
                except (PermissionError, OSError):
                    pass
                return result

            # If name parses as author but fails validation → skip caching
            elif author_name and has_fb2_files and not self._contains_valid_name(author_name):
                if depth > 0:
                    print(f"[CACHE] Skipped (no valid names): {folder.name} → '{author_name}'")
                # Don't cache, allow parent inheritance to work

            # If folder is not author but name parses → cache for inheritance (no FB2 files).
            elif author_name and depth > 0 and self._contains_valid_name(author_name):
                result = (author_name, "low")
                self.author_folder_cache[folder] = result


            # Recursively scan subfolders (не авторская папка — ищем глубже)
            # Если эта папка — коллекция, её дочерние папки принудительно авторские
            if _no_recurse:
                return None
            this_is_collection = folder_name.lower() in collection_names
            try:
                for subdir in folder.iterdir():
                    if subdir.is_dir() and not subdir.name.startswith('.'):
                        scan_folder_hierarchy(subdir, depth + 1,
                                              force_author=this_is_collection)
            except (PermissionError, OSError):
                pass

            return None
        
        # Start scanning
        try:
            if filter_paths:
                _filter_abs = {Path(p).resolve() for p in filter_paths}
                # Для каждой выбранной папки: сначала пройти цепочку родителей вверх до
                # work_dir чтобы определить контекст (force_author, inside_genre_folder и т.д.),
                # затем полностью просканировать саму выбранную папку.
                # Родители сканируются только для контекста — без рекурсии в их дочерние папки.
                for target in _filter_abs:
                    # Строим цепочку папок от work_dir до target (не включая target)
                    try:
                        rel = target.relative_to(self.work_dir)
                    except ValueError:
                        continue
                    parts = rel.parts
                    # Проходим каждый уровень от work_dir вниз, без рекурсии в стороны
                    current = self.work_dir
                    depth = 0
                    for part in parts[:-1]:  # все уровни кроме самого target
                        current = current / part
                        scan_folder_hierarchy(current, depth=depth, _no_recurse=True)
                        depth += 1
                    # Полное сканирование самой выбранной папки
                    scan_folder_hierarchy(target, depth=depth)
            else:
                scan_folder_hierarchy(self.work_dir, depth=0)
            print(f"[PRECACHE] Cached {len(self.author_folder_cache)} author folders\n")
            self.logger.log(f"[PRECACHE] Cached {len(self.author_folder_cache)} author folders")
        except Exception as e:
            self.logger.log(f"[PRECACHE] Error: {e}")

        return self.author_folder_cache
