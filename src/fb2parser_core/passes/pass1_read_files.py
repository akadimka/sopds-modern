"""
PASS 1: Read FB2 files and determine initial authors from folder hierarchy.
"""

import os
import sys
import threading
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import concurrent.futures
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Tuple, Optional
import tqdm

try:
    from extraction_constants import FILE_EXTENSION_FOLDER_NAMES
    from fb2_sax_extractor import FB2SAXExtractor
    from fb2_utils import fb2_rglob
except ImportError:
    from ..extraction_constants import FILE_EXTENSION_FOLDER_NAMES
    from ..fb2_sax_extractor import FB2SAXExtractor
    from ..fb2_utils import fb2_rglob


def process_file_worker(fb2_file_path_str: str, work_dir_str: str,
                       author_folder_cache: Dict, folder_parse_limit: int,
                       settings_dict: Dict, use_cache: bool = True, use_sax_parser: bool = True) -> Optional[Tuple]:
    """
    Module-level worker function for multiprocessing.
    Must be serializable and import all needed dependencies.
    """
    try:
        from pathlib import Path
        from fb2_author_extractor import FB2AuthorExtractor
        from settings_manager import SettingsManager
        from metadata_cache import MetadataCache

        fb2_file = Path(fb2_file_path_str)
        work_dir = Path(work_dir_str)

        # Reconstruct extractor
        if use_sax_parser:
            extractor = FB2SAXExtractor()
        else:
            extractor = FB2AuthorExtractor()
        if hasattr(extractor, 'settings') and settings_dict:
            extractor.settings.settings = settings_dict

        # Try cache first
        cache = MetadataCache() if use_cache else None
        meta = None
        content_hash = ''
        if cache:
            meta, content_hash = cache.get_cached_metadata(fb2_file)

        if not meta:
            # Parse file
            meta = extractor._extract_all_metadata_at_once(fb2_file)
            # Cache the metadata (returns SHA-256 of first 256KB for dedup)
            if cache:
                content_hash = cache.cache_metadata(fb2_file, meta)

        # folder_author_map: {str(parent_folder): (author, source)} — precomputed per unique folder
        folder_key = str(fb2_file.parent)
        if folder_key in author_folder_cache:
            author, author_source = author_folder_cache[folder_key]
        else:
            author, author_source = "", ""

        # Fallback: if folder cache missed, try bottom-up walk matching folder names
        # against this file's metadata_authors (handles hyphen-named folders, reversed
        # word order, folders inside genre-prefix subtrees skipped by Precache).
        meta_series_from_folder = ""
        if not author and meta.get('authors') and meta['authors'] != '[unknown]':
            author_fb, series_fb = _find_author_series_by_metadata(
                fb2_file, work_dir, meta['authors'],
                folder_parse_limit
            )
            if author_fb:
                # Apply author_surname_conversions so folder-name variants like
                # "Стругацкие Аркадий и Борис" map to "Стругацкий Аркадий, Стругацкий Борис"
                _convs = settings_dict.get('author_surname_conversions', {}) if settings_dict else {}
                author_fb = _convs.get(author_fb, author_fb)
                author = author_fb
                author_source = 'folder_dataset'
                meta_series_from_folder = series_fb

        # Create record
        record = BookRecord(
            file_path=str(fb2_file.relative_to(work_dir)),
            file_title=meta['title'] or "[no title]",
            metadata_authors=meta['authors'] or "[unknown]",
            proposed_author=author or "",
            author_source=author_source or "",
            metadata_series=meta['series'] or "",
            series_number=meta.get('series_number', ''),
            proposed_series=meta_series_from_folder,
            series_source='folder_dataset' if meta_series_from_folder else "",
            metadata_genre=meta['genre'] if meta.get('genre') and meta['genre'] != 'None' else "",
            needs_filename_fallback=(author == ""),
            content_hash=content_hash,
        )

        return record.to_tuple()

    except Exception as e:
        print(f"[WORKER ERROR] {fb2_file_path_str}: {e}")
        return None


def _find_author_series_by_metadata(
    fb2_file: Path, work_dir: Path,
    metadata_authors: str, folder_parse_limit: int
) -> Tuple[str, str]:
    """Bottom-up walk: find author folder by matching folder name against metadata_authors.

    Returns (author_name, series_name) where:
      - author_name: normalized author from the matched folder
      - series_name: name of the folder one level closer to the file (= the series)

    Handles hyphen-as-separator (e.g. "Евгеничев-Дмитрий" vs "Дмитрий Евгеничев")
    and reversed word order by comparing sorted word sets.
    """
    if not metadata_authors or metadata_authors == '[unknown]':
        return '', ''

    def _norm_words(s: str):
        return sorted(
            w.strip('.,;').lower().replace('ё', 'е')
            for w in s.replace('-', ' ').split()
            if len(w) > 1
        )

    # Take first author only (before semicolons/commas used for multiple authors)
    first_author = metadata_authors.split(';')[0].split(',')[0].strip()
    if not first_author:
        return '', ''
    meta_words = _norm_words(first_author)
    if not meta_words:
        return '', ''

    current = fb2_file.parent
    child: Optional[Path] = None
    levels = 0

    while current != work_dir and levels < folder_parse_limit:
        if current.name.lower() in FILE_EXTENSION_FOLDER_NAMES:
            child = current
            current = current.parent
            continue
        folder_words = _norm_words(current.name)
        if folder_words == meta_words:
            series_name = child.name if child else ''
            # Normalize folder name: replace hyphen-as-separator so "Фамилия-Имя" → "Фамилия Имя"
            folder_normalized = current.name.replace('-', ' ').strip() if '-' in current.name else current.name
            return folder_normalized, series_name
        child = current
        current = current.parent
        levels += 1

    return '', ''


def _get_author_for_file_worker(fb2_file: Path, work_dir: Path,
                              author_folder_cache: Dict, folder_parse_limit: int) -> Tuple[str, str]:
    """Walk up folder hierarchy to find author from cache.

    Used both for precomputing folder_author_map (one call per unique folder)
    and as fallback if called directly with the serialized cache.
    """
    current_dir = fb2_file.parent
    parse_levels = 0
    last_hit = ""

    while parse_levels < folder_parse_limit:
        if current_dir == work_dir:
            # Also check if work_dir itself is cached as an author folder
            cache_key = str(current_dir)
            if cache_key in author_folder_cache:
                author_name, confidence = author_folder_cache[cache_key]
                last_hit = author_name
            break

        # Skip extension folders
        if current_dir.name.lower() in FILE_EXTENSION_FOLDER_NAMES:
            try:
                parent_dir = current_dir.parent
                if parent_dir == current_dir:
                    break
                current_dir = parent_dir
            except Exception:
                break
            continue

        cache_key = str(current_dir)
        if cache_key in author_folder_cache:
            author_name, confidence = author_folder_cache[cache_key]
            last_hit = author_name

        try:
            parent_dir = current_dir.parent
            if parent_dir == current_dir:
                break
            current_dir = parent_dir
            parse_levels += 1
        except Exception:
            break

    return (last_hit, "folder_dataset") if last_hit else ("", "")


@dataclass
class BookRecord:
    """Book record with progressive filling through PASS stages."""
    file_path: str              # Path to FB2 file (relative to work_dir)
    file_title: str             # Book title from title-info
    metadata_authors: str       # Original authors from FB2 XML (immutable)
    proposed_author: str        # Proposed author (evolves through PASS)
    author_source: str          # Source: "folder_dataset", "filename", "metadata", "consensus", ""
    metadata_series: str        # Original series from FB2 XML (immutable)
    proposed_series: str        # Final series after all PASS
    series_source: str          # Source of series
    metadata_genre: str = ""    # Genres from <genre> tags (comma-separated)
    series_number: str = ""       # Sequence number within series (from <sequence number=.../>)
    extracted_series_candidate: str = ""  # Series found in filename (even if blocked by BL)
    needs_filename_fallback: bool = False  # True if folder parse found nothing, need filename PASS 2
    delete_flag: bool = False     # True if this is an older duplicate superseded by a newer variant
    content_hash: str = ""        # SHA-256 первых 256 КБ содержимого (для поиска дубликатов)

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
            self.metadata_genre,
            self.series_number,
            self.content_hash,
        )

    @classmethod
    def from_tuple(cls, data):
        """Reconstruct from tuple for multiprocessing."""
        return cls(
            file_path=data[0],
            file_title=data[7],  # file_title is at index 7
            metadata_authors=data[1],
            proposed_author=data[2],
            author_source=data[3],
            metadata_series=data[4],
            proposed_series=data[5],
            series_source=data[6],
            metadata_genre=data[8],
            series_number=data[9],
            extracted_series_candidate="",  # defaults
            needs_filename_fallback=(data[2] == ""),  # based on proposed_author
            content_hash=data[10] if len(data) > 10 else "",
        )


class Pass1ReadFiles:
    """PASS 1: Read FB2 files and extract initial metadata."""
    
    def __init__(self, work_dir: Path, author_folder_cache: Dict[Path, Tuple[str, str]],
                 extractor, logger, folder_parse_limit: int,
                 filter_paths=None):
        """Initialize PASS 1.

        Args:
            work_dir: Working directory with FB2 files
            author_folder_cache: Cached author folders from PRECACHE
            extractor: FB2AuthorExtractor instance
            logger: Logger instance
            folder_parse_limit: Maximum depth for folder parsing
            filter_paths: Optional list/set of absolute Path objects — only files
                          inside these folders are processed. None = all files.
        """
        self.work_dir = work_dir
        self.author_folder_cache = author_folder_cache
        self.extractor = extractor
        self.logger = logger
        self.folder_parse_limit = folder_parse_limit
        self.filter_paths = {Path(p).resolve() for p in filter_paths} if filter_paths else None
    
    def execute(self) -> List[BookRecord]:
        """Execute PASS 1: Read FB2 files and create BookRecords.

        Reads files in parallel (I/O-bound) using ThreadPoolExecutor.
        Each file is read exactly once via _extract_all_metadata_at_once().

        Returns:
            List of BookRecord objects
        """
        print("[PASS 1] Reading FB2 files...")

        fb2_files = fb2_rglob(self.work_dir)
        if self.filter_paths:
            fb2_files = [
                f for f in fb2_files
                if any(f.resolve().is_relative_to(fp) for fp in self.filter_paths)
            ]
        total = len(fb2_files)
        if total == 0:
            self.logger.log("[PASS 1] No FB2 files found")
            return []

        print(f"[PASS 1] Found {total} files, processing in parallel...")

        # Serialize cache for hierarchy lookup
        author_folder_cache_serialized = {}
        for path, (author, conf) in self.author_folder_cache.items():
            author_folder_cache_serialized[str(path)] = (author, conf)

        # Precompute author per unique parent folder once — воркеры делают один dict-lookup
        # вместо обхода иерархии для каждого файла.
        unique_folders = {fb2_file.parent for fb2_file in fb2_files}
        folder_author_map: dict = {}  # {str(folder): (author, source)}
        for folder in unique_folders:
            author, source = _get_author_for_file_worker(
                folder / '_dummy',   # файл не используется, нужен только parent
                self.work_dir,
                author_folder_cache_serialized,
                self.folder_parse_limit,
            )
            if author:
                folder_author_map[str(folder)] = (author, source)
        print(f"[PASS 1] Precomputed authors for {len(folder_author_map)} folders "
              f"({len(unique_folders)} unique)")

        settings_dict = getattr(self.extractor.settings, 'settings', {}) if hasattr(self.extractor, 'settings') else {}
        use_cache = settings_dict.get('performance', {}).get('enable_caching', True)
        use_sax_parser = settings_dict.get('performance', {}).get('use_sax_parser', True)

        # Use ProcessPoolExecutor for CPU-bound XML parsing
        max_workers = min(multiprocessing.cpu_count() or 4, max(1, total // 20))
        print(f"[PASS 1] Using {max_workers} processes for CPU-bound XML parsing...")
        if use_cache:
            print(f"[PASS 1] Metadata caching enabled")
        print(f"[PASS 1] Using {'SAX' if use_sax_parser else 'ElementTree'} parser")

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_file = {}
            for fb2_file in fb2_files:
                future = executor.submit(
                    process_file_worker,
                    str(fb2_file),
                    str(self.work_dir),
                    folder_author_map,   # плоская карта: str(parent) → (author, source)
                    self.folder_parse_limit,
                    settings_dict,
                    use_cache,
                    use_sax_parser
                )
                future_to_file[future] = fb2_file

            # Process results with progress bar
            records = []
            with tqdm.tqdm(total=total, desc="Processing FB2 files", unit="file",
                           file=sys.stdout, dynamic_ncols=True) as pbar:
                for future in concurrent.futures.as_completed(future_to_file):
                    fb2_file = future_to_file[future]
                    try:
                        result_tuple = future.result()
                        if result_tuple:
                            record = BookRecord.from_tuple(result_tuple)
                            records.append(record)
                    except Exception as e:
                        self.logger.log(f"[PASS 1] Error processing {fb2_file}: {e}")

                    pbar.update(1)

        self.logger.log(f"[PASS 1] Read {len(records)} files")
        return records
    
    def _get_author_for_file(self, fb2_file: Path) -> Tuple[str, str]:
        """Determine author for a file using folder hierarchy cache.

        Walks UP from the file's folder toward work_dir (up to folder_parse_limit
        steps), collecting ALL cache hits. Returns the hit CLOSEST to work_dir
        (= last found), so a real author folder higher up takes precedence over a
        deeper pseudonym/series folder that also looks like a name.

        Example: "Волк Антон\Макс Лайт\file.fb2"
          - Макс Лайт → cache hit (HIGH)
          - Волк Антон → cache hit (LOW, closer to work_dir)  ← returned

        Returns:
            (author_name, source) where source = "folder_dataset" or ""
        """
        current_dir = fb2_file.parent
        parse_levels = 0
        last_hit: str = ""

        while parse_levels < self.folder_parse_limit:
            if current_dir == self.work_dir:
                break

            # Прозрачно пропускаем папки-расширения (не считаем уровень)
            if current_dir.name.lower() in FILE_EXTENSION_FOLDER_NAMES:
                try:
                    parent_dir = current_dir.parent
                    if parent_dir == current_dir:
                        break
                    current_dir = parent_dir
                except Exception:
                    break
                continue

            if current_dir in self.author_folder_cache:
                author_name, confidence = self.author_folder_cache[current_dir]
                last_hit = author_name  # keep going — higher folder wins

            try:
                parent_dir = current_dir.parent
                if parent_dir == current_dir:
                    break
                current_dir = parent_dir
                parse_levels += 1
            except Exception:
                break

        if last_hit:
            return last_hit, "folder_dataset"
        return "", ""
