"""
PASS 4: Apply consensus author to files in same folder.
"""

import re
import unicodedata
from pathlib import Path
from typing import List, Dict, Optional


from ..series_normalizer import _nfc_lower_yo


from ..author_normalizer_extended import AuthorNormalizer
from ..settings_manager import SettingsManager
from ..series_processor import SeriesProcessor


class Pass4Consensus:
    """PASS 4: Apply consensus author to files in same folder.
    
    For each folder group:
    1. Identify "determined" files with reliable source (folder_dataset, metadata, consensus)
    2. Identify "undetermined" files (empty or filename source)
    3. Apply consensus author (most common) to undetermined files
    
    ⚠️ CRITICAL: Files with ANY successful source are NEVER overwritten!
    - folder_dataset: Extracted from folder hierarchy
    - filename: Successfully parsed from file name  
    - metadata: From FB2 XML metadata
    - consensus: Already has consensus from previous folder
    - empty string: Only undetermined files get new consensus
    """
    
    def __init__(self, logger, settings=None):
        """Initialize PASS 4.
        
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
        self.series_processor = SeriesProcessor(self.settings.config_path if self.settings else 'config.json')
        # Cache for _normalize_series_for_consensus results
        self._series_norm_cache: dict = {}
    
    def _normalize_series_for_consensus(self, series_candidate: str) -> str:
        """
        Normalize series candidate for consensus comparison.
        Removes volume numbers so "Охотник 1" and "Охотник 2" match as same series.
        
        Args:
            series_candidate: Raw series candidate string
        
        Returns:
            Normalized series name
        """
        if not series_candidate:
            return ""

        cached = self._series_norm_cache.get(series_candidate)
        if cached is not None:
            return cached

        import re

        text = series_candidate.strip()

        # Remove " N" or " N. " patterns (space + digits).
        # НЕ трогать если число — часть десятичной версии: "Цивилизация 2.0", "Metro 2.0".
        # Признак версии: после числа сразу идёт точка и ещё цифра (lookahead (?!\.\d)).
        # "Охотник 1"        → "Охотник"
        # "Охотник 2. ..."   → "Охотник"
        # "Цивилизация 2.0"  → "Цивилизация 2.0"  (не трогать)
        text = re.sub(r'\s+\d+(?!\.\d)[\s\.]*$', '', text).strip()

        # Remove trailing digits after space
        text = re.sub(r'\s+\d+(?!\.\d)\s*$', '', text).strip()

        # Remove trailing digits after hyphen ("Фэндом-3" → "Фэндом"), не трогать "Серия-2.0"
        text = re.sub(r'[-–—]\d+(?!\.\d)\s*$', '', text).strip()

        result = text if text else series_candidate
        self._series_norm_cache[series_candidate] = result
        return result
    
    def execute(self, records: List) -> None:
        """Execute PASS 4: Apply consensus author.
        
        Group records by folder and apply consensus author to undetermined files.
        Protected files (folder_dataset, metadata) are not overwritten.
        
        Args:
            records: List of BookRecord objects to process
        """
        print("[PASS 4] Applying consensus...")

        # CLEANUP: Remove false "series" that are actually just titles/subtitles
        # These are single-appearance series with no numbering/service words markers
        # Example: "Осень 93-го" or "Баржа Т-36" (no other files in author's catalog with these series)
        print("[PASS 4] Cleaning up false series (single-file titles)...")
        false_series_count = 0
        
        # Build series frequency map by author
        import re
        author_series_count = {}
        for record in records:
            if record.proposed_series:
                author = record.proposed_author or "[unknown]"
                series = record.proposed_series
                
                key = (author, series)
                if key not in author_series_count:
                    author_series_count[key] = []
                author_series_count[key].append(record)
        
        # Load service words from config (markers that indicate THIS IS a series, not just a title)
        service_markers = set(self.settings.get_list('service_words')) if self.settings else set()
        
        # Check each (author, series) pair
        for (author, series), records_with_series in author_series_count.items():
            # If this series appears only ONCE (not a real series)
            if len(records_with_series) == 1:
                record = records_with_series[0]
                
                # PROTECTION: If series came from folder structure (folder_dataset),
                # DO NOT remove it! Folders are trusted sources
                if record.series_source == "folder_dataset":
                    continue  # Trust folder-based series, don't remove
                
                # Check if series contains service markers that indicate it's a real series
                series_lower = series.lower()
                has_service_marker = any(marker in series_lower for marker in service_markers)
                
                # FIX: Check if series is confirmed in metadata BEFORE removing it
                # Even if it's a single-file series from filename, if metadata confirms it,
                # we should keep it
                is_confirmed_in_metadata = (
                    record.metadata_series and 
                    record.metadata_series.strip().lower() == series.lower()
                )
                
                # NEW FIX: Check if the filename contains evidence of a legitimate series pattern
                # Pattern: "(Series. service_word)" or "(Series service_word)" like "(Солдат удачи. Тетралогия)" or "(Эпоха перемен Трилогия)"
                # Also handles "(Series N. Additional info. service_word)" like "(Мир вечного 1. Охота на охотника. Тетралогия)"
                # Also recognizes "(Series N-M)" patterns which are multi-file series indicators like "(Легион 1-3)", "(Легион 4-6)"
                # Also recognizes "(Novels/Romany из цикла «Series»)" patterns like "(Романы из цикла «Артуа»)"
                # This indicates the original filename HAD a service word or multi-file pattern before extraction
                has_pattern_evidence = False
                if record.file_path and record.series_source in ("filename", "filename+meta_confirmed"):
                    filename = Path(record.file_path).name
                    filename_no_ext = filename.rsplit('.', 1)[0]

                    # Числовой диапазон N-M в имени файла — надёжный признак серии
                    # Пример: "Wismurt. Отпрыск рода Орловых 1-5" → серия реальна
                    if re.search(r'\s+\d+[-–—]\d+\s*$', filename_no_ext):
                        has_pattern_evidence = True
                    # Одиночный числовой суффикс тоже признак серии
                    # Пример: "Leach23 (Михалек). Игрок, забравшийся на вершину 1-12"
                    elif re.search(r'\s+\d+\s*$', filename_no_ext):
                        has_pattern_evidence = True
                    # Число в середине перед ". Title" — паттерн "Author - Series N. Title"
                    # Пример: "Тидхар Леви - Центральная станция 2. Неом"
                    elif re.search(r'\s+\d+\.\s+\S', filename_no_ext):
                        has_pattern_evidence = True
                    
                    # Find ALL brackets in the filename
                    all_brackets = re.findall(r'\([^)]*\)', filename)
                    
                    for bracket_content in all_brackets:
                        bracket_lower = bracket_content.lower()
                        
                        # Check if this bracket content has service markers
                        have_service_marker_in_brackets = any(marker in bracket_lower for marker in service_markers)
                        if have_service_marker_in_brackets:
                            has_pattern_evidence = True
                            break
                        
                        # Check for multi-file patterns like "N-M" which strongly indicate series
                        if re.search(r'\d+[-,]\d+', bracket_content):
                            has_pattern_evidence = True
                            break
                        
                        # Check for "из цикла" or "из серии" patterns
                        # Examples: (Романы из цикла «Артуа»), (Книги из цикла «Серия»)
                        if re.search(r'из\s+(?:цикла|серии)', bracket_lower):
                            has_pattern_evidence = True
                            break
                        
                        # Check для multi-level series patterns like "Series N. SubSeries M. SubSubSeries K"
                        # Examples: (Сид 1. Принцип талиона 1. Геката 1), (Война 1. Мир 2. Система 3)
                        # Наличие 2+ точек-пробелов является веским доказательством иерархической структуры серии
                        if bracket_content.count('. ') >= 2:
                            has_pattern_evidence = True
                            break
                
                # If NO service markers AND from filename source AND NOT confirmed in metadata
                # AND NOT from a legitimate (Series. service_words) pattern
                # → Check if this is in a Series Collection folder before removing
                if (not has_service_marker and record.series_source == "filename" and 
                    not is_confirmed_in_metadata and not has_pattern_evidence):
                    
                    # Одиночный файл с X.Y в имени: X — серия только если X содержит цифру.
                    # Без цифры («Злой» дворник. Чистая правда…) X является частью заголовка.
                    _series_has_digit = bool(re.search(r'\d', record.proposed_series or ''))
                    if _series_has_digit:
                        # Есть цифра в имени серии → может быть настоящей серией, не трогаем
                        pass
                    else:
                        # Нет цифры → сбрасываем как часть заголовка (игнорируем исключение коллекций)
                        record.proposed_series = ""
                        record.series_source = ""
                        false_series_count += 1
                        continue

                    is_series_collection_folder = False
                    if record.file_path:
                        file_path_parts = Path(record.file_path).parts
                        if len(file_path_parts) >= 1:
                            parent_folder = file_path_parts[0]
                            is_series_collection_folder = (
                                parent_folder.startswith('Серия') or
                                'Серия' in parent_folder
                            )

                    # Only clear if it's NOT in a Series Collection folder
                    if not is_series_collection_folder:
                        # Clear it
                        record.proposed_series = ""
                        record.series_source = ""
                        false_series_count += 1

        
        self.logger.log(f"[PASS 4] Removed {false_series_count} false series (single-file titles)")
        
        # SPECIAL HANDLING: If metadata contains specific series values, they take absolute priority
        # These values override all other extraction methods
        special_series_values = self.settings.get_list('special_series_values') if self.settings else []
        
        for record in records:
            if record.metadata_series:
                metadata_series = record.metadata_series.strip()
                if metadata_series in special_series_values:
                    # This metadata value has absolute priority
                    record.proposed_series = metadata_series
                    record.series_source = "metadata"
        
        # ⚠️ REMOVED: METADATA AUTHOR CONFIRMATION logic
        # This logic was attempting to "improve" authors by cross-checking with metadata,
        # but this is fundamentally wrong:
        # 
        # 1. folder_dataset source is AUTHORITATIVE (user explicitly created folder hierarchy)
        #    → MUST NOT be modified or questioned
        # 
        # 2. Cross-checking with metadata is:
        #    - Resource-intensive (requires parsing every FB2 file)
        #    - Ineffective (metadata may be worse quality than folder_dataset)
        #    - Logically incorrect (damages confidence in folder-based extraction)
        # 
        # 3. Correct strategy:
        #    - folder_dataset → Final and should never be changed
        #    - filename → Can check metadata only if extraction is incomplete
        #    - metadata → Sufficient on its own, no need to cross-check
        
        # Group by folder
        groups: Dict[Path, List] = {}
        for record in records:
            folder = Path(record.file_path).parent
            if folder not in groups:
                groups[folder] = []
            groups[folder].append(record)
        
        # Apply author consensus using SeriesProcessor
        consensus_count = self.series_processor.apply_author_consensus(records)
        self.logger.log(f"[PASS 4] Applied consensus to {consensus_count} records")
        
        # SERIES CONSENSUS: Apply consensus series to files in same folder
        # IMPORTANT: Only apply to files that have extracted_series_candidate matching
        # the consensus candidate. This prevents applying unrelated series to files
        # that only happen to be in the same folder.
        # GROUP BY AUTHOR for consensus calculation
        # Build author groups: author → [records]
        author_groups = {}
        for record in records:
            author = record.proposed_author or "[unknown]"
            if author not in author_groups:
                author_groups[author] = []
            author_groups[author].append(record)
        
        # Apply series consensus using SeriesProcessor
        series_consensus_count = self.series_processor.apply_series_consensus(records)
        self.logger.log(f"[PASS 4] Applied author-based series consensus to {series_consensus_count} records")
        
        # METADATA SERIES CONSENSUS: For depth 2 files (Author/File)
        # Apply metadata_series consensus to files without proposed_series
        # This handles files that have metadata_series but it was rejected/empty
        print("[PASS 4] Applying metadata series consensus...")
        metadata_series_consensus_count = 0
        
        for folder, group_records in groups.items():
            # Count metadata_series occurrences (only from files with valid proposed_series)
            metadata_series_count = {}
            for record in group_records:
                # Consider medadata_series only if it resulted in proposed_series
                if record.metadata_series and record.proposed_series == record.metadata_series:
                    series = record.metadata_series
                    metadata_series_count[series] = metadata_series_count.get(series, 0) + 1
            
            # Учитываем серию если хотя бы ОДИН файл в папке имеет её в proposed_series
            consensus_metadata_series = {
                series: count 
                for series, count in metadata_series_count.items() 
                if count >= 1
            }
            
            if not consensus_metadata_series:
                continue

            # Применяем только если в папке одна-единственная серия из метаданных.
            # Если серий несколько — они конкурируют, применять нечего.
            if len(consensus_metadata_series) != 1:
                continue
            
            # Apply to files with empty proposed_series if they have empty proposed_series
            for record in group_records:
                if (not record.proposed_series and 
                    record.series_source != "no_series_folder" and
                    record.metadata_series and 
                    record.metadata_series in consensus_metadata_series):
                    
                    record.proposed_series = record.metadata_series
                    record.series_source = "consensus"
                    metadata_series_consensus_count += 1
        
        self.logger.log(f"[PASS 4] Applied metadata series consensus to {metadata_series_consensus_count} records")
        
        # PROPOSED SERIES CONSENSUS: For files without extracted_series_candidate
        # Apply proposed_series from other files in same folder when multiple files agree
        # This handles series folders where some files don't have extractable series names
        print("[PASS 4] Applying proposed series fallback consensus...")
        proposed_consensus_count = 0
        
        for folder, group_records in groups.items():
            # Count proposed_series occurrences (only from files with valid proposed_series)
            proposed_count = {}
            for record in group_records:
                if record.proposed_series:
                    series = record.proposed_series
                    proposed_count[series] = proposed_count.get(series, 0) + 1
            
            # Only consider series that appear 2+ times
            consensus_proposed_series = {
                series: count 
                for series, count in proposed_count.items() 
                if count >= 2
            }
            
            if not consensus_proposed_series:
                continue
            
            # Apply to files with empty proposed_series if they're in a series folder
            for record in group_records:
                # Only apply if:
                # 1. File has no proposed_series yet (empty)
                # 2. Not extracted from extracted_series_candidate (would be caught earlier)
                # 3. A proposed_series appears 2+ times in the group
                # ВАЖНО: проверяем что extracted_series_candidate is None, не just falsey
                # Потому что "" (empty string) означает что это одна книга, не серия —
                # НО делаем исключение если имя файла содержит консенсусную серию
                # (первая книга серии часто не имеет номера, отсюда пустой candidate).
                if not record.proposed_series and record.series_source != "no_series_folder":
                    if len(consensus_proposed_series) != 1:
                        continue
                    consensus_series = list(consensus_proposed_series.keys())[0]
                    esc = record.extracted_series_candidate
                    if esc is None:
                        pass  # обычный случай — применяем
                    elif esc == '':
                        # Пустая строка: разрешаем если серия есть в имени файла или в title
                        stem = _nfc_lower_yo(Path(record.file_path).stem)
                        title = _nfc_lower_yo(record.file_title or '')
                        cs_norm = _nfc_lower_yo(consensus_series)
                        if cs_norm not in stem and cs_norm not in title:
                            continue
                    else:
                        continue  # уже был другой кандидат
                    record.proposed_series = consensus_series
                    record.series_source = "consensus"
                    proposed_consensus_count += 1
        
        self.logger.log(f"[PASS 4] Applied proposed series consensus to {proposed_consensus_count} records")
        
        # SERIES AUTHOR CONSENSUS (Variant 5: Combined Protective Approach)
        # For each series with multiple files, apply the most common author to all files
        # in that series. This handles cases where a multi-author series has inconsistent
        # author assignments across files.
        # 
        # CRITICAL SAFEGUARDS for filename sources:
        #   1. Skip if metadata_authors confirm the extracted author
        #   2. Skip if this author is UNIQUE in the series (appears only once)
        #   3. Skip if consensus percentage < 80% (insufficient majority)
        #   4. Only apply if all checks pass AND consensus >= 80%
        # 
        # folder_dataset: NEVER change (sacred, user-defined folder hierarchy)
        # metadata/consensus/empty: Apply consensus if different (low-quality sources)
        # 
        # Case Studies:
        #
        # Case 1: "Капитонов Николай - Наемник 1" (filename source, unique)
        #   - Series "Наемник": 6 files (5 Поселягин, 1 Капитонов)
        #   - Consensus: Поселягин (83.3%) → YES, above threshold
        #   - But Капитонов is UNIQUE (count=1) → SKIP (might be co-author)
        #   Result: UNCHANGED ✓
        #
        # Case 2: "Ипатова" in "Врата Валгаллы" (filename source, incomplete)
        #   - Series "Врата Валгаллы": 3 files
        #     * File 1: "Ильин, Ипатова" (filename)
        #     * File 2: "Ильин, Ипатова" (filename)
        #     * File 3: "Ипатова" (filename, incomplete name)
        #   - Consensus: "Ильин, Ипатова" (66.7%) → Below 80% threshold
        #   - Ипатова appears 3 times → Not unique
        #   - Metadata confirms "Ипатова" is Ипатова → Safeguard 1 should skip
        #   Result: UNCHANGED (by Safeguard 1 - metadata confirmation)
        print("[PASS 4] Applying series author consensus...")
        series_author_consensus_count = 0
        
        # Group records by proposed_series
        series_groups = {}
        for record in records:
            if record.proposed_series:
                series = record.proposed_series
                if series not in series_groups:
                    series_groups[series] = []
                series_groups[series].append(record)
        
        # Apply author consensus within each series
        for series, series_records in series_groups.items():
            # Only apply consensus if there are 2+ files in the series
            if len(series_records) < 2:
                continue
            
            # Count author occurrences (only for files with valid authors)
            author_counts = {}
            for record in series_records:
                if record.proposed_author and record.proposed_author != "Сборник":
                    author = record.proposed_author
                    author_counts[author] = author_counts.get(author, 0) + 1
            
            if not author_counts:
                continue
            
            # Find most common author (consensus)
            consensus_author = max(author_counts, key=author_counts.get)
            consensus_count = author_counts[consensus_author]
            consensus_percentage = (consensus_count / len(series_records)) * 100
            
            # HELPER: Check if current_author is a subset/incomplete version of consensus_author
            def is_author_subset(current_author, consensus_author):
                """
                Check if current_author is an incomplete/subset version of consensus_author.
                Examples:
                  - "Ипатова Наталия" is subset of "Ильин Сергей, Ипатова Наталия" → YES
                  - "Капитонов Николай" is subset of "Поселягин Владимир" → NO
                """
                if current_author == consensus_author:
                    # Exact match - should be handled by skip logic above
                    return False
                
                # Normalize and split into words
                current_words = set(current_author.lower().replace(',', '').split())
                consensus_words = set(consensus_author.lower().replace(',', '').split())
                
                # Check if all words from current are in consensus (is subset)
                # AND current has fewer words (is incomplete version)
                if current_words and consensus_words:
                    is_subset = current_words.issubset(consensus_words)
                    is_shorter = len(current_words) < len(consensus_words)
                    return is_subset and is_shorter
                
                return False
            
            for record in series_records:
                # Skip if already matches consensus
                if record.proposed_author == consensus_author or record.proposed_author == "Сборник":
                    continue
                
                # PROTECTION 1: Never touch folder_dataset / folder_hierarchy
                if record.author_source.startswith(('folder_dataset', 'folder_hierarchy')):
                    continue
                
                # NEW LOGIC: Check if current author is a subset of consensus
                is_subset = is_author_subset(record.proposed_author, consensus_author)
                
                # For filename source
                if record.author_source in ("filename", "filename+meta_expanded"):
                    if is_subset:
                        # Current author is incomplete version of consensus → DEFINITELY apply
                        record.proposed_author = consensus_author
                        record.author_source = f"{record.author_source}+series-consensus"
                        series_author_consensus_count += 1
                    else:
                        # Current author is DIFFERENT, not subset → DON'T apply (might be co-author)
                        continue
                
                # For low-quality sources
                elif record.author_source in ["metadata", "consensus", ""]:
                    # Note: "metadata_folder_confirmed" is intentionally excluded —
                    # it is already confirmed by folder and treated as authoritative.
                    if is_subset:
                        # Incomplete version → apply
                        original_source = record.author_source or ""
                        record.proposed_author = consensus_author
                        if original_source:
                            record.author_source = f"{original_source}+series-consensus"
                        else:
                            record.author_source = "series-consensus"
                        series_author_consensus_count += 1
                    elif author_counts.get(record.proposed_author, 0) == 1:
                        # Unique author in series (might be co-author) → protect
                        continue
                    else:
                        # Not unique, not subset → still different author, skip
                        continue
        
        self.logger.log(f"[PASS 4] Applied series author consensus to {series_author_consensus_count} records")
        
        # HIERARCHICAL SERIES UNIFICATION
        # Валидация подсерий по именам файлов ДО консенсуса.
        # Если proposed_series = "X\Y" (из filename), проверяем сколько файлов автора
        # содержат Y как первый сегмент заголовка после "X N." в имени файла.
        # Если < 2 → Y это название книги, не подсерия → схлопываем в "X".
        # Запускаем ДО hierarchical unification, чтобы ложная подсерия не распространялась.
        print("[PASS 4] Validating filename-based subseries before consensus...")
        filename_subseries_fix_count = 0

        # Группируем записи по автору
        _author_recs: dict = {}
        for r in records:
            _author_recs.setdefault(r.proposed_author or '[unknown]', []).append(r)

        for author, author_recs in _author_recs.items():
            # Для каждой уникальной пары (base_series, subseries) из filename-источников
            # считаем сколько файлов автора имеют subseries как первый сегмент заголовка
            # после "base_series N." в stem файла.
            subseries_file_count: dict = {}  # (base_lc, sub_lc) → set of file stems

            for r in author_recs:
                s = r.proposed_series or ''
                if '\\' not in s:
                    continue
                if 'filename' not in (r.series_source or ''):
                    continue
                base = s.split('\\')[0].strip()
                sub  = s.split('\\', 1)[1].strip()
                if not base or not sub:
                    continue
                base_lc = base.lower().replace('ё', 'е')
                sub_lc  = sub.lower().replace('ё', 'е')
                key = (base_lc, sub_lc)

                # Ищем sub в stem файла: паттерн "base N. sub" или "base N. sub."
                stem = Path(r.file_path).stem.lower().replace('ё', 'е')
                # Вырезаем зону после "base [N]." в стеме
                _base_pos = stem.find(base_lc)
                if _base_pos < 0:
                    continue
                _after = stem[_base_pos + len(base_lc):]
                # Пропускаем номер тома и точку: " 05. " или " 5. "
                _zone_m = re.match(r'[\s\-]*\d{1,4}[\.\s]+(.+)', _after)
                if not _zone_m:
                    continue
                _title_part = _zone_m.group(1).strip()
                # Первый сегмент заголовка (до следующей точки или конца)
                _first_seg = re.split(r'[\.\(]', _title_part)[0].strip()
                _first_seg_lc = _first_seg.lower().replace('ё', 'е')
                # Считаем совпадение если sub начинается с first_seg или наоборот
                if _first_seg_lc and (sub_lc.startswith(_first_seg_lc) or _first_seg_lc.startswith(sub_lc)):
                    subseries_file_count.setdefault(key, set()).add(r.file_path)

            # Схлопываем подсерии где только один файл подтверждён из filename.
            # Папочные источники (folder_dataset, folder_hierarchy) уже авторитетны — не трогаем.
            _FOLDER_SRC = {'folder_dataset', 'folder_hierarchy', 'folder_meta_consensus',
                           'folder_metadata_confirmed', 'filename_named_arc'}
            for r in author_recs:
                s = r.proposed_series or ''
                if '\\' not in s:
                    continue
                if (r.series_source or '') in _FOLDER_SRC:
                    continue  # папка авторитетнее filename-подтверждения
                base = s.split('\\')[0].strip()
                sub  = s.split('\\', 1)[1].strip()
                base_lc = base.lower().replace('ё', 'е')
                sub_lc  = sub.lower().replace('ё', 'е')
                key = (base_lc, sub_lc)
                confirmed = subseries_file_count.get(key, set())
                if len(confirmed) < 2:
                    r.proposed_series = base
                    filename_subseries_fix_count += 1

        self.logger.log(f"[PASS 4] Collapsed {filename_subseries_fix_count} unconfirmed filename subseries")

        # Если у одного автора есть серии "А" и "А. Б" (с точкой), вторая — подсерия первой.
        # Конвертируем "А. Б" → "А\Б" по конвенции backslash.
        # Пример: "Рожденные в СССР" + "Рожденные в СССР. Личности"
        #          → файлы подсерии получают "Рожденные в СССР\Личности"
        print("[PASS 4] Applying hierarchical series unification...")
        hierarchical_unification_count = 0

        # Group by author
        author_groups = {}
        for record in records:
            author = record.proposed_author or "[unknown]"
            if author not in author_groups:
                author_groups[author] = []
            author_groups[author].append(record)

        for author, author_records in author_groups.items():
            # Собираем все уникальные серии автора
            all_series = {r.proposed_series for r in author_records if r.proposed_series and '\\' not in r.proposed_series}

            # Строим множество "чистых" базовых серий (без точки-суффикса)
            base_only = {s for s in all_series if '. ' not in s}

            # Ищем варианты вида "База. Подсерия" где "База" есть в base_only
            for record in author_records:
                series = record.proposed_series or ''
                if '\\' in series or '. ' not in series:
                    continue
                dot_pos = series.find('. ')
                base = series[:dot_pos]
                if base not in base_only:
                    continue
                subseries = series[dot_pos + 2:].strip()
                # Не конвертируем числовые суффиксы ("Серия. 1" → просто "Серия")
                if subseries.isdigit():
                    record.proposed_series = base
                    hierarchical_unification_count += 1
                # Не конвертируем неполные фрагменты: подсерия должна быть самостоятельным
                # значимым словом (≥4 символов) или содержать хотя бы 3 слова.
                # "Назад в" — фрагмент (2 слова, последнее — предлог), не подсерия.
                elif len(subseries) >= 4 and (
                    len(subseries.split()) >= 3 or
                    (len(subseries.split()) >= 1 and len(subseries.split()[-1]) >= 4)
                ):
                    record.proposed_series = f"{base}\\{subseries}"
                    hierarchical_unification_count += 1
                # Иначе оставляем только базовую серию
                else:
                    record.proposed_series = base
                    hierarchical_unification_count += 1

        self.logger.log(f"[PASS 4] Unified {hierarchical_unification_count} hierarchical series variants")

        # Если у автора есть подсерия "X\Y" и запись с proposed_series="X N" (пробел+число),
        # конвертируем "X N" → "X\Y", а series_number устанавливаем в N.
        # Пример: "Фортуна Эрика Минца 1" + подсерия "Фортуна Эрика Минца\Пилот ракетоносца"
        #          → книга 1 получает "Фортуна Эрика Минца\Пилот ракетоносца", series_number="1"
        subseries_num_fix_count = 0
        for author, author_records in author_groups.items():
            # Собираем все подсерии автора: base_lower → set of proposed_series (с backslash)
            subseries_map: dict = {}  # base_lower → set
            for r in author_records:
                s = r.proposed_series or ''
                if '\\' in s:
                    base = s.split('\\')[0].strip().lower().replace('ё', 'е')
                    subseries_map.setdefault(base, set()).add(s)
            if not subseries_map:
                continue
            for record in author_records:
                s = record.proposed_series or ''
                if '\\' in s:
                    continue
                m = re.match(r'^(.+?)\s+(\d{1,4})\s*$', s)
                if not m:
                    continue
                base_lc = m.group(1).strip().lower().replace('ё', 'е')
                num = m.group(2)
                if base_lc not in subseries_map:
                    continue
                candidates = subseries_map[base_lc]
                # Применяем только если подсерия ровно одна — иначе нельзя угадать
                if len(candidates) != 1:
                    continue
                # Убедимся что series_number не противоречит
                if record.series_number and record.series_number != num:
                    continue
                record.proposed_series = next(iter(candidates))
                record.series_number = num
                subseries_num_fix_count += 1

        self.logger.log(f"[PASS 4] Fixed {subseries_num_fix_count} trailing-number series into subseries")

        # Подсерия "X\Y" считается настоящей только если Y встречается у ≥2 книг одного автора.
        # Если Y уникален — это название книги, попавшее в иерархию ошибочно. Сбрасываем в "X".
        singleton_subseries_fix_count = 0
        for author, author_records in author_groups.items():
            # Считаем сколько книг у каждой подсерии (author, full_subseries)
            from collections import Counter
            subseries_counts = Counter(
                r.proposed_series for r in author_records
                if r.proposed_series and '\\' in r.proposed_series
            )
            _FOLD_SRC = {'folder_dataset', 'folder_hierarchy', 'folder_meta_consensus',
                         'folder_metadata_confirmed'}
            for record in author_records:
                s = record.proposed_series or ''
                if '\\' not in s:
                    continue
                if (record.series_source or '') in _FOLD_SRC:
                    continue  # папочная иерархия авторитетна без count-подтверждения
                if subseries_counts[s] >= 2:
                    continue
                # Уникальная подсерия — сбрасываем в базовую серию
                base = s.split('\\')[0].strip()
                record.proposed_series = base
                singleton_subseries_fix_count += 1

        self.logger.log(f"[PASS 4] Collapsed {singleton_subseries_fix_count} singleton subseries to base series")

        # Если proposed_series = "X N" (хвостовое число ≤ 3 цифр, не год) — стрипим N.
        # series_number обновляем только если он пустой (не перебиваем уже выставленный).
        # Ранее проверяли sn == num, но это пропускало случаи когда блок-матчер добавлял
        # номер подсерии в название ("Азиатская сага 2", sn="1"), оставляя мусор в серии.
        trailing_num_strip_count = 0
        # Предварительно строим индекс: (author, series) → множество series_number
        _series_sn_map: dict = {}
        for _r in records:
            if _r.proposed_series and not ('\\' in (_r.proposed_series or '')):
                _k = (_r.proposed_author or '', _r.proposed_series)
                _series_sn_map.setdefault(_k, set()).add((_r.series_number or '').strip())

        for record in records:
            s = record.proposed_series or ''
            if '\\' in s:
                continue
            m = re.match(r'^(.+?)\s+(\d{1,3})\s*$', s)
            if not m:
                continue
            num = m.group(2)
            if int(num) >= 1900:  # год — не трогаем ("Метро 2035" и т.п.)
                continue
            sn = (record.series_number or '').strip()
            # Если series_number уже задан и отличается от num — число часть названия серии,
            # а не номер тома. Пример: "База 24" с series_number=1 → не трогаем.
            if sn and sn != num:
                continue
            # Если другие тома той же серии у того же автора имеют РАЗНЫЕ series_number —
            # число является частью имени серии (arc/season), а не номером тома.
            # Пример: «Хоттабыч 1» с sn=1 НЕ стрипится если тома 2-7 тоже «Хоттабыч 1».
            _all_sn = _series_sn_map.get((_record_author := (record.proposed_author or ''), s), set())
            if len(_all_sn) >= 2:
                continue  # несколько разных sn → «1» — часть названия серии
            record.proposed_series = m.group(1).strip()
            if not sn:
                record.series_number = num
            trailing_num_strip_count += 1

        self.logger.log(f"[PASS 4] Stripped trailing number from {trailing_num_strip_count} series names")

        # Финальный шаг: перечитываем series_number из имени файла по текущему proposed_series.
        # После всей нормализации серия могла измениться (схлопывание подсерий и т.п.),
        # а series_number мог остаться от старого контекста (подсерии или другой серии).
        # Ищем "proposed_series N" в стеме — это позиция в итоговой серии.
        # Применяем только для filename-источников.
        fn_sn_refix_count = 0
        for record in records:
            series = (record.proposed_series or '').split('\\')[0].strip()
            if not series:
                continue
            series_lc = series.lower().replace('ё', 'е')
            stem = Path(record.file_path).stem
            stem_lc = stem.lower().replace('ё', 'е')
            # Серия должна присутствовать в стеме — только тогда стем авторитетен
            pos = stem_lc.find(series_lc)
            if pos < 0:
                continue
            after = stem[pos + len(series):]
            m = re.match(r'[\s\-]*0*(\d{1,4}(?:\s*[-–—]\s*\d{1,4})?)\s*(?:[\.\s]|$)', after)
            if not m:
                # Номера в стеме нет — не перебиваем series_number (остаётся из меты)
                continue
            fn_num = re.sub(r'\s*[-–—]\s*', '-', m.group(1).strip())
            fn_num_lo = int(fn_num.split('-')[0])
            if fn_num_lo >= 1900:
                continue
            _cur_sn = (record.series_number or '').strip()
            if _cur_sn != fn_num:
                # Не перезаписываем дробный sn вида «8.1» (временная подсерия)
                if re.match(r'^\d+\.\d+$', _cur_sn):
                    continue
                record.series_number = fn_num
                fn_sn_refix_count += 1

        self.logger.log(f"[PASS 4] Re-fixed series_number from filename for {fn_sn_refix_count} records")

        # FOLDER_HIERARCHY CLEANUP
        # Fall back to metadata_series if available, otherwise clear.
        print("[PASS 4] Cleaning up folder_hierarchy series with embedded author names...")
        folder_hierarchy_cleanup_count = 0

        for record in records:
            if record.series_source != "folder_hierarchy":
                continue
            if not record.proposed_series:
                continue

            # Build 4-char prefix sets for fuzzy Russian declension matching
            # e.g. "Браст" → author prefix "Брас" matches series word "Браста"[:4] = "Брас"
            author_prefixes = set(
                w[:4].lower() for w in (record.proposed_author or '').split()
                if len(w) >= 4
            )
            series_prefixes = set(
                w[:4].lower() for w in record.proposed_series.split()
                if len(w) >= 4
            )

            # Also check short author words (< 4 chars, >= 2) via exact match in series
            author_short = set(
                w.lower() for w in (record.proposed_author or '').split()
                if 2 <= len(w) < 4
            )
            series_words_lower = set(
                w.lower().rstrip('аяоеуиёью') for w in record.proposed_series.split()
            )

            # Check 1: long-word prefix match (e.g. "Браста" vs "Браст")
            # Check 2: blacklist word in series (e.g. "Loft. ..." → publisher branding)
            # Check 3: short author words appear in series (e.g. "Мо Янь" in "Мо Яня")
            _blacklist = [bl.lower() for bl in (self.settings.get_list('filename_blacklist') if self.settings else [])]
            _series_lower = record.proposed_series.lower()
            _series_word_count = len(record.proposed_series.split())
            import re as _re2
            def _bl_matches(bl, text, multi_word_series=False):
                # Для многословных серий (2+ слов) требуем точного совпадения всей строки.
                # Это предотвращает блокировку реальных серий вроде "Попаданец на рыбалке"
                # из-за того что "попаданец" есть в blacklist как жанровый ярлык.
                if multi_word_series:
                    return bl == text.strip()
                # Short blacklist entries must match as whole words to avoid
                # substring false positives (e.g. "си" inside "цусимские")
                if len(bl) < 4:
                    return bool(_re2.search(r'(?<![а-яёa-z])' + _re2.escape(bl) + r'(?![а-яёa-z])', text, _re2.IGNORECASE))
                return bl in text
            _multi = _series_word_count >= 2
            is_publisher_branding = any(_bl_matches(bl, _series_lower, multi_word_series=_multi) for bl in _blacklist)
            is_author_in_series = bool(author_prefixes & series_prefixes) or bool(author_short & series_words_lower)

            if is_publisher_branding or is_author_in_series:
                # Publisher spotlight folder — not a real series
                # Also reject metadata_series that look like library classifications
                # e.g. "Дик, Филип. Сборники" (Surname, Firstname. Category)
                import re as _re
                _lib_class_pattern = _re.compile(r'^\w[\w\s]+,\s+\w', _re.UNICODE)
                meta = record.metadata_series or ''
                # Дополнительно: не используем metadata как fallback если оно само в blacklist
                _meta_blacklisted = bool(meta) and any(_bl_matches(bl, meta.lower()) for bl in _blacklist)
                if meta and not _lib_class_pattern.match(meta) and not _meta_blacklisted:
                    record.proposed_series = meta
                    record.series_source = "metadata"
                else:
                    record.proposed_series = ""
                    record.series_source = ""
                folder_hierarchy_cleanup_count += 1

        self.logger.log(f"[PASS 4] Cleaned up {folder_hierarchy_cleanup_count} folder_hierarchy series with embedded author names")

        # FILENAME PREFIX AUTHOR CONSENSUS
        # For records with [unknown] or empty author, check if the filename starts with
        # a known author name from another file in the same folder.
        # Use case: "Злой медик (сборник).fb2" has no metadata author, but the same folder
        # contains "Злой медик. Тень медработника (сборник).fb2" which has author="Zлой Медик".
        # The shared filename prefix identifies the author.
        import re as _re_prefix
        _MIXED_SCRIPT_NORM = str.maketrans('ZzАВЕКМНОРСТХ', 'ЗзАВЕКМНОРСТХ')  # Latin→Cyrillic lookalikes

        def _norm_for_prefix(s: str) -> str:
            return _nfc_lower_yo(s).translate(_MIXED_SCRIPT_NORM)

        prefix_fixed_count = 0
        for folder, group_records in groups.items():
            unknown = [r for r in group_records
                       if r.proposed_author in ('[unknown]', '') or not r.proposed_author]
            if not unknown:
                continue
            # Build author → normalised-name map from known files
            known = [(r.proposed_author, _norm_for_prefix(r.proposed_author))
                     for r in group_records
                     if r.proposed_author and r.proposed_author not in ('[unknown]', 'Сборник', 'Соавторство')]
            if not known:
                continue
            for record in unknown:
                stem = Path(record.file_path).stem
                stem_norm = _norm_for_prefix(stem)
                for orig_author, author_norm in known:
                    if not author_norm:
                        continue
                    # Require filename to start with author name followed by a non-letter char
                    if stem_norm.startswith(author_norm):
                        next_char_idx = len(author_norm)
                        if next_char_idx >= len(stem_norm) or not stem_norm[next_char_idx].isalpha():
                            record.proposed_author = orig_author
                            record.author_source = "filename"
                            prefix_fixed_count += 1
                            break

        self.logger.log(f"[PASS 4] Fixed {prefix_fixed_count} [unknown] authors via filename prefix")

        # MULTI-AUTHOR SERIES FOLDER CLEANUP
        # Если тематическая папка "Серия - «X»" содержит книги разных авторов,
        # X — это издательская/жанровая серия, а не авторская.
        # Очищаем proposed_series у тех файлов, где серия совпадает с именем папки.
        import re as _re_ser
        multiauthor_series_cleared = 0
        for folder, group_records in groups.items():
            folder_name_lower = _nfc_lower_yo(folder.name) if folder.name else ''
            if not folder_name_lower or len(folder_name_lower) < 4:
                continue

            # Считаем уникальных «настоящих» авторов (не Соавторство/Сборник/unknown)
            real_authors = {
                r.proposed_author for r in group_records
                if r.proposed_author and r.proposed_author not in (
                    'Соавторство', 'Сборник', '[unknown]', ''
                )
            }
            has_collection_files = any(
                r.proposed_author in ('Соавторство', 'Сборник')
                for r in group_records
            )

            # Многоавторная папка: >1 разных авторов ИЛИ есть Соавторство/Сборник файлы
            # вместе с хотя бы одним другим автором
            is_multiauthor_folder = (
                len(real_authors) > 1 or
                (has_collection_files and len(real_authors) >= 1)
            )
            if not is_multiauthor_folder:
                continue
            # Guard: если все авторы имеют общий токен → авторская папка с соавторами,
            # а не издательская (напр. Верн Жюль + Верн Жюль, Лори Андре → токен 'верн').
            # Очищать серию в этом случае неправильно.
            def _has_common_token(authors):
                token_sets = [
                    {t.lower().replace('ё', 'е')
                     for t in _re_ser.split(r'[\s,;]+', a) if len(t) > 2}
                    for a in authors if a
                ]
                if not token_sets:
                    return False
                common = token_sets[0].copy()
                for ts in token_sets[1:]:
                    common &= ts
                return bool(common)
            if _has_common_token(real_authors):
                continue  # авторы имеют общий токен → не издательская серия

            for record in group_records:
                if not record.proposed_series:
                    continue
                rec_series_norm = _nfc_lower_yo(record.proposed_series)
                # Проверяем что имя серии содержится в имени папки (покрывает "Серия - «X»" формат)
                if rec_series_norm in folder_name_lower:
                    record.proposed_series = ''
                    record.series_source = ''
                    multiauthor_series_cleared += 1

        self.logger.log(
            f"[PASS 4] Cleared {multiauthor_series_cleared} publisher-imprint series "
            f"from multi-author folders"
        )
        print(f"[PASS 4] Cleared {multiauthor_series_cleared} publisher-imprint series from multi-author folders")

        # METADATA RESCUE: после очистки издательских серий восстанавливаем metadata_series
        # если запись осталась без серии, а метаданные содержат корректное название.
        # Типичный случай: "Fanzon. Век магии..." → folder_dataset-серия очищена,
        # но metadata_series = "Изгой" (авторская серия) — используем её.
        meta_rescue_count = 0
        for record in records:
            if record.proposed_series or not record.metadata_series:
                continue
            meta = record.metadata_series.strip()
            if not meta or meta == record.proposed_author:
                continue
            # Не берём если совпадает с именем автора
            if record.proposed_author and _nfc_lower_yo(meta) == _nfc_lower_yo(record.proposed_author):
                continue
            # Фильтруем издательские импринты через blacklist
            _blacklist_words = self.settings.get_list('filename_blacklist') if self.settings else []
            _meta_l = meta.lower()
            _bl_hit = any(
                re.search(r'(?<![а-яёa-z])' + re.escape(bl.lower().strip()) + r'(?![а-яёa-z])', _meta_l)
                for bl in _blacklist_words if bl.strip()
            )
            if _bl_hit:
                continue
            record.proposed_series = meta
            record.series_source = 'metadata'
            meta_rescue_count += 1
        if meta_rescue_count:
            self.logger.log(f"[PASS 4] Rescued {meta_rescue_count} series from metadata after publisher-imprint cleanup")
            print(f"[PASS 4] Rescued {meta_rescue_count} series from metadata after publisher-imprint cleanup")

        # FILENAME SEQUENCE + METADATA DUAL CONFIRMATION (финальный шаг)
        #
        # Исправляем записи с низкодоверительной серией (consensus / author-consensus),
        # если выполняются ОБА условия:
        #   1. В имени ФАЙЛА присутствует база серии, которую подтвердил другой файл
        #      того же автора (filename+meta_confirmed или filename с номером).
        #   2. metadata_series текущего файла совпадает с той же базой серии.
        #
        # Пример: "Переписать сценарий 2.fb2" → series_candidate = "Переписать сценарий"
        #          "Переписать сценарий.fb2"   → в имени есть "переписать сценарий",
        #                                        metadata_series = "Переписать сценарий"
        #          → вывод: серия "Переписать сценарий", source = "filename+meta_confirmed"
        import re as _re_seq
        LOW_CONFIDENCE = {"consensus", "author-consensus", "author-consensus (metadata-confirmed)"}
        STRONG_SERIES_SOURCES = {"filename+meta_confirmed", "filename", "folder_dataset", "metadata"}

        # Группируем по автору
        _auth_groups: dict = {}
        for rec in records:
            key = rec.proposed_author or ""
            _auth_groups.setdefault(key, []).append(rec)

        seq_correction_count = 0
        for author, grp in _auth_groups.items():
            # Собираем подтверждённые серии: normalized_base → original_name
            confirmed_bases: dict = {}
            for rec in grp:
                if rec.series_source in STRONG_SERIES_SOURCES and rec.extracted_series_candidate:
                    base = self._normalize_series_for_consensus(rec.extracted_series_candidate)
                    if base and base not in confirmed_bases:
                        confirmed_bases[base] = rec.proposed_series or rec.extracted_series_candidate

            if not confirmed_bases:
                continue

            # Проверяем записи с ненадёжной серией
            for rec in grp:
                if rec.series_source not in LOW_CONFIDENCE:
                    continue
                meta = (rec.metadata_series or '').strip()
                if not meta:
                    continue
                meta_base = self._normalize_series_for_consensus(meta)

                # Условие 1: metadata_series совпадает с одной из подтверждённых баз
                if meta_base not in confirmed_bases:
                    continue

                # Условие 2: база серии присутствует в имени файла
                stem = _nfc_lower_yo(Path(rec.file_path).stem)
                if _nfc_lower_yo(meta_base) not in stem:
                    continue

                # Оба условия выполнены — исправляем
                correct_series = confirmed_bases[meta_base]
                if rec.proposed_series != correct_series:
                    rec.proposed_series = correct_series
                    rec.series_source = "filename+meta_confirmed"
                    seq_correction_count += 1

        self.logger.log(f"[PASS 4] Filename+meta dual confirmations: {seq_correction_count}")

        # SERIES SUFFIX CORRECTION
        #
        # Парсер может срезать префикс серии через " - " как разделитель.
        # Пример: "Я - Миха 1. Дикарь" → extracted "Миха", но правильно "Я - Миха".
        # Если в группе автора есть файл с подтверждённой серией "Я - Миха",
        # а соседний имеет "Миха" (суффикс), исправим его.
        suffix_fix_count = 0
        for author, grp in _auth_groups.items():
            # Собираем все подтверждённые полные серии
            full_series: list = sorted(
                {
                    rec.proposed_series
                    for rec in grp
                    if rec.proposed_series and rec.series_source in STRONG_SERIES_SOURCES
                },
                key=len, reverse=True  # длинные первыми
            )
            for rec in grp:
                cur = rec.proposed_series or ''
                if not cur or rec.series_source not in ('filename',):
                    continue
                cur_lower = _nfc_lower_yo(cur)
                for full in full_series:
                    full_lower = _nfc_lower_yo(full)
                    if full_lower == cur_lower:
                        break  # уже правильно
                    if full_lower.endswith(cur_lower) and full_lower != cur_lower:
                        # cur — суффикс full → скорее всего обрезан " - Prefix"
                        rec.proposed_series = full
                        rec.series_source = "author-consensus"
                        suffix_fix_count += 1
                        break

        self.logger.log(f"[PASS 4] Series suffix corrections: {suffix_fix_count}")


        #
        # Если в папке большинство файлов имеют одинаковую metadata_series (после
        # стрипания суффиксов вида "(Автор)" и "[Автор]"), используем её для ВСЕХ
        # файлов папки, включая те у которых series_source="filename".
        #
        # Пример: все 11 файлов Гаусса имеют metadata_series "Второй шанс (Максим Гаусс)"
        # / "Второй шанс [Гаусс]" / "Второй шанс" → нормализовано = "второй шанс"
        # → все получают proposed_series = "Второй шанс"
        import re as _re_folder_meta
        def _strip_author_suffix(s: str) -> str:
            """Strip (Author) / [Author] suffixes from series name."""
            s = _re_folder_meta.sub(r'\s*\([^)]*\)\s*$', '', s).strip()
            s = _re_folder_meta.sub(r'\s*\[[^\]]*\]\s*$', '', s).strip()
            return s

        # Группируем по папке
        _folder_groups_meta: dict = {}
        for rec in records:
            if rec.file_path:
                folder = str(Path(rec.file_path).parent)
                _folder_groups_meta.setdefault(folder, []).append(rec)

        folder_meta_correction_count = 0
        for folder, grp in _folder_groups_meta.items():
            # Если в папке уже есть файл с авторитетной серией из иерархии папок —
            # folder_meta_consensus не применяется: имя папки важнее метаданных.
            if any(r.series_source in ("folder_hierarchy", "folder_dataset") for r in grp):
                continue

            # Собираем нормализованные metadata_series
            meta_votes: dict = {}  # normalized_base → (clean_display_name, count)
            for rec in grp:
                raw = (rec.metadata_series or '').strip()
                if not raw:
                    continue
                clean = _strip_author_suffix(raw)
                base = _nfc_lower_yo(self._normalize_series_for_consensus(clean))
                if not base:
                    continue
                if base not in meta_votes:
                    meta_votes[base] = [clean, 0]
                meta_votes[base][1] += 1

            if not meta_votes:
                continue

            # Выбираем лидирующую базу
            best_base, (best_clean, best_count) = max(meta_votes.items(), key=lambda kv: kv[1][1])

            # Применяем только если она встречается у большинства файлов.
            # max(1, ...) вместо max(2, ...) позволяет применять к 2-файловым папкам
            # где лишь 1 файл имеет metadata_series (но inner-проверки всё равно
            # защищают от ложного применения — stem-check и meta-check).
            if best_count < max(1, len(grp) * 0.5):
                continue

            for rec in grp:
                current_base = _nfc_lower_yo(self._normalize_series_for_consensus(
                    rec.proposed_series or ''
                ))
                if current_base == best_base:
                    continue  # уже правильно
                # Применяем только если у файла есть metadata_series совпадающая с базой
                rec_meta_raw = (rec.metadata_series or '').strip()
                if rec_meta_raw:
                    rec_meta_base = _nfc_lower_yo(self._normalize_series_for_consensus(
                        _strip_author_suffix(rec_meta_raw)
                    ))
                    if rec_meta_base != best_base:
                        continue  # метаданные указывают на другую серию
                else:
                    # Нет метаданных — два случая:
                    # 1. proposed_series является подстрокой/суффиксом консенсусной серии
                    #    (парсер обрезал префикс через " - ").
                    # 2. proposed_series пустая, но имя файла содержит консенсусную серию
                    #    (первая книга серии без номера — esc='', proposed='').
                    cur_series = (rec.proposed_series or _nfc_lower_yo(''))
                    best_lower = _nfc_lower_yo(best_clean)
                    if cur_series:
                        if cur_series not in best_lower:
                            continue
                    else:
                        # Пустая серия — применяем только если имя файла содержит серию
                        stem = _nfc_lower_yo(Path(rec.file_path).stem)
                        if best_lower not in stem:
                            continue
                # Не применяем если серия в blacklist (издательская/жанровая метка)
                _bl_cands = [bl.lower() for bl in (self.settings.get_list('filename_blacklist') if self.settings else [])]
                _best_lower_bl = best_clean.lower()
                import re as _re_bl

                def _bl_hit(bl, text):
                    if len(bl) < 4:
                        return bool(_re_bl.search(r'(?<![а-яёa-z])' + _re_bl.escape(bl) + r'(?![а-яёa-z])', text, _re_bl.IGNORECASE))
                    return bl in text

                if any(_bl_hit(bl, _best_lower_bl) for bl in _bl_cands):
                    continue  # издательская серия — не применяем

                # Не перезаписываем если серия уже получена из имени файла —
                # filename имеет приоритет над metadata согласно настройкам.
                # filename_named_arc — именованная дуга, обнаруженная по имени файла,
                # тоже имеет приоритет над folder_meta_consensus.
                if rec.series_source in ('filename', 'filename+meta_confirmed',
                                         'filename_named_arc'):
                    continue

                # Исправляем
                rec.proposed_series = best_clean
                rec.series_source = "folder_meta_consensus"
                folder_meta_correction_count += 1

        self.logger.log(f"[PASS 4] Folder metadata consensus corrections: {folder_meta_correction_count}")

        # FOLDER SERIES PROPAGATION
        # Если хоть один файл в папке получил серию из папки (любой папочный источник),
        # все остальные файлы в той же папке без серии получают ту же серию автоматически.
        _FOLDER_SOURCES = {"folder_hierarchy", "folder_dataset", "folder_meta_consensus"}
        _folder_prop_count = 0
        for folder, grp in _folder_groups_meta.items():
            donor = next((r for r in grp if r.series_source in _FOLDER_SOURCES and r.proposed_series), None)
            if donor is None:
                continue
            for rec in grp:
                if rec is donor:
                    continue
                if rec.proposed_series:
                    continue  # уже есть серия
                rec.proposed_series = donor.proposed_series
                rec.series_source = donor.series_source
                _folder_prop_count += 1
        self.logger.log(f"[PASS 4] Folder series propagation: {_folder_prop_count} records updated")

        # FILENAME PHRASE + METADATA CONFIRMATION
        #
        # Для каждого автора: ищем словосочетания из имён файлов,
        # которые встречаются в 2+ файлах И подтверждены metadata_series
        # хотя бы одного файла. Применяем ко всем подходящим файлам.
        #
        # Пример: "Коруд Ал. Студент в СССР 2", "Коруд Ал. Студент в СССР 3",
        #          "Коруд Ал. Студент в СССР"
        #  → общее словосочетание "студент в ссср" в 3 файлах
        #  → metadata_series у файлов 2 и 3 = "Студент в СССР" (подтверждает)
        #  → все три получают proposed_series = "Студент в СССР"
        import re as _re_ph

        def _strip_author_from_stem(stem: str, author: str) -> str:
            """Убрать имя автора с начала стема. Пробует 'Author. ' и 'Author - '."""
            sl = _nfc_lower_yo(stem)
            for sep in ('. ', ' - '):
                prefix = _nfc_lower_yo(author) + sep
                if sl.startswith(prefix):
                    return stem[len(prefix):]
            # Попробовать reversed "Фамилия Имя" → "Имя Фамилия"
            parts = author.split()
            if len(parts) == 2:
                rev = f"{parts[1]} {parts[0]}"
                for sep in ('. ', ' - '):
                    prefix = _nfc_lower_yo(rev) + sep
                    if sl.startswith(prefix):
                        return stem[len(prefix):]
            return stem

        def _title_phrases(title: str, min_words: int = 2) -> list:
            """Все N-грамы с начала строки (убирая концевой номер)."""
            t = _re_ph.sub(r'\s+\d+(\s*\.\s*.*)?$', '', title).strip()
            words = t.split()
            result = []
            for n in range(len(words), min_words - 1, -1):
                phrase = ' '.join(words[:n])
                if len(phrase) >= 3:
                    result.append(phrase)
            return result

        LOW_CONF = {'consensus', 'author-consensus', 'author-consensus (metadata-confirmed)', ''}
        phrase_fix_count = 0

        _auth_for_phrase: dict = {}
        for rec in records:
            _auth_for_phrase.setdefault(rec.proposed_author or '', []).append(rec)

        for author, auth_recs in _auth_for_phrase.items():
            if not author:
                continue

            # 1. Собираем нормализованные metadata_series → оригинальное имя
            meta_confirmed: dict = {}  # norm_base → clean_name
            for rec in auth_recs:
                raw_meta = (rec.metadata_series or '').strip()
                if not raw_meta:
                    continue
                clean = _strip_author_suffix(raw_meta)
                norm = _nfc_lower_yo(self._normalize_series_for_consensus(clean))
                if norm and norm not in meta_confirmed:
                    meta_confirmed[norm] = clean

            if not meta_confirmed:
                continue

            # 2. Для каждого файла вычисляем title-часть и её фразы
            file_phrases: list = []  # (record, [phrase_norm, ...])
            phrase_count: dict = {}
            for rec in auth_recs:
                stem = Path(rec.file_path).stem
                title = _strip_author_from_stem(stem, author)
                title_norm = _nfc_lower_yo(title)
                phrases = _title_phrases(title_norm)
                file_phrases.append((rec, phrases))
                for ph in set(phrases):
                    phrase_count[ph] = phrase_count.get(ph, 0) + 1

            # 3. Подтверждённые фразы: встречаются в 2+ файлах И есть в meta_confirmed
            confirmed = {
                ph: meta_confirmed[ph]
                for ph, cnt in phrase_count.items()
                if cnt >= 2 and ph in meta_confirmed
            }
            if not confirmed:
                continue

            # 4. Применяем к файлам
            for rec, phrases in file_phrases:
                # Применяем только если серия отсутствует или низкодостоверная
                if rec.proposed_series and rec.series_source not in LOW_CONF:
                    continue
                # Выбираем самую длинную подходящую фразу
                matching = [ph for ph in phrases if ph in confirmed]
                if not matching:
                    continue
                best_ph = max(matching, key=len)
                canonical = confirmed[best_ph]
                if rec.proposed_series != canonical:
                    rec.proposed_series = canonical
                    rec.series_source = "filename_phrase_confirmed"
                    phrase_fix_count += 1

        self.logger.log(f"[PASS 4] Filename phrase+meta confirmations: {phrase_fix_count}")


        # Если у одного автора есть серии "А" и "А. Б" (с точкой), вторая — подсерия первой.
        # Конвертируем "А. Б" → "А\Б" по конвенции backslash.
        # Пример: "Рожденные в СССР" + "Рожденные в СССР. Личности"
        #          → файлы подсерии получают "Рожденные в СССР\Личности"
        hier_count = 0
        _auth_grps2: dict = {}
        for rec in records:
            _auth_grps2.setdefault(rec.proposed_author or '', []).append(rec)

        for auth, auth_recs in _auth_grps2.items():
            all_s = {r.proposed_series for r in auth_recs if r.proposed_series and '\\' not in r.proposed_series}
            base_only = {s for s in all_s if '. ' not in s}
            for record in auth_recs:
                series = record.proposed_series or ''
                if '\\' in series or '. ' not in series:
                    continue
                dot_pos = series.find('. ')
                base = series[:dot_pos]
                if base not in base_only:
                    continue
                subseries = series[dot_pos + 2:].strip()
                if subseries.isdigit():
                    record.proposed_series = base
                else:
                    record.proposed_series = f"{base}\\{subseries}"
                hier_count += 1

        self.logger.log(f"[PASS 4] Hierarchical series conversions (dot→backslash): {hier_count}")

        # Унификация автора по серии с общим соавтором.
        # Если у всех записей одной серии есть хотя бы один общий автор-токен,
        # но proposed_author различается → назначаем автора большинства.
        # Пример: "Ревизор. Возвращение в СССР":
        #   17 записей → "Винтеркей Серж" (folder_dataset)
        #   52 записи  → "Винтеркей Серж, Шумилин Артем" (filename)
        #   Общий токен: "винтеркей" → большинство: "Винтеркей Серж, Шумилин Артем"
        from collections import defaultdict as _dd, Counter as _Cnt
        _series_author_groups: dict = _dd(list)
        for rec in records:
            if rec.proposed_series and rec.proposed_author:
                _series_author_groups[_nfc_lower_yo(rec.proposed_series.strip())].append(rec)

        _author_unified = 0
        for _series_key, _recs in _series_author_groups.items():
            _author_counts = _Cnt(rec.proposed_author.strip() for rec in _recs)
            if len(_author_counts) <= 1:
                continue  # все одинаковые — нечего делать
            # Если в группе есть записи с разными metadata_series — это разные произведения.
            # Унификация автора между ними некорректна даже при общем токене.
            _meta_series_vals = {_nfc_lower_yo(r.metadata_series.strip())
                                 for r in _recs if r.metadata_series and r.metadata_series.strip()}
            if len(_meta_series_vals) > 1:
                continue
            # Токены каждого варианта автора
            def _atokens(a):
                return {t.lower().replace('ё', 'е')
                        for t in re.split(r'[\s,;]+', a) if len(t) > 2}
            _variants = {a: _atokens(a) for a in _author_counts}
            # Ищем хотя бы один общий токен во ВСЕХ вариантах
            _all_token_sets = list(_variants.values())
            _common = _all_token_sets[0].copy()
            for ts in _all_token_sets[1:]:
                _common &= ts
            if not _common:
                continue  # нет общего автора — не трогаем
            def _token_count(a):
                return len([t for t in re.split(r'[\s,;]+', a) if len(t) > 2])
            _token_counts = {_token_count(a) for a in _author_counts}

            if len(_token_counts) > 1:
                # Разное число авторов — проверяем, является ли меньший набор подмножеством большего.
                # Пример: "Барчук Павел" ⊂ "Барчук Павел, Прядеев Евгений" → объединяем к большему.
                # НО только если metadata_series одинакова (иначе разные серии — разные авторства).
                # А_З_К + Берг / Берг: разные metadata_series → guard уже пропустил это выше.
                _max_tokens = max(_token_counts)
                _largest_authors = [a for a in _author_counts if _token_count(a) == _max_tokens]
                if len(_largest_authors) != 1:
                    # Несколько вариантов с одинаковым max — пробуем тайбрейк по папке.
                    # Находим «минимальные» варианты (токены которых ⊆ всех остальных вариантов)
                    # и проверяем, содержится ли один из них в имени папки серии.
                    _min_tokens = min(_token_counts)
                    _minimal_authors = [a for a in _author_counts if _token_count(a) == _min_tokens]
                    # Имя папки серии — parent первой записи
                    _folder_name = _nfc_lower_yo(
                        Path(_recs[0].file_path).parent.name
                    ) if _recs else ''
                    _folder_confirmed = [
                        a for a in _minimal_authors
                        if all(t in _folder_name for t in _atokens(a))
                    ]
                    if len(_folder_confirmed) == 1:
                        _majority_author = _folder_confirmed[0]
                    elif len(_folder_confirmed) > 1:
                        # Несколько кандидатов — проверяем, одна ли это персона (одинаковые токены)?
                        _fc_token_sets = [frozenset(_atokens(a)) for a in _folder_confirmed]
                        if len(set(_fc_token_sets)) == 1:
                            # Одна персона, разный порядок слов → берём самый частый вариант
                            _majority_author = max(_folder_confirmed,
                                                   key=lambda a: _author_counts[a])
                        else:
                            continue  # разные люди → неоднозначно, не трогаем
                    else:
                        continue  # папка не подтвердила ни одного → не трогаем
                else:
                    _largest = _largest_authors[0]
                    _largest_tokens = _atokens(_largest)
                    # Все остальные варианты должны быть подмножеством наибольшего
                    _all_subsets = all(
                        _atokens(a) <= _largest_tokens
                        for a in _author_counts if a != _largest
                    )
                    if not _all_subsets:
                        continue  # есть авторы не входящие в наибольший набор → не трогаем
                    _majority_author = _largest
            else:
                # Все одинаковое число авторов — берём по большинству записей
                _majority_author = _author_counts.most_common(1)[0][0]

            for rec in _recs:
                if rec.proposed_author.strip() != _majority_author:
                    if rec.author_source.startswith('folder_dataset'):
                        continue  # folder_dataset — наивысший приоритет, не перебиваем
                    rec.proposed_author = _majority_author
                    rec.author_source = f"{rec.author_source}+series-consensus"
                    _author_unified += 1

        if _author_unified:
            print(f"[PASS 4] Unified {_author_unified} author values by series+common-author consensus")

        # Апгрейд folder_dataset серий до filename_named_arc подсерий.
        # Когда filename_named_arc даёт «Серия\Подсерия», а folder_dataset —
        # только «Серия» (корень), обновляем folder_dataset записи до полного пути.
        # Это безопасно: корень должен совпадать точно, и только в рамках одного автора.
        _subseries_upgraded = 0
        # Собираем все named_arc подсерии: корень → (полный путь, author_key)
        _arc_subseries: dict = {}  # (author_key, root_norm) → full_subseries
        for rec in records:
            if rec.series_source == 'filename_named_arc' and '\\' in (rec.proposed_series or ''):
                root = rec.proposed_series.split('\\', 1)[0].strip()
                root_norm = _nfc_lower_yo(root)
                author_key = _nfc_lower_yo((rec.proposed_author or '').strip())
                key = (author_key, root_norm)
                # Если несколько подсерий у одного автора с одним корнем — не трогаем
                if key not in _arc_subseries:
                    _arc_subseries[key] = rec.proposed_series
                elif _arc_subseries[key] != rec.proposed_series:
                    _arc_subseries[key] = None  # неоднозначно

        for rec in records:
            if rec.series_source != 'folder_dataset':
                continue
            if not rec.proposed_series or '\\' in rec.proposed_series:
                continue
            # Пропускаем предкомпиляции (sn вида "N-M") — это сборники томов,
            # а не отдельные книги; их серия уже корректна.
            _sn = (rec.series_number or '').strip()
            if re.match(r'^\d+\s*[-–—]\s*\d+$', _sn):
                continue
            root_norm = _nfc_lower_yo(rec.proposed_series.strip())
            author_key = _nfc_lower_yo((rec.proposed_author or '').strip())
            key = (author_key, root_norm)
            target = _arc_subseries.get(key)
            if target and target != rec.proposed_series:
                rec.proposed_series = target
                _subseries_upgraded += 1

        if _subseries_upgraded:
            print(f"[PASS 4] Upgraded {_subseries_upgraded} folder_dataset series to named_arc subseries")

        # Финальная нормализация ё→е во всех proposed_series.
        # Pass3SeriesNormalize делает это до Pass4, но Pass4 может перезаписать
        # proposed_series значениями с ё (через консенсус). Делаем NFC + ё→е здесь,
        # чтобы гарантировать единообразие ПОСЛЕ всех консенсусных операций.
        for record in records:
            if record.proposed_series:
                _s = unicodedata.normalize('NFC', record.proposed_series).replace('\u0451', '\u0435').replace('\u0401', '\u0415')
                if _s != record.proposed_series:
                    record.proposed_series = _s

