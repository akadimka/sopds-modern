#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Synchronization Service - Move and organize FB2 files into library structure.

Handles:
- CSV generation from last_scan_path
- Duplicate detection (author + series + title)
- Folder structure creation (genre/author/series/)
- File movement to library_path
- Database recording
- Empty folder cleanup
- Progress reporting and statistics
"""

import os
import re
import sqlite3
import shutil
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Callable
from collections import defaultdict

try:
    from settings_manager import SettingsManager
    from logger import Logger
    from regen_csv import RegenCSVService
    from fb2_utils import read_fb2_bytes, write_fb2_bytes, fb2_rglob, has_fb2_files as _has_fb2_util
except ImportError:
    from .settings_manager import SettingsManager
    from .logger import Logger
    from .regen_csv import RegenCSVService
    from .fb2_utils import read_fb2_bytes, write_fb2_bytes, fb2_rglob, has_fb2_files as _has_fb2_util


class SynchronizationService:
    """Service for synchronizing FB2 library into organized structure."""
    
    def __init__(self, config_path: str = 'config.json'):
        """Initialize the service.
        
        Args:
            config_path: Path to config.json
        """
        self.config_path = Path(config_path)
        self.settings = SettingsManager(config_path)
        self.logger = Logger()
        self.csv_service = RegenCSVService(config_path)
        
        # Get paths from config
        self.library_path = Path(self.settings.get_library_path())
        _last = self.settings.get_last_scan_path()
        self.last_scan_path = Path(_last) if _last else None
        
        # Database is in project root, not in library
        self.db_path = Path(__file__).parent / '.library_cache.db'
        
        # Log callback for UI integration
        self.log_callback = None
        
        # Statistics tracking
        self.stats = {
            'files_moved': 0,
            'duplicates_found': 0,
            'duplicates_deleted': 0,
            'compilation_deletions': 0,
            'folders_deleted': 0,
            'errors': 0,
            'total_files': 0,
            'start_time': None,
            'end_time': None,
        }
    
    def _log(self, msg: str):
        """Log message using callback or logger.
        
        Args:
            msg: Message to log
        """
        if self.log_callback:
            self.log_callback(msg)
        else:
            self.logger.log(msg)
        
    def sync_database_with_library(self, log_callback: Optional[Callable] = None, 
                                   progress_callback: Optional[Callable] = None) -> Dict:
        """Synchronize database with actual library structure.
        
        Removes entries for files that physically no longer exist in the library.
        Call this at application startup to clean up orphaned database records.
        
        Args:
            log_callback: Function(message_str) for logging messages to UI
            progress_callback: Function(current, total, status_str) for progress updates
            
        Returns:
            Dictionary with statistics {'deleted': count, 'checked': count}
        """
        self.log_callback = log_callback
        
        self._log("=" * 60)
        self._log("СИНХРОНИЗАЦИЯ БД С БИБЛИОТЕКОЙ (удаление orphaned записей)")
        self._log("=" * 60)
        
        stats = {'deleted': 0, 'checked': 0, 'errors': 0}
        
        try:
            if not self.db_path.exists():
                self._log(f"БД не найдена: {self.db_path} - синхронизация не требуется")
                return stats
            
            if not self.library_path.exists():
                self._log(f"Папка библиотеки не найдена: {self.library_path}")
                return stats
            
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            # Read all book entries
            cursor.execute("SELECT id, file_path, author, series, title FROM books")
            rows = cursor.fetchall()
            
            total_rows = len(rows)
            self._log(f"Проверка {total_rows} записей в БД...")
            
            if progress_callback:
                progress_callback(0, total_rows, "Проверка БД...")
            
            deleted_ids = []
            
            for i, row in enumerate(rows):
                record_id, file_path, author, series, title = row
                stats['checked'] += 1
                
                # Progress update
                if progress_callback and i % max(1, total_rows // 10) == 0:
                    progress_callback(i, total_rows, f"Проверка записей БД ({i}/{total_rows})")
                
                # Check if file physically exists
                full_path = self.library_path / file_path
                
                if not full_path.exists():
                    deleted_ids.append(record_id)
                    stats['deleted'] += 1
            
            # Delete orphaned records
            if deleted_ids:
                placeholders = ','.join(['?' for _ in deleted_ids])
                cursor.execute(f"DELETE FROM books WHERE id IN ({placeholders})", deleted_ids)
                
                if progress_callback:
                    progress_callback(len(deleted_ids), len(deleted_ids), 
                                    f"Удаление orphaned записей...")
                
                self._log(f"Удалено orphaned записей: {len(deleted_ids)}")
                conn.commit()
            
            conn.close()
            
            self._log(f"Синхронизация БД завершена: "
                     f"проверено {stats['checked']}, удалено {stats['deleted']}")
            self._log("=" * 60)
            
            if progress_callback:
                progress_callback(100, 100, "БД синхронизирована")
            
        except Exception as e:
            self._log(f"ОШИБКА при синхронизации БД: {str(e)}")
            import traceback
            self._log(f"Stacktrace: {traceback.format_exc()}")
            stats['errors'] += 1
        
        return stats
    
    def synchronize(self, progress_callback: Optional[Callable] = None,
                    log_callback: Optional[Callable] = None,
                    allowed_folders: Optional[set] = None) -> Dict:
        """Execute full synchronization process.
        
        Args:
            progress_callback: Function(current, total, status_str) for progress updates
            log_callback: Function(message_str) for logging messages to UI
            
        Returns:
            Dictionary with statistics
        """
        self.stats['start_time'] = datetime.now()
        self.log_callback = log_callback  # Store for use in other methods

        if not self.last_scan_path:
            self._log("⚠ Папка для сканирования не задана. Выберите папку на главном экране и повторите.")
            return self.stats

        self._log("=" * 60)
        self._log("НАЧАЛО СИНХРОНИЗАЦИИ")
        self._log("=" * 60)
        self._log(f"Library path: {self.library_path}")
        self._log(f"Last scan path: {self.last_scan_path}")
        self._log(f"DB path: {self.db_path}")
        
        try:
            # Step 0: Cleanup orphaned database entries
            if progress_callback:
                progress_callback(1, 100, "Очистка БД от orphaned записей")
            
            self._log("Шаг 0: Очистка БД от orphaned записей")
            
            def db_cleanup_progress(current, total, status):
                """Progress callback for DB cleanup."""
                if progress_callback:
                    progress_callback(1 + (current / max(total, 1) * 3), 100, status)
            
            db_cleanup = self.sync_database_with_library(
                log_callback=log_callback,
                progress_callback=db_cleanup_progress
            )
            self._log(f"  Удалено orphaned записей: {db_cleanup['deleted']}")
            
            # Step 1: Generate CSV
            if progress_callback:
                progress_callback(5, 100, "Генерация CSV из исходной папки")
            
            # Передаём allowed_folders прямо в пайплайн — Precache и Pass1
            # сканируют только выбранные папки, а не всю work_dir.
            _filter = {Path(p).resolve() for p in allowed_folders} if allowed_folders else None
            records = self._generate_csv_data(progress_callback, filter_paths=_filter)
            if _filter:
                self._log(f"Пайплайн ограничен {len(_filter)} папками с жанром")

            self.stats['total_files'] = len(records)

            if not records:
                if progress_callback:
                    progress_callback(10, 100, "Нет файлов для обработки")
                self._log("Синхронизация: нет файлов в разрешённых папках")
                return self.stats
            
            # Step 2: Deduplicate by compilation (compilations win over single volumes)
            if progress_callback:
                progress_callback(13, 100, "Дедупликация: компиляции vs одиночные тома")

            records, compilation_deletions = self._deduplicate_by_compilation(
                records, progress_callback
            )
            if compilation_deletions:
                self._log(f"  Компиляционная дедупликация: удалено {len(compilation_deletions)} одиночных томов")
                self._delete_records_and_files(compilation_deletions, self.last_scan_path)

            # Step 3: Build folder structure and detect duplicates
            if progress_callback:
                progress_callback(15, 100, "Анализ дубликатов")
            
            folder_structure = self._build_folder_structure(records, progress_callback)
            
            # Step 4: Move files and track successfully moved
            if progress_callback:
                progress_callback(50, 100, "Перемещение файлов в библиотеку")

            moved_records = self._move_files(records, folder_structure, progress_callback)

            self._log(f"Всего перемещено: {len(moved_records)} файлов")
            self._log(f"Готово к внесению в БД: {len(moved_records)} записей")

            # Step 5: Update database with moved files
            if progress_callback:
                progress_callback(80, 100, "Обновление базы данных")

            self._update_database(moved_records, progress_callback)

            # Step 6: Cleanup empty folders
            if progress_callback:
                progress_callback(90, 100, "Очистка пустых папок")
            
            self._cleanup_empty_folders()
            
            if progress_callback:
                progress_callback(100, 100, "Синхронизация завершена")
            
            self.stats['end_time'] = datetime.now()
            
            # Log summary statistics
            self._log("")
            self._log("=" * 60)
            self._log("ИТОГОВАЯ СТАТИСТИКА:")
            self._log(f"  Файлов перемещено: {self.stats['files_moved']}")
            self._log(f"  Дубликатов (по БД) найдено и удалено: {self.stats['duplicates_found']}")
            self._log(f"  Одиночных томов удалено по компиляциям: {self.stats.get('compilation_deletions', 0)}")
            self._log(f"  Папок удалено: {self.stats['folders_deleted']}")
            self._log(f"  Ошибок: {self.stats['errors']}")
            self._log("=" * 60)
            
            return self.stats
            
        except Exception as e:
            self._log(f"ОШИБКА при синхронизации: {str(e)}")
            import traceback
            self._log(f"Stacktrace: {traceback.format_exc()}")
            self.stats['errors'] += 1
            self.stats['end_time'] = datetime.now()
            self._log("=" * 60)
            raise
    
    def _generate_csv_data(self, progress_callback: Optional[Callable] = None,
                           filter_paths=None) -> List:
        """Generate CSV data from last_scan_path without saving to file.

        Args:
            progress_callback: Function(current, total, status_str) for progress
            filter_paths: Optional set of absolute Path objects — only these folders
                          are scanned by Precache and Pass1. None = scan everything.

        Returns:
            List of BookRecord objects
        """
        self._log(f"Генерация CSV из: {self.last_scan_path}")
        self._log(f"Путь существует: {self.last_scan_path.exists()}")
        if filter_paths:
            self._log(f"Фильтр папок: {len(filter_paths)} папок")

        try:
            records = self.csv_service.generate_csv(
                str(self.last_scan_path),
                output_csv_path=None,
                progress_callback=progress_callback,
                filter_paths=filter_paths
            )
            
            self._log(f"CSV сгенерирован: {len(records)} записей")
            for i, record in enumerate(records[:5]):  # Log first 5 records
                self._log(f"  [{i+1}] {record.proposed_author} | {record.file_title}")
            if len(records) > 5:
                self._log(f"  ... и ещё {len(records) - 5} записей")
            
            return records
            
        except Exception as e:
            self._log(f"Ошибка при генерации CSV: {str(e)}")
            import traceback
            self._log(f"Stacktrace: {traceback.format_exc()}")
            raise
    
    def _build_folder_structure(
        self,
        records: List,
        progress_callback: Optional[Callable] = None
    ) -> Dict:
        """Build folder structure and detect duplicates.
        
        Args:
            records: List of BookRecord objects
            progress_callback: Progress callback function
            
        Returns:
            Dictionary mapping file_path -> (genre, author, series, subseries)
        """
        self._log("Построение структуры папок")
        
        folder_structure = {}
        duplicates = defaultdict(list)
        
        # Check database for existing entries
        existing_entries = self._get_existing_entries()
        self._log(f"Существующих записей в БД: {len(existing_entries)}")
        
        # Debug: Log first few existing entries
        if existing_entries:
            entries_list = list(existing_entries)[:5]
            for entry in entries_list:
                self._log(f"  БД содержит: {entry[0]} | {entry[1]} | {entry[2]}")
            if len(existing_entries) > 5:
                self._log(f"  ... и ещё {len(existing_entries) - 5} записей")
        else:
            self._log("  (БД пуста или очищена)")
        
        new_files_count = 0
        duplicate_files_count = 0
        
        for i, record in enumerate(records):
            # Progress update
            if progress_callback and i % 10 == 0:
                progress_callback(15 + (i / len(records) * 35), 100, 
                                f"Анализ файла {i+1}/{len(records)}")
            
            # Extract metadata
            genre = record.metadata_genre or "Без жанра"
            author = record.proposed_author or "Неизвестный автор"
            series = record.proposed_series or ""
            title = record.file_title or Path(record.file_path).stem
            
            # Handle genre with multiple entries
            genres = [g.strip() for g in genre.split(',') if g.strip()]
            primary_genre = genres[0] if genres else "Без жанра"
            
            # Detect duplicates
            dup_key = (author, series, title)
            in_db = dup_key in existing_entries
            
            if in_db:
                self._log(f"  [{i+1}] ДУБЛИКАТ: {author} | {series} | {title}")
                self._log(f"       Файл: {record.file_path}")
                duplicates[dup_key].append(record.file_path)
                self.stats['duplicates_found'] += 1
                duplicate_files_count += 1
                
                # Удаляем дубликат из файловой системы
                try:
                    file_to_delete = self.last_scan_path / record.file_path
                    if file_to_delete.exists():
                        os.unlink(str(file_to_delete))
                        self._log(f"       ✓ Удален")
                    else:
                        self._log(f"       ⚠️  Файл не найден для удаления")
                except Exception as e:
                    self._log(f"       ✗ Ошибка при удалении: {str(e)}")
                    self.stats['errors'] += 1
                continue
            
            # New file - add to structure
            new_files_count += 1
            
            # Store subseries info if present (parse from filename)
            subseries = self._extract_subseries(record)
            
            # Build folder path
            folder_structure[record.file_path] = (
                primary_genre,
                author,
                series,
                subseries
            )
            
            # Record as existing for duplicate detection in this batch
            existing_entries.add(dup_key)
        
        self._log(f"")
        self._log(f"РЕЗУЛЬТАТЫ АНАЛИЗА:")
        self._log(f"  Новые файлы: {new_files_count}")
        self._log(f"  Дубликаты: {duplicate_files_count}")
        self._log(f"  Итого файлов в структуре: {len(folder_structure)}")
        
        return folder_structure
    
    def _extract_subseries(self, record) -> str:
        """Extract subseries information from record if present.
        
        Args:
            record: BookRecord object
            
        Returns:
            Subseries string or empty string
        """
        # For now, return empty - can be enhanced to parse from metadata
        return ""

    # ---------------------------------------------------------------------------
    # Compilation-based deduplication
    # ---------------------------------------------------------------------------

    # Ключевые слова, сигнализирующие что файл является компиляцией нескольких томов.
    # Значение — сколько томов охватывает (None = неизвестно).
    _COMPILATION_KEYWORDS: Dict[str, Optional[int]] = {
        'дилогия': 2, 'трилогия': 3, 'тетралогия': 4, 'пенталогия': 5,
        'гексалогия': 6, 'гепталогия': 7, 'окталогия': 8,
        'ноналогия': 9, 'декалогия': 10,
        'дилог': 2, 'трилог': 3, 'тетралог': 4, 'пенталог': 5,
        'гексалог': 6, 'гепталог': 7,
        'omnibus': None, 'omnib': None, 'trilogy': 3, 'tetralogy': 4,
        'сборник': None,
    }

    def _classify_record(self, record) -> Tuple[str, set]:
        """Классифицировать запись как компиляцию или одиночный том.

        Returns:
            ('compilation', covered_volumes) | ('single', {volume_num}) | ('unknown', set())
            covered_volumes — set номеров томов, которые охватывает компиляция.
            Пустой set означает «является компиляцией, но diапазон неизвестен».
        """
        sn = (getattr(record, 'series_number', '') or '').strip()

        # series_number = "1-4" → это явный диапазон компиляции
        range_m = re.match(r'^(\d+)\s*[-–]\s*(\d+)$', sn)
        if range_m:
            lo, hi = int(range_m.group(1)), int(range_m.group(2))
            return 'compilation', set(range(lo, hi + 1))

        stem = Path(record.file_path).stem.lower()
        title_lower = (record.file_title or '').lower()
        combined = stem + ' ' + title_lower

        # Паттерн "(N-M)" в имени файла или заголовке
        paren_range = re.search(r'\(\s*(\d+)\s*[-–]\s*(\d+)\s*\)', combined)
        if paren_range:
            lo, hi = int(paren_range.group(1)), int(paren_range.group(2))
            return 'compilation', set(range(lo, hi + 1))

        # Одиночный том — series_number — целое число (проверяем до эвристик по заголовку,
        # чтобы "Серия. Том N. Название" не ложно классифицировалось как компиляция)
        if sn and re.match(r'^\d+$', sn):
            return 'single', {int(sn)}

        # "Том N" / "том N" в заголовке или имени файла — явный признак одиночного тома
        tom_m = re.search(r'\bтом\s+(\d{1,3})\b', combined, re.IGNORECASE)
        if tom_m:
            return 'single', {int(tom_m.group(1))}

        # Ключевые слова компиляции
        for kw, count in self._COMPILATION_KEYWORDS.items():
            if kw in combined:
                return 'compilation', set(range(1, count + 1)) if count else set()

        # Несколько заголовков перечислены через ". " или ":" в file_title → компиляция
        title = record.file_title or ''
        if len(re.findall(r'\.\s+[А-ЯЁA-Z]', title)) >= 2:
            return 'compilation', set()

        # Пробуем извлечь номер из паттерна "... N. Title" или "... - N. Title"
        num_m = re.search(r'(?:[-–\s])(\d{1,3})\.\s+[А-ЯЁA-Z]', Path(record.file_path).stem)
        if num_m:
            return 'single', {int(num_m.group(1))}

        return 'unknown', set()

    def _deduplicate_by_compilation(
        self,
        records: List,
        progress_callback: Optional[Callable] = None
    ) -> Tuple[List, List]:
        """Дедуплицировать записи: компиляции имеют приоритет над одиночными томами.

        Правило:
        - Компиляция всегда остаётся.
        - Одиночный том удаляется, если его номер охвачен хотя бы одной компиляцией
          той же серии того же автора.
        - Одиночные тома с номерами вне диапазона компиляций — остаются.
        - Файлы без серии и файлы с неизвестным типом — остаются (не трогаем).

        Returns:
            (records_to_keep, records_to_delete)
        """
        self._log("Шаг 1.5: Дедупликация по компиляциям")

        # Группируем только файлы с author + series
        groups: Dict[Tuple[str, str], List] = defaultdict(list)
        no_series: List = []

        for rec in records:
            author = (rec.proposed_author or '').strip()
            series = (rec.proposed_series or '').strip()
            if author and series:
                groups[(author.lower(), series.lower())].append(rec)
            else:
                no_series.append(rec)

        to_keep: List = list(no_series)
        to_delete: List = []
        total_deleted = 0

        for (author, series), group in groups.items():
            classified = [(rec, self._classify_record(rec)) for rec in group]

            compilations = [(rec, vols) for rec, (kind, vols) in classified if kind == 'compilation']
            singles      = [(rec, vols) for rec, (kind, vols) in classified if kind == 'single']
            unknowns     = [rec         for rec, (kind, _)    in classified if kind == 'unknown']

            if not compilations:
                # Нет компиляций — нечего дедублировать
                to_keep.extend(rec for rec, _ in classified)
                continue

            # Строим покрытие — объединение диапазонов всех компиляций
            covered: set = set()
            unknown_range_compilations = 0
            for _, vols in compilations:
                if vols:
                    covered |= vols
                else:
                    unknown_range_compilations += 1

            self._log(f"  {author} / {series}: "
                      f"{len(compilations)} компил., {len(singles)} одиночных, "
                      f"покрытие={sorted(covered) if covered else '?'}")

            # Компиляции и неизвестные — всегда оставляем
            to_keep.extend(rec for rec, _ in compilations)
            to_keep.extend(unknowns)

            for rec, vols in singles:
                if covered and vols and vols.issubset(covered):
                    # Том полностью покрыт компиляцией → удаляем
                    self._log(f"    ✗ УДАЛИТЬ: {Path(rec.file_path).name} (тома {sorted(vols)} ⊆ {sorted(covered)})")
                    to_delete.append(rec)
                    total_deleted += 1
                elif unknown_range_compilations > 0 and not covered:
                    # Есть компиляция с неизвестным диапазоном — безопаснее удалить одиночку
                    self._log(f"    ✗ УДАЛИТЬ (неизв. компиляция): {Path(rec.file_path).name}")
                    to_delete.append(rec)
                    total_deleted += 1
                else:
                    # Том вне покрытия → оставляем
                    self._log(f"    ✓ ОСТАВИТЬ: {Path(rec.file_path).name} (том {sorted(vols)} вне диапазона)")
                    to_keep.append(rec)

        self._log(f"  Итого: оставить {len(to_keep)}, удалить {total_deleted}")
        return to_keep, to_delete

    def _delete_records_and_files(
        self,
        records_to_delete: List,
        scan_path: Path,
    ) -> None:
        """Физически удалить файлы и их записи из БД.

        Args:
            records_to_delete: Записи, файлы которых нужно удалить.
            scan_path: Корневой путь откуда берутся исходные файлы.
        """
        if not records_to_delete:
            return

        deleted_paths = []

        for rec in records_to_delete:
            src = scan_path / rec.file_path
            if src.exists():
                try:
                    src.unlink()
                    self._log(f"  ✓ Удалён файл: {rec.file_path}")
                    deleted_paths.append(rec.file_path)
                    self.stats['duplicates_deleted'] = self.stats.get('duplicates_deleted', 0) + 1
                except Exception as e:
                    self._log(f"  ✗ Не удалось удалить {rec.file_path}: {e}")
                    self.stats['errors'] += 1
            else:
                self._log(f"  ⚠ Файл не найден (уже удалён?): {rec.file_path}")

        # Удаляем соответствующие записи из БД (если там уже есть)
        if deleted_paths and self.db_path.exists():
            try:
                conn = sqlite3.connect(str(self.db_path))
                cursor = conn.cursor()
                # В БД file_path относительный к library_path, но при первой синхронизации
                # файл ещё не в библиотеке — ищем по title+author+series тоже.
                for rec in records_to_delete:
                    cursor.execute(
                        "DELETE FROM books WHERE file_path = ? OR "
                        "(author = ? AND series = ? AND title = ?)",
                        (
                            rec.file_path,
                            rec.proposed_author or '',
                            rec.proposed_series or '',
                            rec.file_title or '',
                        )
                    )
                conn.commit()
                self._log(f"  БД: удалено записей ~ {len(records_to_delete)}")
                conn.close()
            except Exception as e:
                self._log(f"  Ошибка при удалении из БД: {e}")
    
    # Символы, недопустимые в именах файлов Windows
    _UNSAFE_CHARS_RE = re.compile(r'[\\/:*?"<>|]')

    @classmethod
    def _safe(cls, s: str) -> str:
        """Заменить недопустимые символы в имени файла на '_'."""
        return cls._UNSAFE_CHARS_RE.sub('_', s).strip()

    def _build_target_filename(self, record, kind: str, covered_volumes: set) -> str:
        """Build proper target filename based on record metadata.

        Compilations:  "Автор - Серия (Суффикс).fb2"
        Single/unknown with series:
            "Автор - Серия. Название. т. N.fb2"   ← если есть series_number
            "Автор - Серия. Название.fb2"          ← без series_number
        No series:
            "Автор - Название.fb2"
        """
        _safe = self._safe

        # ── Компиляции ────────────────────────────────────────────────
        if kind == 'compilation':
            try:
                from .fb2_compiler import FB2CompilerService
                clean_series = FB2CompilerService._clean_series_name(
                    (record.proposed_series or '').strip()
                )
            except Exception:
                clean_series = (record.proposed_series or '').strip()

            if covered_volumes:
                lo, hi = min(covered_volumes), max(covered_volumes)
                volume_range = str(lo) if lo == hi else f'{lo}-{hi}'
            else:
                lo = hi = 1
                volume_range = ''

            try:
                from .fb2_compiler import FB2CompilerService
                suffix = FB2CompilerService._series_suffix(len(covered_volumes), lo, hi)
            except Exception:
                suffix = f'т. {volume_range}' if volume_range else 'Сборник'

            return f"{_safe(record.proposed_author or '')} - {_safe(clean_series)} ({suffix}).fb2"

        # ── Одиночные тома и неопределённые ──────────────────────────
        author = _safe((record.proposed_author or '').strip())
        series = (record.proposed_series or '').strip()
        raw_title = (record.file_title or Path(record.file_path).stem).strip()

        # series_number: используем только если это число или простой диапазон
        sn = (getattr(record, 'series_number', '') or '').strip()
        has_tome_in_title = bool(re.search(r'[тТ]\.\s*\d', raw_title))
        if sn and re.match(r'^\d+(?:\s*[-–]\s*\d+)?$', sn) and not has_tome_in_title:
            tome = f' т. {sn}'
        else:
            tome = ''

        if series:
            def _norm_cmp(s):
                return s.lower().replace('ё', 'е').strip()

            series_nc = _norm_cmp(series)
            title_nc = _norm_cmp(raw_title)

            if title_nc == series_nc:
                # title полностью совпадает с серией — включать его нет смысла
                return f"{author} - {_safe(series)}{tome}.fb2"

            if title_nc.startswith(series_nc):
                # title начинается с серии — убираем префикс
                raw_title = raw_title[len(series):].lstrip('. \t')

            title = _safe(raw_title)
            if title:
                return f"{author} - {_safe(series)}. {title}{tome}.fb2"
            else:
                return f"{author} - {_safe(series)}{tome}.fb2"
        else:
            title = _safe(raw_title)
            return f"{author} - {title}{tome}.fb2"

    def _move_files(
        self,
        records: List,
        folder_structure: Dict,
        progress_callback: Optional[Callable] = None
    ) -> List:
        """Move files to library structure.
        
        Args:
            records: List of BookRecord objects
            folder_structure: Dictionary with folder mapping
            progress_callback: Progress callback function
            
        Returns:
            List of successfully moved records with updated file_path
        """
        self._log("Начало перемещения файлов")
        self._log(f"Всего записей: {len(records)}, в структуре: {len(folder_structure)}")
        
        # Log first few items in folder_structure
        if folder_structure:
            for file_path in list(folder_structure.keys())[:3]:
                self._log(f"  В структуре: {file_path}")
        
        deleted_duplicates = len(records) - len(folder_structure)
        if deleted_duplicates > 0:
            self._log(f"🗑️  {deleted_duplicates} дубликатов удалены")
        
        moved_records = []
        
        for i, record in enumerate(records):
            # Delete if no structure (duplicate)
            if record.file_path not in folder_structure:
                self._log(f"[{i+1}/{len(records)}] 🗑️  УДАЛЕН: {record.file_path} (дубликат - уже в БД)")
                try:
                    file_to_delete = self.last_scan_path / record.file_path
                    if file_to_delete.exists():
                        os.unlink(str(file_to_delete))
                        self._log(f"       ✓ Успешно удален")
                        self.stats['duplicates_deleted'] = self.stats.get('duplicates_deleted', 0) + 1
                    else:
                        self._log(f"       ⚠️  Файл не найден")
                except Exception as e:
                    self._log(f"       ✗ Ошибка при удалении: {str(e)}")
                    self.stats['errors'] += 1
                continue
            
            self._log(f"[{i+1}/{len(records)}] ◆ Обработка: {record.file_path}")
            
            try:
                genre, author, series, subseries = folder_structure[record.file_path]
                
                # Build target path
                target_dir = self.library_path / genre / author
                if series:
                    target_dir = target_dir / series
                if subseries:
                    target_dir = target_dir / subseries

                # Path traversal guard: ensure target stays inside library_path
                resolved_target = target_dir.resolve()
                resolved_library = self.library_path.resolve()
                if not str(resolved_target).startswith(str(resolved_library)):
                    self._log(f"✖️ Попытка выхода за пределы библиотеки: {target_dir}")
                    self.stats['errors'] += 1
                    continue

                # Create directories
                target_dir.mkdir(parents=True, exist_ok=True)

                # Build source and target file paths
                source_file = self.last_scan_path / record.file_path
                kind, covered = self._classify_record(record)
                target_name = self._build_target_filename(record, kind, covered)
                target_file = target_dir / target_name
                
                # Check if file already exists at target
                if target_file.exists():
                    self._log(f"⚠️  Файл уже существует в библиотеке: {target_file}")
                    self._log(f"    Пропускаем перемещение")
                    self.stats['duplicates_found'] += 1
                    continue
                
                # Move file
                if source_file.exists():
                    self._log(f"  → Перемещение: {source_file.name}")
                    shutil.move(str(source_file), str(target_file))
                    self._log(f"  ✓ Успешно перемещён")
                    self.stats['files_moved'] += 1

                    # Patch FB2 metadata: author, series, book-title.
                    # Автор: перезаписываем только если:
                    #   а) исходных авторов < 3 (коллективные сборники не трогаем)
                    #   б) proposed_author — не коллективный маркер (Соавторство / Сборник / …)
                    # Если proposed_author — коллективный маркер, файл всё равно перемещается
                    # в папку с этим именем, но авторы внутри FB2 остаются оригинальными.
                    # Заголовок: если file_title ошибочен (содержит имя автора) или
                    # отличается от реального — записываем правильный из имени файла.
                    _COLLECTIVE_AUTHORS = {
                        'соавторство', 'сборник', 'антология', 'коллектив авторов',
                        'разные авторы', 'various authors', 'anthology',
                    }
                    _prop_auth_lower = (record.proposed_author or '').strip().lower()
                    _is_collective = _prop_auth_lower in _COLLECTIVE_AUTHORS
                    orig_auth_count = len([
                        a for a in re.split(r'[;,]+', record.metadata_authors or '')
                        if a.strip()
                    ])
                    patch_author = (
                        None if _is_collective or orig_auth_count >= 3
                        else record.proposed_author
                    )
                    patch_title = None
                    derived = self._derive_title_from_filename(record)
                    if derived:
                        if self._is_title_erroneous(record.file_title, record.proposed_author):
                            patch_title = derived
                        elif (record.file_title or '').strip().lower() != derived.lower():
                            patch_title = derived
                    self._patch_fb2_tags(
                        target_file,
                        patch_author,
                        record.proposed_series or "",
                        patch_title,
                    )

                    # Update record with new path (relative to library_path)
                    record.file_path = str(target_file.relative_to(self.library_path))
                    moved_records.append(record)
                    self._log(f"    Новый путь: {record.file_path}")
                else:
                    self._log(f"  ✗ Ошибка: файл не найден: {source_file}")
                    self.stats['errors'] += 1
                    
            except Exception as e:
                self._log(f"ОШИБКА при перемещении {record.file_path}: {str(e)}")
                import traceback
                self._log(f"Stacktrace: {traceback.format_exc()}")
                self.stats['errors'] += 1
        
        self._log(f"Перемещение завершено: {len(moved_records)} файлов переместили")
        return moved_records
    
    def _update_database(
        self,
        records: List,
        progress_callback: Optional[Callable] = None
    ) -> None:
        """Insert records into database.
        
        Args:
            records: List of BookRecord objects (successfully moved files)
            progress_callback: Progress callback function
        """
        self._log(f"Обновление базы данных: {self.db_path}")
        self._log(f"Количество записей для внесения: {len(records)}")
        
        if not records:
            self._log("ВНИМАНИЕ: Нет файлов для внесения в БД")
            self._log("Проверьте, были ли файлы успешно перемещены")
            return
        
        try:
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()

            # Create table if not yet initialised
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS books (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    author TEXT,
                    author_source TEXT,
                    series TEXT,
                    series_source TEXT,
                    series_number TEXT DEFAULT '',
                    subseries TEXT DEFAULT '',
                    title TEXT,
                    file_path TEXT UNIQUE,
                    file_hash TEXT,
                    genre TEXT,
                    added_date TEXT,
                    updated_date TEXT,
                    last_sync_check TEXT
                )
            """)
            # Migrate older DBs that pre-date the series_number column
            try:
                cursor.execute("ALTER TABLE books ADD COLUMN series_number TEXT DEFAULT ''")
            except Exception:
                pass  # Column already exists

            now = datetime.now().isoformat()
            rows_to_insert = []

            for i, record in enumerate(records):
                # Progress update
                if progress_callback and i % 10 == 0:
                    progress_callback(80 + (i / len(records) * 10), 100,
                                    f"Запись в БД: {i+1}/{len(records)}")
                
                try:
                    file_hash = self._calculate_file_hash(
                        self.library_path / record.file_path
                    )
                    rows_to_insert.append((
                        record.proposed_author,
                        record.author_source,
                        record.proposed_series,
                        record.series_source,
                        getattr(record, 'series_number', ''),
                        getattr(record, 'subseries', ''),
                        record.file_title,
                        record.file_path,
                        file_hash,
                        record.metadata_genre,
                        now, now, now,
                    ))
                except Exception as e:
                    self._log(f"ОШИБКА при подготовке записи {record.file_path}: {str(e)}")
                    self.stats['errors'] += 1

            if rows_to_insert:
                cursor.executemany("""
                    INSERT INTO books (
                        author, author_source, series, series_source,
                        series_number, subseries, title, file_path, file_hash, genre,
                        added_date, updated_date, last_sync_check
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, rows_to_insert)

            inserted_count = len(rows_to_insert) - self.stats.get('errors', 0)
            self._log(f"Коммит базы данных... ({inserted_count} записей)")
            conn.commit()
            self._log(f"Коммит завершён успешно")
            self._log(f"Записано в БД: {inserted_count} записей")
            
        except Exception as e:
            self._log(f"ОШИБКА при обновлении БД: {str(e)}")
            import traceback
            self._log(f"Stacktrace: {traceback.format_exc()}")
            self.stats['errors'] += 1
        finally:
            try:
                conn.close()
            except Exception:
                pass
    
    def _cleanup_empty_folders(self) -> None:
        """Remove folders from source directory after FB2 files have been moved.

        Deletes every subdirectory of last_scan_path that contains no FB2 files
        (recursively), regardless of other file types remaining in it.
        The root working directory (last_scan_path) is never deleted.
        """
        self._log(f"Очистка папок в: {self.last_scan_path}")

        try:
            for item in sorted(self.last_scan_path.iterdir()):
                if item.is_dir():
                    self._remove_dir_if_no_fb2(item)
            self._log(f"Удалено папок: {self.stats['folders_deleted']}")
        except Exception as e:
            self._log(f"Ошибка при очистке папок: {str(e)}")

    def _has_fb2_files(self, path: Path) -> bool:
        """Return True if path contains any .fb2 or .fb2.zip files recursively."""
        return _has_fb2_util(path)

    def _remove_dir_if_no_fb2(self, path: Path) -> None:
        """Remove directory tree if it contains no FB2 files.

        Args:
            path: Directory to evaluate and possibly remove
        """
        if not path.is_dir():
            return

        if self._has_fb2_files(path):
            # Still has FB2 files — recurse into subdirectories only
            for item in path.iterdir():
                if item.is_dir():
                    self._remove_dir_if_no_fb2(item)
        else:
            # No FB2 files left — remove the whole subtree
            try:
                shutil.rmtree(path)
                self.stats['folders_deleted'] += 1
                self._log(f"Удалена папка: {path}")
            except Exception as e:
                self._log(f"Не удалось удалить папку {path}: {e}")
    
    def _get_existing_entries(self) -> set:
        """Get existing entries from database.
        
        Returns:
            Set of (author, series, title) tuples
        """
        existing = set()
        
        try:
            if not self.db_path.exists():
                self._log(f"БД не найдена: {self.db_path}")
                return existing
            
            conn = sqlite3.connect(str(self.db_path))
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT author, series, title FROM books
            """)
            
            rows = cursor.fetchall()
            self._log(f"Прочитано из БД: {len(rows)} существующих записей")
            
            for row in rows:
                existing.add(tuple(row))
            
            conn.close()
        except Exception as e:
            self._log(f"Ошибка при чтении БД: {str(e)}")
            import traceback
            self._log(f"Stacktrace: {traceback.format_exc()}")
        
        return existing
    
    def _calculate_file_hash(self, file_path: Path, chunk_size: int = 8192) -> str:
        """Calculate SHA256 hash of file.
        
        Args:
            file_path: Path to file
            chunk_size: Size of chunks to read
            
        Returns:
            Hexadecimal hash string
        """
        try:
            hash_obj = hashlib.sha256()
            
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(chunk_size)
                    if not chunk:
                        break
                    hash_obj.update(chunk)
            
            return hash_obj.hexdigest()
        except Exception as e:
            self.logger.log(f"Ошибка при расчёте хеша {file_path}: {str(e)}")
            return ""
    
    def get_statistics(self) -> Dict:
        """Get current statistics.
        
        Returns:
            Dictionary with statistics
        """
        stats = self.stats.copy()
        
        if stats['start_time'] and stats['end_time']:
            duration = (stats['end_time'] - stats['start_time']).total_seconds()
            stats['duration_seconds'] = duration
            stats['duration_str'] = f"{int(duration)} секунд"
        
        return stats

    # ------------------------------------------------------------------
    # FB2 tag patching
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_title_from_filename(record) -> Optional[str]:
        """Извлечь чистое название книги из имени файла.

        Паттерны:
        - "Автор - Название.fb2" → "Название"
        - "01_Название.fb2" → "Название"
        - "Название.fb2" → "Название"
        """
        import os
        stem = os.path.splitext(os.path.basename(record.file_path))[0]

        # Убираем ведущий числовой префикс: "01_", "1. ", "01 - "
        stem = re.sub(r'^\d{1,4}[\s._\-]+', '', stem).strip()

        # Если есть паттерн "Автор - Название", берём часть после " - "
        if ' - ' in stem:
            parts = stem.split(' - ', 1)
            candidate = parts[1].strip()
            if candidate:
                return candidate

        return stem.strip() if stem.strip() else None

    @staticmethod
    def _is_title_erroneous(file_title: str, proposed_author: str) -> bool:
        """Определить, является ли <book-title> ошибочным (содержит имя автора).

        Сравниваем нормализованные слова: если слова из proposed_author
        покрывают большинство слов title — это не настоящий заголовок.
        """
        if not file_title or not proposed_author:
            return False

        def _norm(s: str) -> set:
            return {w.lower().replace('ё', 'е') for w in re.split(r'\W+', s) if len(w) >= 3}

        title_words = _norm(file_title)
        author_words = _norm(proposed_author)
        if not title_words:
            return False
        overlap = title_words & author_words
        # Если ≥70% слов заголовка совпадают со словами автора — заголовок ошибочный
        return len(overlap) / len(title_words) >= 0.7

    @staticmethod
    def _pretty_print_description(content: str) -> str:
        """Переформатировать секцию <description> с красивыми отступами.

        Безопасно: парсим только <description>…</description>, секции <body>
        не трогаем (там инлайн-разметка, где пробелы важны).
        При любой ошибке парсинга возвращаем исходный content без изменений.
        """
        import xml.dom.minidom as _minidom

        desc_m = re.search(
            r'(<(?:fb:)?description>)(.*?)(</(?:fb:)?description>)',
            content, re.DOTALL | re.IGNORECASE,
        )
        if not desc_m:
            return content

        raw_desc = desc_m.group(0)
        try:
            # Оборачиваем в корневой элемент для парсинга
            dom = _minidom.parseString(f'<?xml version="1.0"?>{raw_desc}'.encode('utf-8'))
            pretty = dom.toprettyxml(indent='  ', encoding=None)
            # Убираем добавленную XML-декларацию и пустые строки
            lines = [
                ln for ln in pretty.splitlines()
                if ln.strip() and not ln.strip().startswith('<?xml')
            ]
            formatted = '\n'.join(lines)
        except Exception:
            return content  # парсинг не удался — оставляем как есть

        return content[:desc_m.start()] + formatted + content[desc_m.end():]

    def _patch_fb2_tags(
        self,
        fb2_path: Path,
        proposed_author: Optional[str],
        proposed_series: str,
        proposed_title: Optional[str] = None,
    ) -> None:
        """Overwrite <author>, <sequence> and <book-title> tags in a FB2 file.

        The file is read and written back with its **original** encoding
        (detected from the XML declaration or trial-decoded).

        Args:
            fb2_path:        Absolute path to the already-moved FB2 file.
            proposed_author: Author string in "Фамилия Имя[, …]" format.
                             ``None`` — leave existing <author> tags untouched.
            proposed_series: Series name to write into <sequence name="…"/>.
                             Empty string — leave existing <sequence> untouched.
            proposed_title:  Book title to write into <book-title>.
                             ``None`` — leave existing <book-title> untouched.
        """
        if proposed_author is None and not proposed_series and proposed_title is None:
            return

        try:
            raw_bytes = read_fb2_bytes(fb2_path)  # прозрачно распаковывает fb2.zip

            # ---- detect encoding ----
            declared_enc = None
            decl_m = re.search(
                rb'<\?xml[^>]*encoding\s*=\s*["\']([^"\']+)["\']',
                raw_bytes, re.IGNORECASE,
            )
            if decl_m:
                try:
                    declared_enc = decl_m.group(1).decode('ascii', errors='ignore')
                except Exception:
                    pass

            enc_candidates = []
            if declared_enc:
                enc_candidates.append(declared_enc)
            enc_candidates.extend(['utf-8-sig', 'utf-8', 'cp1251', 'latin-1'])

            seen_enc: set = set()
            content: Optional[str] = None
            content_encoding = 'utf-8'

            for enc in enc_candidates:
                norm = enc.lower().replace('-', '').replace('_', '')
                if norm in seen_enc:
                    continue
                seen_enc.add(norm)
                try:
                    candidate = raw_bytes.decode(enc, errors='strict')
                except (LookupError, UnicodeDecodeError):
                    continue
                if candidate.lstrip('\ufeff').lstrip().startswith(('<', '<?')):
                    content = candidate
                    content_encoding = enc
                    break

            if content is None:
                self._log(f"  ⚠️  Не удалось определить кодировку: {fb2_path.name}")
                return

            # ---- strip / remember BOM ----
            has_bom = content.startswith('\ufeff')
            if has_bom:
                content = content[1:]

            # ---- locate <title-info> section ----
            ti_m = re.search(
                r'(<(?:fb:)?title-info>)(.*?)(</(?:fb:)?title-info>)',
                content, re.DOTALL,
            )
            if not ti_m:
                self._log(f"  ⚠️  <title-info> не найден в {fb2_path.name}")
                return

            ti_open  = ti_m.group(1)
            ti_body  = ti_m.group(2)
            ti_close = ti_m.group(3)

            # namespace prefix used in this file
            ns = 'fb:' if ti_open.startswith('<fb:') else ''

            # ---- 1. patch author tags ----
            if proposed_author:
                authors = [a.strip() for a in re.split(r'[,;]+', proposed_author) if a.strip()]
                author_xmls = []
                for auth in authors:
                    parts = auth.split()
                    if len(parts) == 1:
                        xml = (
                            f'<{ns}author>'
                            f'<{ns}last-name>{parts[0]}</{ns}last-name>'
                            f'</{ns}author>'
                        )
                    elif len(parts) == 2:
                        xml = (
                            f'<{ns}author>'
                            f'<{ns}last-name>{parts[0]}</{ns}last-name>'
                            f'<{ns}first-name>{parts[1]}</{ns}first-name>'
                            f'</{ns}author>'
                        )
                    else:
                        # Формат всегда "Фамилия Имя" — отчество не пишем в FB2
                        xml = (
                            f'<{ns}author>'
                            f'<{ns}last-name>{parts[0]}</{ns}last-name>'
                            f'<{ns}first-name>{parts[1]}</{ns}first-name>'
                            f'</{ns}author>'
                        )
                    author_xmls.append(xml)

                new_authors_block = '\n    '.join(author_xmls)

                # remove all existing <author> blocks (including whitespace around them)
                ti_body = re.sub(
                    r'\s*<(?:fb:)?author>.*?</(?:fb:)?author>',
                    '', ti_body, flags=re.DOTALL,
                )

                # insert before <book-title> (or prepend if absent)
                bt_m = re.search(r'<(?:fb:)?book-title', ti_body)
                if bt_m:
                    pos = bt_m.start()
                    ti_body = (
                        ti_body[:pos]
                        + '\n    ' + new_authors_block + '\n    '
                        + ti_body[pos:]
                    )
                else:
                    ti_body = '\n    ' + new_authors_block + ti_body

            # ---- 2. patch series tag ----
            if proposed_series:
                seq_m = re.search(r'<sequence\b[^>]*/?\s*>', ti_body, re.IGNORECASE)

                # preserve existing <number> attribute if any
                number_attr = ''
                if seq_m:
                    num_m = re.search(
                        r'number\s*=\s*["\']([^"\']*)["\']',
                        seq_m.group(0), re.IGNORECASE,
                    )
                    if num_m:
                        number_attr = f' number="{num_m.group(1)}"'

                new_seq = f'<sequence name="{proposed_series}"{number_attr}/>'
                if seq_m:
                    ti_body = ti_body[:seq_m.start()] + new_seq + ti_body[seq_m.end():]
                else:
                    ti_body = ti_body.rstrip() + '\n    ' + new_seq + '\n  '

            # ---- 3. patch book-title tag ----
            if proposed_title:
                import html as _html_mod
                safe_title = _html_mod.escape(proposed_title)
                bt_m = re.search(
                    r'<(?:fb:)?book-title>.*?</(?:fb:)?book-title>',
                    ti_body, re.DOTALL,
                )
                new_bt = f'<{ns}book-title>{safe_title}</{ns}book-title>'
                if bt_m:
                    ti_body = ti_body[:bt_m.start()] + new_bt + ti_body[bt_m.end():]
                else:
                    # Вставляем после блока авторов
                    ti_body = ti_body.rstrip() + '\n    ' + new_bt + '\n  '

            # ---- reconstruct content ----
            new_ti = ti_open + ti_body + ti_close
            result = content[:ti_m.start()] + new_ti + content[ti_m.end():]

            # ---- pretty-print <description> block ----
            # Переформатируем только секцию <description>…</description>,
            # не трогая <body> (там инлайн-теги, пробелы значимы).
            result = self._pretty_print_description(result)

            if has_bom:
                result = '\ufeff' + result

            # ---- write back with ORIGINAL encoding ----
            try:
                out_bytes = result.encode(content_encoding, errors='replace')
            except LookupError:
                out_bytes = result.encode('utf-8', errors='replace')

            write_fb2_bytes(fb2_path, out_bytes)  # сохраняет zip-формат если нужно
            self._log(f"  ✓ Теги обновлены ({content_encoding}): {fb2_path.name}")

        except Exception as e:
            import traceback
            self._log(f"  ✗ Ошибка обновления тегов {fb2_path.name}: {e}")
            self._log(traceback.format_exc())
