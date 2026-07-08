#!/usr/bin/env python3
"""
CSV Regeneration Service - 6-PASS Architecture 2.0 (Modular Edition)

PRECACHE + PASS 1-6 system with each PASS in separate module.

Reference: REGEN_CSV_ARCHITECTURE.md
"""

import csv
import sys
import time
from pathlib import Path

from .settings_manager import SettingsManager
from .logger import Logger
from .fb2_author_extractor import FB2AuthorExtractor

from .precache import Precache
from passes import (
    Pass1ReadFiles,
    Pass2Filename,
    Pass2Fallback,
    Pass3Normalize,
    Pass4Consensus,
    Pass5Conversions,
    Pass6Abbreviations,
)
from .passes.pass2_series_filename import Pass2SeriesFilename
from .passes.pass3_series_normalize import Pass3SeriesNormalize
from .passes.folder_series_parser import parse_series_from_folder_name
from .extraction_constants import FILE_EXTENSION_FOLDER_NAMES, is_no_series_folder
from .pattern_converter import compile_patterns
from .folder_classifier import FolderClassifier, FolderType
import re


class RegenCSVService:
    """Service for CSV regeneration using 6-PASS architecture."""
    
    def __init__(self, config_path: str = 'config.json'):
        """Initialize the service.
        
        Args:
            config_path: Path to config.json
        """
        self.config_path = Path(config_path)
        self.settings = SettingsManager(config_path)
        self.logger = Logger()
        self.extractor = FB2AuthorExtractor(config_path)
        
        # Load configuration lists
        self.collection_keywords = self.settings.get_list('collection_keywords')
        self.service_words = self.settings.get_list('service_words')
        
        # Load folder patterns for series extraction
        folder_patterns_raw = self.settings.get_author_series_patterns_in_folders()
        self.folder_patterns = compile_patterns(folder_patterns_raw) if folder_patterns_raw else []

        # Folder type classifier — uses config BL lists + name dictionaries
        self.folder_classifier = FolderClassifier(self.settings)
        
        # Working directory (where FB2 files are scanned from)
        self.work_dir = Path(self.settings.get_last_scan_path())
        self.folder_parse_limit = self.settings.get_folder_parse_limit()
        
        # Records list
        self.records = []

        # Compiled blacklist patterns — populated once per regenerate() run,
        # capturing any settings changes made before the run.
        self._compiled_blacklist: list = []

        # Author folder cache from PRECACHE
        self.author_folder_cache = {}
        
        # CSV output path - ALWAYS in project directory
        self.project_dir = Path(__file__).parent
        self.output_csv = self.project_dir / "regen.csv"

        # По умолчанию CSV сохраняется; generate_csv(output_csv_path=None) отключает запись
        self._do_save_csv = True
    
    def generate_csv(self, folder_path: str, output_csv_path=None, progress_callback=None,
                     filter_paths=None):
        """
        Generate CSV from FB2 files in folder.
        Wrapper for regenerate() that returns records for GUI compatibility.
        
        Args:
            folder_path: Path to folder with FB2 files
            output_csv_path: Optional path to save CSV file
            progress_callback: Optional callback for progress updates (current, total, status)
            
        Returns:
            List of BookRecord objects
        """
        # Override work directory with the provided folder path
        self.work_dir = Path(folder_path)

        # Override output CSV path if provided
        if output_csv_path:
            self.output_csv = Path(output_csv_path)
            self._do_save_csv = True
        else:
            self._do_save_csv = False

        try:
            # Run regeneration with progress callback
            success = self.regenerate(progress_callback=progress_callback,
                                      filter_paths=filter_paths)
            
            if success:
                return self.records
            else:
                return []
        except Exception as e:
            self.logger.log(f"[ERROR] generate_csv failed: {e}")
            import traceback
            traceback.print_exc()
            raise
    
    def _normalize_name_for_comparison(self, name: str) -> str:
        """Нормализировать имя для сравнения (lowercase, убрать лишние пробелы и пунктуацию).
        
        Заменяет запятые, точки, скобки на пробелы и нормализует пробелы.
        Это позволяет сравнивать "Иван, Петр" с "Иван; Петр" как одинаковые.
        
        Args:
            name: Имя для нормализации
            
        Returns:
            Нормализованное имя
        """
        if not name:
            return ""
        # Заменяем пунктуацию на пробелы
        normalized = re.sub(r'[;,()[\].]', ' ', name)
        # Удаляем лишние пробелы
        normalized = re.sub(r'\s+', ' ', normalized.strip().lower())
        return normalized
    
    def _is_author_folder(self, folder_name: str, proposed_author: str) -> bool:
        """Проверить, является ли папка папкой автора.
        
        Сравнивает нормализованные имена.
        
        Args:
            folder_name: Имя папки
            proposed_author: Предложенное имя автора
            
        Returns:
            True если папка = папка автора
        """
        if not proposed_author or not folder_name:
            return False
        
        folder_normalized = self._normalize_name_for_comparison(folder_name)
        author_normalized = self._normalize_name_for_comparison(proposed_author)
        
        return folder_normalized == author_normalized

    def _surnames_match_folder(self, proposed_author: str, folder_name: str) -> bool:
        """Проверить, является ли папка папкой автора с учётом склонения и формы.

        Обрабатывает:
        - Точное совпадение после нормализации (быстрый путь)
        - Форму множественного числа: "Живовы" совпадает с фамилией "Живов"
          (folder word startswith surname)
        - Несколько авторов: "Живов Геннадий, Живов Георгий" ↔ "Живовы Георгий и Геннадий"
          (все уникальные фамилии должны присутствовать в папке)
        """
        if not proposed_author or not folder_name:
            return False

        # Быстрый путь: точное совпадение после нормализации
        if self._normalize_name_for_comparison(folder_name) == \
                self._normalize_name_for_comparison(proposed_author):
            return True

        # Извлечь уникальные фамилии из proposed_author
        # Формат: "Фамилия Имя" или "Фамилия Имя, Фамилия Имя"
        surnames = []
        for author in re.split(r'[,;]', proposed_author):
            words = author.strip().replace('ё', 'е').split()
            if words:
                surnames.append(words[0].lower())
        unique_surnames = list(dict.fromkeys(surnames))  # дедупликация с сохранением порядка
        if not unique_surnames:
            return False

        folder_words = [re.sub(r'[()]', '', w) for w in re.split(r'[\s,;\-]+', folder_name.lower().replace('ё', 'е')) if w]
        folder_words = [w for w in folder_words if w]

        # Каждая уникальная фамилия должна совпадать хотя бы с одним словом папки.
        # startswith учитывает форму множественного числа (Живов → Живовы)
        for surname in unique_surnames:
            if not any(fw == surname or fw.startswith(surname) for fw in folder_words):
                return False

        return True

    def _folder_is_author_login(self, folder_name: str, proposed_author: str) -> bool:
        """Проверяет, является ли папка логином/псевдонимом автора на латинице.

        Покрывает случай когда имя папки — однословный латинский никнейм/транслит:
        «shellina» для автора «Шеллина Олеся».
        Критерии: 1 слово, все ASCII-буквы, длина ≈ длине одного из слов автора (±3 символа).
        """
        if not folder_name or not proposed_author:
            return False
        # Папка — ровно одно слово из ASCII-букв
        if not re.match(r'^[A-Za-z]+$', folder_name.strip()):
            return False
        fl = len(folder_name.strip())
        if fl < 4:
            return False
        # Сравниваем длину с каждым словом имени автора (кириллица, ≥4 символа)
        for word in re.split(r'[\s,;]+', proposed_author):
            word = word.strip()
            if len(word) >= 4 and not word.isascii() and abs(fl - len(word)) <= 3:
                return True
        return False
    
    def _extract_series_from_folder_name(self, folder_name: str,
                                         preserve_leading_number: bool = False) -> str:
        """
        Извлечь название серии из имени папки, применяя паттерны.

        1. Убирает ведущие номера ("1. ", "2) " и т.д.) — если preserve_leading_number=False
        2. Затем применяет паттерны для извлечения серии из авторов в скобках
        3. Fallback: берёт всё перед скобками

        Args:
            folder_name: Имя папки ("1941 (Иван Байбаков)" или "1. Путь в Царьград")
            preserve_leading_number: если True — не срезаем числовой префикс (папка является
                подсерией в иерархии и порядковый номер несёт смысл для читателя)

        Returns:
            Название серии или исходное имя папки
        """
        # ШАГ 0: убрать ведущие номера ("1. ", "2) " и т.д.)
        # "1. Путь в Царьград" → "Путь в Царьград"
        # "2) Варяг" → "Варяг"
        # НО: "1941 (Иван Байбаков)" оставляем (1941 — часть имени, не порядковый номер)
        # НЕ срезаем когда папка — подсерия в иерархии (preserve_leading_number=True)
        if not preserve_leading_number:
            cleaned = re.sub(r'^\d+[\.\)\-]\s+', '', folder_name).strip()
            if cleaned and cleaned != folder_name:
                folder_name = cleaned
        
        # ШАГ 1: Попробуем применить паттерны и найти группу "series"
        # Исключение: если папка начинается с '(' — скобка в начале является частью названия,
        # а не разделителем "(Серия) Автор". Пропускаем паттерны, берём имя как есть.
        if folder_name.startswith('('):
            return folder_name.strip()
        for pattern_str, pattern_regex, group_names in self.folder_patterns:
            match = pattern_regex.search(folder_name)
            if match:
                # Ищем группу "series" (нормализовано в нижнем регистре)
                if 'series' in group_names:
                    series = match.group('series').strip()
                    if series:
                        # Если паттерн также захватил 'author' группу содержащую цифры —
                        # это контекст нумерации "(Хроники 7-8)", а не имя автора.
                        # Пропускаем паттерн, не стрипаем суффикс.
                        # Если остаток строки после серии содержит цифры — это контекст
                        # нумерации ("Хроники 7-8"), а не имя автора. Пропускаем паттерн.
                        _remaining = folder_name[folder_name.find(series) + len(series):].strip()
                        if any(c.isdigit() for c in _remaining):
                            continue
                        return series
        
        # ШАГ 2: Fallback - простое правило: всё перед скобками это серия
        # "1941 (Иван Байбаков)" → "1941"
        # НО: если скобки содержат цифры ("Хроники 7-8") — это контекст нумерации, не автор
        match = re.match(r'^(.+?)\s*\(([^)]+)\)\s*$', folder_name)
        if match:
            if not any(c.isdigit() for c in match.group(2)):
                return match.group(1).strip()
        
        # ШАГ 3: Если ничего не помогло, берём всё имя
        return folder_name.strip()
    
    def _compile_blacklist_for_run(self) -> list:
        """Скомпилировать blacklist regex-паттерны один раз на прогон.

        Вызывается в начале regenerate() — захватывает актуальные настройки
        на момент запуска, включая все изменения, сделанные пользователем.
        """
        blacklist = self.settings.get_list('filename_blacklist')
        if not blacklist:
            return []
        compiled = []
        for bl_word in blacklist:
            bl_word_lower = bl_word.lower().strip()
            if bl_word_lower:
                pattern = r'(?:^|\W)' + re.escape(bl_word_lower) + r'(?:\W|$)'
                compiled.append(re.compile(pattern))
        return compiled

    def _contains_blacklist_word_regen(self, text: str) -> bool:
        """
        Проверить, содержит ли text слово(а) из blacklist (заимствовано от Pass2).

        Использует pre-compiled паттерны из self._compiled_blacklist.
        Паттерны компилируются один раз в начале regenerate(), а не на каждый вызов.

        Args:
            text: Проверяемый текст (название папки)

        Returns:
            True если найдено хотя бы одно blacklist слово, False иначе
        """
        if not text:
            return False
        text_lower = text.lower()
        for pattern in self._compiled_blacklist:
            if pattern.search(text_lower):
                return True
        return False
    
    def regenerate(self, progress_callback=None, filter_paths=None) -> bool:
        """Execute full CSV regeneration pipeline.
        
        Args:
            progress_callback: Optional callback function(current, total, status) for progress updates
            
        Returns:
            True if successful, False otherwise
        """
        try:
            # Пункт 3: compile blacklist once per run with current settings
            self._compiled_blacklist = self._compile_blacklist_for_run()
            # Загрузить пользовательский список «без серии» один раз на прогон
            self._no_series_names = self.settings.get_no_series_folder_names()
            # Загрузить ключевые слова вариантных папок
            self._variant_kw = [kw.lower() for kw in (self.settings.get_list('variant_folder_keywords') or [])]

            print("\n" + "="*80)
            print("  CSV REGENERATION - 6-PASS SYSTEM (Modular)")
            print(f"  Work folder: {self.work_dir}\n")
            print("="*80 + "\n")

            self.logger.log("=== Starting CSV regeneration ===")
            if progress_callback:
                progress_callback(0, 100, "Инициализация")
            
            # ===== PRECACHE =====
            if progress_callback:
                progress_callback(5, 100, "Кеширование папок авторов")
            _t = time.perf_counter()
            precache = Precache(self.work_dir, self.settings, self.logger,
                               self.folder_parse_limit)
            self.author_folder_cache = precache.execute(filter_paths=filter_paths)
            print(f"[PRECACHE] → {time.perf_counter()-_t:.2f}s")
            self.logger.log("[OK] Author folder hierarchy cached")

            # ===== PASS 1 =====
            if progress_callback:
                progress_callback(10, 100, "Pass 1: Чтение FB2 файлов")
            _t = time.perf_counter()
            pass1 = Pass1ReadFiles(self.work_dir, self.author_folder_cache,
                                  self.extractor, self.logger,
                                  self.folder_parse_limit,
                                  filter_paths=filter_paths)
            self.records = pass1.execute()
            print(f"[PASS 1] → {time.perf_counter()-_t:.2f}s")
            
            if not self.records:
                raise FileNotFoundError(
                    f"Файлы FB2 не найдены в папке:\n{self.work_dir}\n\n"
                    "Убедитесь, что папка содержит FB2-файлы."
                )
            
            self.logger.log(f"[OK] PASS 1: Read {len(self.records)} files")

            # ===== PASS 1.5: Propagate folder_dataset author within each folder =====
            # If at least one file in a folder got author_source="folder_dataset",
            # all other files in the same folder inherit that author.
            from collections import defaultdict
            _folder_groups = defaultdict(list)
            for rec in self.records:
                parent = str(Path(rec.file_path).parent)
                _folder_groups[parent].append(rec)

            propagated = 0
            for parent, group in _folder_groups.items():
                # Find the best folder_dataset author in this group
                dataset_rec = next(
                    (r for r in group if r.author_source == 'folder_dataset' and r.proposed_author),
                    None
                )
                if dataset_rec:
                    for rec in group:
                        if rec is not dataset_rec and rec.proposed_author != dataset_rec.proposed_author:
                            rec.proposed_author = dataset_rec.proposed_author
                            rec.author_source = 'folder_dataset'
                            rec.needs_filename_fallback = False
                            propagated += 1

            if propagated:
                self.logger.log(f"[OK] PASS 1.5: Propagated folder_dataset author to {propagated} files")

            # PASS 1.5 grandparent propagation: subfolders where all filled siblings
            # share a folder_dataset author equal to the grandparent folder name —
            # apply the same author to unfilled sibling-subfolder records.
            from collections import defaultdict as _defdict15
            _gp15: dict = _defdict15(list)
            for rec in self.records:
                _gp15[str(Path(rec.file_path).parent.parent)].append(rec)
            _gp_prop = 0
            for gp_str, grp in _gp15.items():
                gp_name = Path(gp_str).name
                if not gp_name:
                    continue
                _filled15 = [r for r in grp
                             if r.author_source == 'folder_dataset' and r.proposed_author
                             and r.proposed_author.lower() == gp_name.lower()]
                _unfilled15 = [r for r in grp
                               if r.author_source != 'folder_dataset' or not r.proposed_author]
                if _filled15 and _unfilled15:
                    _auth15 = _filled15[0].proposed_author
                    for r in _unfilled15:
                        r.proposed_author = _auth15
                        r.author_source = 'folder_dataset'
                        r.needs_filename_fallback = False
                        _gp_prop += 1
            if _gp_prop:
                self.logger.log(f"[OK] PASS 1.5: Grandparent propagation applied to {_gp_prop} files")

            # ===== TRANSLATOR FOLDERS: сброс folder_dataset автора =====
            # Файлы в папках типа «Переводы Б. Акунина - Г. Чхартишвили» содержат
            # чужие книги — автором является переводчик (из родительской папки), а не
            # сам автор произведения. Сбрасываем folder_dataset, чтобы Pass2
            # извлёк реального автора из имени файла.
            _tfp = [p.lower() for p in
                    (self.settings.settings.get('translator_folder_prefixes', [])
                     if hasattr(self.settings, 'settings') else [])]
            if _tfp:
                _tr_cleared = 0
                for _rec in self.records:
                    if _rec.author_source != 'folder_dataset':
                        continue
                    _parts = Path(_rec.file_path).parts
                    if any(p.lower().startswith(tuple(_tfp)) for p in _parts[:-1]):
                        _rec.proposed_author = ''
                        _rec.author_source = ''
                        _tr_cleared += 1
                if _tr_cleared:
                    print(f"[TRANSLATOR] Cleared folder_dataset author for {_tr_cleared} files in translator folders")

            if progress_callback:
                progress_callback(20, 100, "Pass 2: Извлечение авторов")
            _t = time.perf_counter()
            pass2 = Pass2Filename(self.settings, self.logger, self.work_dir,
                                male_names=precache.male_names,
                                female_names=precache.female_names)
            pass2.prebuild_author_cache(self.records)
            pass2.execute(self.records)
            print(f"[PASS 2] → {time.perf_counter()-_t:.2f}s")
            self.logger.log("[OK] PASS 2: Authors extracted from filenames")

            # ===== PASS 2 Fallback =====
            if progress_callback:
                progress_callback(25, 100, "Pass 2 Fallback: Применение метаданных")
            _t = time.perf_counter()
            pass2_fallback = Pass2Fallback(self.logger, settings=self.settings)
            pass2_fallback.execute(self.records)
            print(f"[PASS 2 Fallback] → {time.perf_counter()-_t:.2f}s")
            self.logger.log("[OK] PASS 2 Fallback: Metadata applied")


            # ===== PASS 2.5: Expand abbreviated/plural author from consistent metadata =====
            _t25 = time.perf_counter()
            # Случай: папка "Войлошниковы", proposed_author="Войлошниковы" (filename),
            # но metadata_authors стабильно содержит полные имена авторов. Расширяем.

            # Перестраиваем группы по папкам после Pass 2
            _folder_groups2: dict = {}
            for rec in self.records:
                parent = str(Path(rec.file_path).parent)
                _folder_groups2.setdefault(parent, []).append(rec)

            def _stem25(s: str) -> str:
                # Two passes to handle compound endings like 'овы' = 'ов'+'ы'
                # "Войлошниковы" → "Войлошников" → "Войлошник"
                s = s.lower().replace('ё', 'е')
                for _ in range(2):
                    s2 = re.sub(r'(?:ова|ева|ов|ев|ин|ина|ий|ая|ый|ых|ы|а|я)$', '', s)
                    if s2 == s:
                        break
                    s = s2
                return s

            def _normalize_meta_author25(name: str) -> str:
                parts = name.strip().split()
                if len(parts) == 2:
                    return f"{parts[-1]} {parts[0]}"
                return name.strip()

            expanded25 = 0
            for parent, group in _folder_groups2.items():
                filename_recs = [
                    r for r in group
                    if r.author_source == 'filename' and r.proposed_author
                    and r.metadata_authors and r.metadata_authors != '[unknown]'
                ]
                if not filename_recs:
                    continue

                # Проверяем стабильность metadata_authors (≥ 60% файлов согласны)
                # Нормализуем: разбиваем на авторов и сортируем, чтобы порядок не важен
                def _meta_key(m):
                    authors = frozenset(a.strip().lower() for a in re.split(r'[;,]+', m) if a.strip())
                    return authors

                meta_counts: dict = {}
                for r in filename_recs:
                    key = _meta_key(r.metadata_authors.strip())
                    meta_counts[key] = meta_counts.get(key, 0) + 1
                dominant_key, dominant_count = max(meta_counts.items(), key=lambda x: x[1])
                if dominant_count / len(filename_recs) < 0.6:
                    continue
                # Берём первый файл с этим ключом как источник canonical metadata
                dominant_meta = next(
                    r.metadata_authors for r in filename_recs
                    if _meta_key(r.metadata_authors.strip()) == dominant_key
                )

                # proposed_author должен быть усечённой формой одного из авторов в meta.
                # ВАЖНО: Pass 2.5 предназначен только для ОДНОСЛОВНЫХ усечённых форм
                # (e.g. "Войлошниковы" → "Войлошников Тим"). Если proposed_author уже
                # содержит 2+ слов — это полное имя, расширение не нужно.
                proposed = filename_recs[0].proposed_author
                if len(proposed.split()) >= 2:
                    continue  # Уже полное имя — пропускаем
                proposed_stem = _stem25(proposed)
                if len(proposed_stem) < 4:
                    continue

                meta_authors_list = [a.strip() for a in re.split(r'[;,]+', dominant_meta) if a.strip()]
                matched = any(
                    # bidirectional: either stem contains the other
                    (proposed_stem in _stem25(part) or _stem25(part) in proposed_stem)
                    for a in meta_authors_list
                    for part in a.split()
                    if len(_stem25(part)) >= 4
                )
                if not matched:
                    continue

                normalized_authors = ', '.join(_normalize_meta_author25(a) for a in meta_authors_list)

                for rec in group:
                    if rec.proposed_author == proposed and rec.author_source in ('filename', ''):
                        rec.proposed_author = normalized_authors
                        rec.author_source = 'metadata'
                        rec.needs_filename_fallback = False
                        expanded25 += 1

            print(f"[PASS 2.5] → {time.perf_counter()-_t25:.2f}s")
            if expanded25:
                self.logger.log(f"[OK] PASS 2.5: Expanded abbreviated authors in {expanded25} files")

            # ===== SERIES EXTRACTION: From Folders (VARIANT B) =====
            if progress_callback:
                progress_callback(30, 100, "Извлечение серий")
            _t = time.perf_counter()
            print("\n[SERIES] Extracting series from folder structure...")

            # Кэш нормализации имён для _surnames_match_folder
            _norm_cache: dict = {}

            def _norm(name: str) -> str:
                if name not in _norm_cache:
                    _norm_cache[name] = self._normalize_name_for_comparison(name)
                return _norm_cache[name]

            # Вспомогательная функция: вычислить (proposed_series, series_source)
            # по частям пути и автору. Результат кэшируется по ключу (author, parent_parts).
            _series_folder_cache: dict = {}  # (author, parent_parts) → (series, source)

            def _compute_folder_series(author: str, parent_parts: tuple) -> tuple:
                """Вернуть (proposed_series, series_source) из структуры папок.

                Логика зависит от типа корневой папки (FolderType):

                AUTHOR:
                    Ищем папку автора в пути → всё что глубже = серия/подсерия.
                    Это основной случай: Волков Тим/Дуэлянт/1. Книга.fb2

                PUBLISHER / COLLECTION:
                    Корневая папка НЕ является серией (это издательский каталог).
                    Если файл лежит в подпапке — подпапка = серия, независимо от автора.
                    Если файл лежит прямо в корневой папке — серии из папки нет.
                    Пример: Серия - «Боевая фантастика»/ИмяСерии/1. Книга.fb2

                UNKNOWN:
                    Пробуем найти автора в пути (как AUTHOR).
                    Если автор не найден, но есть подпапки — берём подпапки как серию.
                    Это покрывает случай, когда корневая папка сама является серией.

                VARIANT / NO_SERIES / SKIP:
                    Серию из папки не извлекаем.
                """
                key = (author, parent_parts)
                if key in _series_folder_cache:
                    return _series_folder_cache[key]

                result = ('', '')

                if not parent_parts:
                    _series_folder_cache[key] = result
                    return result

                root_type = self.folder_classifier.classify(parent_parts[0])

                if root_type in (FolderType.SKIP, FolderType.VARIANT, FolderType.NO_SERIES):
                    # Не используем папку как источник серии
                    pass

                elif root_type in (FolderType.PUBLISHER, FolderType.COLLECTION):
                    # Корневая папка = издательский каталог.
                    # Серия = подпапки начиная с уровня 2 (index 1+).
                    # Исключаем подпапки, которые являются ЧИСТОЙ папкой автора.
                    # Папка формата "Серия (Автор)" НЕ является чистой папкой автора —
                    # из неё нужно извлечь серию через _extract_series_from_folder_name.
                    subfolders = parent_parts[1:]
                    # Загружаем жанрово-издательские метки один раз
                    _gfp = getattr(self, '_genre_folder_prefixes_cache', None)
                    if _gfp is None:
                        _gfp = [p.lower() for p in
                                (self.settings.settings.get('genre_folder_prefixes', [])
                                 if hasattr(self.settings, 'settings') else [])]
                        self._genre_folder_prefixes_cache = _gfp
                    _tfp_ser = getattr(self, '_translator_folder_prefixes_cache', None)
                    if _tfp_ser is None:
                        _tfp_ser = [p.lower() for p in
                                    (self.settings.settings.get('translator_folder_prefixes', [])
                                     if hasattr(self.settings, 'settings') else [])]
                        self._translator_folder_prefixes_cache = _tfp_ser

                    series_folders = []
                    for _sf in subfolders:
                        if not author or not self._surnames_match_folder(author, _sf):
                            # Дополнительная проверка: папка = латинский логин/транслит автора
                            if self._folder_is_author_login(_sf, author):
                                continue  # папка автора, не серия
                            # Дополнительная проверка: папка начинается с жанрово-издательской метки
                            # («Фэнтези МИФ. ...», «Детектив МИФ. ...») — это sub-collection,
                            # а не серия. Серия извлекается из имени файла.
                            _sf_lower = _sf.lower()
                            _is_genre_collection = any(
                                _sf_lower.startswith(_gp) for _gp in _gfp
                            )
                            if _is_genre_collection:
                                continue  # жанровый sub-collection — не серия
                            # Дополнительная проверка: папка-переводчик («Переводы X»)
                            # — содержит чужие книги, не является серией автора.
                            _is_translator_folder = any(
                                _sf_lower.startswith(_tp) for _tp in _tfp_ser
                            )
                            if _is_translator_folder:
                                continue  # папка переводов — не серия
                            series_folders.append(_sf)
                            continue
                        # Даже если автор найден в имени подпапки — жанровая метка
                        # имеет приоритет: «Fanzon. Кинжал и магия. Фэнтези Браста» — это
                        # genre-collection несмотря на имя автора в названии.
                        _sf_lower2 = _sf.lower()
                        if any(_sf_lower2.startswith(_gp) for _gp in _gfp):
                            continue  # жанровый sub-collection с упоминанием автора
                        # Автор найден в имени подпапки.
                        # Пробуем извлечь серию — если она непустая и не совпадает с автором,
                        # это формат "Серия (Автор)", используем её.
                        _extracted = self._extract_series_from_folder_name(_sf)
                        _auth_norm = self._normalize_name_for_comparison(author)
                        _extr_norm = self._normalize_name_for_comparison(_extracted) if _extracted else ''
                        # Если extracted является частью имени автора (или наоборот),
                        # это всё равно папка автора — псевдоним и реальное имя.
                        # Пример: автор «Базилио (Риддер Аристарх)», папка «Риддер Аристарх (Базилио)»
                        # → extracted «Риддер Аристарх», auth_norm «базилио риддер аристарх»
                        # → «риддер аристарх» is substring of auth_norm → чистая папка автора.
                        # Проверяем оба порядка слов: нормализованный («Фамилия Имя»)
                        # и исходный («Имя Фамилия») для западных имён типа «Элин Хильдебранд».
                        _auth_words = set(_auth_norm.split())
                        _extr_words = set(_extr_norm.split())
                        _is_author_variant = (_extr_norm and (
                            _extr_norm in _auth_norm or _auth_norm in _extr_norm
                            or (_auth_words and _auth_words.issubset(_extr_words))
                        ))
                        if _extracted and _extr_norm != _auth_norm and not _is_author_variant:
                            series_folders.append(_sf)
                        # иначе — чистая папка автора, пропускаем
                    # Вариантные папки ("Вариант с СИ", "ЛП" и т.п.) не образуют уровень иерархии.
                    # Файлы внутри них получают серию из ближайшей не-вариантной папки выше.
                    _vkw = getattr(self, '_variant_kw', [])
                    series_folders_clean = []
                    for _sf in series_folders:
                        _sf_lower = _sf.lower().replace('ё', 'е')
                        if any(_vk in _sf_lower for _vk in _vkw):
                            continue  # вариантная папка — пропускаем
                        series_folders_clean.append(_sf)
                    series_folders = tuple(series_folders_clean)
                    if series_folders:
                        if any(is_no_series_folder(f, self._no_series_names) for f in series_folders):
                            result = ('', 'no_series_folder')
                        else:
                            _pln = len(series_folders) >= 2
                            series_names = [self._extract_series_from_folder_name(f, preserve_leading_number=_pln) for f in series_folders]
                            series_combined = '\\'.join(s for s in series_names if s)
                            if series_combined:
                                result = (series_combined, 'folder_dataset')

                else:
                    # AUTHOR или UNKNOWN — ищем папку автора в пути
                    author_folder_index = -1
                    if author:
                        for idx, part in enumerate(parent_parts):
                            if self._surnames_match_folder(author, part):
                                author_folder_index = idx
                                break

                    if author_folder_index >= 0:
                        # Нашли папку автора → всё глубже = серия
                        series_folders = parent_parts[author_folder_index + 1:]
                        if series_folders:
                            if any(is_no_series_folder(f, self._no_series_names) for f in series_folders):
                                result = ('', 'no_series_folder')
                            else:
                                _pln = len(series_folders) >= 2
                                series_names = [self._extract_series_from_folder_name(f, preserve_leading_number=_pln) for f in series_folders]
                                series_combined = '\\'.join(s for s in series_names if s)
                                if series_combined:
                                    result = (series_combined, 'folder_dataset')

                    elif root_type == FolderType.UNKNOWN and len(parent_parts) > 1:
                        # Автор не найден, но есть подпапки в UNKNOWN-папке.
                        # Берём все подпапки (начиная с index 1) как серию.
                        series_folders = parent_parts[1:]
                        if any(is_no_series_folder(f, self._no_series_names) for f in series_folders):
                            result = ('', 'no_series_folder')
                        else:
                            _pln = len(series_folders) >= 2
                            series_names = [self._extract_series_from_folder_name(f, preserve_leading_number=_pln) for f in series_folders]
                            series_combined = '\\'.join(s for s in series_names if s)
                            if series_combined:
                                result = (series_combined, 'folder_dataset')

                _series_folder_cache[key] = result
                return result

            # Предвычисляем части пути один раз
            _parts_cache: dict = {}

            # Источники по возрастанию приоритета. Папка (3) > файл (2) > мета (1).
            # VARIANT B всегда перезаписывает источники с приоритетом ниже папочного.
            _FOLDER_SOURCES = {
                'folder_dataset', 'folder_hierarchy', 'folder_meta_consensus',
                'folder_metadata_confirmed', 'no_series_folder',
            }

            for record in self.records:
                # Пропускаем только если уже установлен папочный источник
                if record.series_source in _FOLDER_SOURCES:
                    continue

                file_path_parts = _parts_cache.get(record.file_path)
                if file_path_parts is None:
                    raw_parts = Path(record.file_path).parts
                    file_path_parts = tuple(
                        p for i, p in enumerate(raw_parts)
                        if i == len(raw_parts) - 1 or p.lower() not in FILE_EXTENSION_FOLDER_NAMES
                    )
                    _parts_cache[record.file_path] = file_path_parts

                parent_parts = file_path_parts[:-1]  # без имени файла
                author = record.proposed_author or ''

                series, source = _compute_folder_series(author, parent_parts)
                if source and series != author:
                    record.proposed_series = series
                    record.series_source = source

                # Если author_folder_index < 0 (папка автора не найдена) —
                # серия из папок не извлекается; Pass 2 Series и metadata возьмут на себя.

            print(f"[SERIES folders] → {time.perf_counter()-_t:.2f}s")
            self.logger.log("[OK] Series extracted from folder structure (Variant B)")
            def _chk(label):
                for r in self.records:
                    if 'Зверь лютый (Бирюк' in r.file_path:
                        print(f"[{label}] {r.file_path[-35:]} | ser_src={r.series_source!r}")
                        break
            _chk("AFTER_VARB")

            # ===== SERIES PASS 2 =====
            if progress_callback:
                progress_callback(40, 100, "Извлечение серий из имен файлов")
            _t = time.perf_counter()
            print("[SERIES] Extracting series from filenames...")
            pass2_series = Pass2SeriesFilename(self.logger,
                                              male_names=precache.male_names,
                                              female_names=precache.female_names)
            pass2_series.execute(self.records)
            print(f"[SERIES PASS 2] → {time.perf_counter()-_t:.2f}s")
            self.logger.log("[OK] Series PASS 2: Extracted from filenames")
            _chk("AFTER_P2S")

            # ===== SERIES PASS 3 =====
            if progress_callback:
                progress_callback(45, 100, "Нормализация серий")
            _t = time.perf_counter()
            print("[SERIES] Normalizing series names...")
            pass3_series = Pass3SeriesNormalize(self.logger, settings=self.settings)
            pass3_series.execute(self.records)
            print(f"[SERIES PASS 3] → {time.perf_counter()-_t:.2f}s")
            self.logger.log("[OK] Series PASS 3: Normalized series names")
            _chk("AFTER_P3S")

            # ===== PASS 3 =====
            if progress_callback:
                progress_callback(55, 100, "Pass 3: Нормализация авторов")
            _t = time.perf_counter()
            pass3 = Pass3Normalize(self.logger, settings=self.settings)
            pass3.execute(self.records)
            print(f"[PASS 3] → {time.perf_counter()-_t:.2f}s")
            self.logger.log("[OK] PASS 3: Authors normalized")

            # ===== PASS 4 =====
            if progress_callback:
                progress_callback(65, 100, "Pass 4: Консенсус")
            _t = time.perf_counter()
            pass4 = Pass4Consensus(self.logger, settings=self.settings)
            pass4.execute(self.records)
            print(f"[PASS 4] → {time.perf_counter()-_t:.2f}s")
            self.logger.log("[OK] PASS 4: Consensus applied")
            _chk("AFTER_P4")

            # ===== PASS 5 =====
            if progress_callback:
                progress_callback(75, 100, "Pass 5: Преобразования")
            _t = time.perf_counter()
            pass5 = Pass5Conversions(self.logger, settings=self.settings)
            pass5.execute(self.records)
            print(f"[PASS 5] → {time.perf_counter()-_t:.2f}s")
            self.logger.log("[OK] PASS 5: Conversions re-applied")

            # ===== PASS 6 =====
            if progress_callback:
                progress_callback(85, 100, "Pass 6: Раскрытие аббревиатур")
            _t = time.perf_counter()
            pass6 = Pass6Abbreviations(self.logger, settings=self.settings)
            pass6.execute(self.records)
            print(f"[PASS 6] → {time.perf_counter()-_t:.2f}s")
            self.logger.log("[OK] PASS 6: Abbreviations expanded")
            
            self._postcheck_series_not_equal_author()
            self._postcheck_metadata_rescue()

            self._postcheck_clear_large_numbers()

            self._postcheck_clear_title_series_fp()
            self._postcheck_strip_leading_number()

            self._postcheck_trim_to_metadata_prefix()
            self._postcheck_expand_truncated_series()

            self._postcheck_strip_service_words()
            self._postcheck_dedup_backslash_hierarchy()
            self._postcheck_dedup_consecutive_words()

            self._postcheck_enrich_folder_hierarchy()
            self._postcheck_filename_prefix_pattern()
            self._postcheck_strip_metadata_coauthors_not_in_filename()
            self._postcheck_series_folder_blacklist()
            self._postcheck_normalize_series_arc_number()
            self._postcheck_clear_author_as_series()
            self._postcheck_build_subfolder_hierarchy()
            self._postcheck_strip_author_prefix_from_series()
            self._postcheck_strip_bracket_annotations_from_series()
            self._postcheck_expand_truncated_series()  # повторно, после strip-префиксов (РОС. Подсерия → РОС\Подсерия)
            self._postcheck_strip_leading_number()  # повторно, после backslash-стрипинга
            self._postcheck_fill_empty_authors()
            self._postcheck_strip_digit_prefix_author()
            self._clear_series_for_compilations()
            self.logger.log("[OK] Series cleared for compilations")

            # ===== Final sanitization: strip folder-illegal chars from all series/authors =====
            # Backslash (\) сохраняем в series — это разделитель иерархии "Серия\Подсерия".
            _ILLEGAL_AUTHOR = re.compile(r'[\\/:*?"<>=|]')
            _ILLEGAL_SERIES = re.compile(r'[/:*?"<>=|]')   # без backslash

            def _replace_colon_in_series(s: str) -> str:
                """Replace ':' with '. ' and capitalize the next word."""
                def _repl(m):
                    rest = m.string[m.end():]
                    # Find next non-space character
                    stripped = rest.lstrip(' ')
                    if stripped:
                        capitalized = stripped[0].upper() + stripped[1:]
                        return '. ' + capitalized[:len(stripped)]
                    return '. '
                # Replace colon + optional spaces with ". " + capitalized next char
                result = re.sub(r':\s*([^\s]?)', lambda m: '. ' + m.group(1).upper() if m.group(1) else '.', s)
                return result

            _abbr_re = re.compile(r'\b[А-ЯЁA-Z][а-яёa-zA-Z]?\.$')

            def _strip_trailing_dot(s: str) -> str:
                """Strip trailing punctuation except a period that belongs to an abbreviation."""
                stripped = s.rstrip('.,…;: \t').rstrip('.')
                # If the original ended with an abbreviated initial (e.g. "Таннер А." or "Бреннан Дж."),
                # restore the trailing period.
                if s.endswith('.') and _abbr_re.search(s):
                    stripped = stripped.rstrip() + '.'
                return stripped

            # Предкомпилируем publisher-prefix паттерны из series_cleanup_patterns
            _cleanup_pats_raw = self.settings.settings.get('series_cleanup_patterns', []) \
                if hasattr(self.settings, 'settings') else []
            _publisher_prefix_pats = [p for p in _cleanup_pats_raw if p.startswith('^')]

            for rec in self.records:
                if rec.proposed_series:
                    # First replace ':' with '. Capitalized'
                    rec.proposed_series = _replace_colon_in_series(rec.proposed_series)
                    # Then strip remaining illegal chars (excluding ':' already handled)
                    rec.proposed_series = re.sub(r'[/*?"<>=|]', '', rec.proposed_series).strip()
                    rec.proposed_series = _strip_trailing_dot(rec.proposed_series)
                    # Strip unbalanced closing brackets from broken metadata (e.g. "Тринадцатый)")
                    _s_open = rec.proposed_series.count('(')
                    _s_close = rec.proposed_series.count(')')
                    if _s_close > _s_open:
                        rec.proposed_series = rec.proposed_series.rstrip(')')
                        rec.proposed_series = rec.proposed_series.rstrip()
                    # Capitalize first letter
                    if rec.proposed_series:
                        rec.proposed_series = rec.proposed_series[0].upper() + rec.proposed_series[1:]
                    # Издательские префиксы МИФ: «Романы МИФ. Серия» → «Серия»
                    # Применяем здесь (финальный шаг) чтобы охватить серии из metadata/Pass4.
                    if rec.proposed_series:
                        for _cpat in _publisher_prefix_pats:
                            _cleaned = re.sub(_cpat, '', rec.proposed_series, flags=re.IGNORECASE).strip()
                            if _cleaned and _cleaned != rec.proposed_series:
                                rec.proposed_series = _cleaned[0].upper() + _cleaned[1:]
                                break
                if rec.proposed_author:
                    rec.proposed_author = _ILLEGAL_AUTHOR.sub('', rec.proposed_author).strip()
                    rec.proposed_author = _strip_trailing_dot(rec.proposed_author)
                    # Strip dot-as-word-separator: "Конторщиков. Виталий" → "Конторщиков Виталий"
                    # Applies to metadata-sourced authors where trailing dot survived reordering.
                    # Rule: word ≥3 chars, ending in lowercase, no internal dots → separator dot.
                    def _strip_dot_sep(s: str) -> str:
                        def _r(m):
                            w = m.group(1)
                            if len(w) >= 3 and w[-1].islower() and '.' not in w:
                                return w + ' ' + m.group(2)
                            return m.group(0)
                        return re.sub(r'(\S+)\. ([А-ЯЁA-Z])', _r, s)
                    rec.proposed_author = _strip_dot_sep(rec.proposed_author)
                    # Balance unmatched opening brackets from broken metadata
                    _open = rec.proposed_author.count('(')
                    _close = rec.proposed_author.count(')')
                    if _open > _close:
                        rec.proposed_author += ')' * (_open - _close)
            self.logger.log("[OK] Final sanitization applied")
            
            # ===== Save CSV =====
            if self._do_save_csv:
                if progress_callback:
                    progress_callback(95, 100, "Сохранение CSV")
                self._save_csv()
                self.logger.log(f"[OK] CSV saved to {self.output_csv}")
            
            print(f"\n[OK] CSV regeneration completed successfully!")
            print(f"   Output: {self.output_csv}")
            print(f"   Records: {len(self.records)}")
            print("="*80 + "\n")
            
            if progress_callback:
                progress_callback(100, 100, "Завершено")
            
            return True
            
        except Exception as e:
            self.logger.log(f"[ERROR] CSV regeneration failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def _postcheck_strip_metadata_coauthors_not_in_filename(self) -> None:
        """Убирает соавторов из metadata-автора если их фамилия не встречается в имени файла.

        Пример: «Краснов 1 Последние дни...» → автор из metadata «Краснов Петр, Криворотов Василий»
        → «Криворотов» не в имени файла → оставляем только «Краснов Петр».
        Применяется только к записям с author_source == 'metadata' и несколькими авторами.
        """
        import re as _re
        _APPLICABLE_SOURCES = {'metadata', 'metadata+series-consensus'}
        _count = 0
        for record in self.records:
            if record.author_source not in _APPLICABLE_SOURCES:
                continue
            if not record.proposed_author or ',' not in record.proposed_author:
                continue
            stem = Path(record.file_path).stem.lower().replace('ё', 'е')
            parts = [a.strip() for a in record.proposed_author.split(',')]
            # Фамилия = первое слово каждого автора (формат «Фамилия Имя»)
            kept = [
                a for a in parts
                if a and a.split()[0].lower().replace('ё', 'е') in stem
            ]
            if not kept or len(kept) == len(parts):
                continue  # все авторы есть в имени файла, или никого нет — не трогаем
            record.proposed_author = ', '.join(kept)
            _count += 1
        if _count:
            print(f"[POST-CHECK] Stripped {_count} metadata co-authors absent from filename")
            self.logger.log(f"[OK] POST-CHECK: Stripped {_count} metadata co-authors not in filename")

    def _postcheck_series_folder_blacklist(self) -> None:
        """Очищает организационные значения серий и обрезает служебные префиксы папок.

        Читает из конфига:
        - series_folder_blacklist: точные значения для полной очистки серии
        - series_folder_prefixes_to_strip: префиксы для обрезки (оставляем остаток)
        """
        blacklist = [s.lower() for s in (self.settings.get_series_folder_blacklist() or [])]
        prefixes = self.settings.get_series_folder_prefixes_to_strip() or []

        if not blacklist and not prefixes:
            return

        cleared = 0
        stripped = 0
        for record in self.records:
            if not record.proposed_series:
                continue
            s = record.proposed_series
            s_lower = s.lower()

            # Точное совпадение с blacklist → очистить серию
            if s_lower in blacklist:
                record.proposed_series = ''
                record.series_source = ''
                cleared += 1
                continue

            # Обрезать служебный префикс папки
            for prefix in prefixes:
                if s.startswith(prefix):
                    remainder = s[len(prefix):]
                    record.proposed_series = remainder
                    s = remainder
                    stripped += 1
                    break

            # Generic: если в серии остался '\', берём только часть после последнего '\'
            if '\\' in record.proposed_series:
                record.proposed_series = record.proposed_series.rsplit('\\', 1)[-1].strip()
                stripped += 1

            # Повторно проверяем blacklist после стрипинга
            if record.proposed_series and record.proposed_series.lower() in blacklist:
                record.proposed_series = ''
                record.series_source = ''
                cleared += 1

        if cleared:
            print(f"[POST-CHECK] Cleared {cleared} organizational folder series (blacklist)")
            self.logger.log(f"[OK] POST-CHECK: Cleared {cleared} organizational series from blacklist")
        if stripped:
            print(f"[POST-CHECK] Stripped folder prefix from {stripped} series values")
            self.logger.log(f"[OK] POST-CHECK: Stripped folder prefix from {stripped} series values")

    def _clear_series_for_compilations(self) -> None:
        """Clear series for compilation/collection records.
        
        If proposed_author contains collection keyword, 
        proposed_series should be empty.
        """
        for record in self.records:
            if not record.proposed_author:
                continue
            
            author_lower = record.proposed_author.lower()
            
            # Check if author contains collection keyword
            if any(kw.lower() in author_lower for kw in self.collection_keywords):
                # Clear the series for compilations
                record.proposed_series = ""
                record.series_source = ""
    
    # -------------------------------------------------------------------------
    # POST-CHECK methods (called from regenerate() after all passes)
    # -------------------------------------------------------------------------

    def _norm_for_series_cmp(self, s: str) -> str:
        """Нормализация для сравнения серии/автора: strip trailing dots, lowercase, убрать кавычки."""
        s = s.rstrip('. ').strip().lower().replace('ё', 'е')
        return re.sub(r'[«»""„"‹›]', '', s)

    def _postcheck_metadata_rescue(self) -> None:
        """Восстанавливает metadata_series для записей без серии после других чеков."""
        _count = 0
        for record in self.records:
            if record.proposed_series or not record.metadata_series:
                continue
            meta = record.metadata_series.strip()
            if not meta:
                continue
            author_norm = self._norm_for_series_cmp(record.proposed_author or '')
            meta_norm = self._norm_for_series_cmp(meta)
            if author_norm and meta_norm == author_norm:
                continue
            _meta_l = meta.lower()
            if any(_bl.search(_meta_l) for _bl in self._compiled_blacklist):
                continue
            record.proposed_series = meta
            record.series_source = 'metadata'
            _count += 1
        if _count:
            print(f"[POST-CHECK] Rescued {_count} series from metadata after series==author cleanup")
            self.logger.log(f"[OK] POST-CHECK: Rescued {_count} series from metadata")

    def _postcheck_fill_empty_authors(self) -> None:
        """Заполняет записи с пустым автором:
        - имя файла содержит (Автор) → извлечь из скобок
        - имя файла начинается с 'Сборник' → 'Сборник'
        - иначе → 'Неизвестный автор'
        """
        _paren_re = re.compile(r'\(([^)]+)\)\s*(?:\.fb2)?$', re.IGNORECASE)
        filled = 0
        for record in self.records:
            if record.proposed_author:
                continue
            filename = Path(record.file_path).stem  # без расширения
            # Попытка извлечь автора из скобок в имени файла
            m = _paren_re.search(filename)
            if m:
                record.proposed_author = m.group(1).strip()
                record.author_source = 'filename_parens'
            elif filename.startswith('Сборник'):
                record.proposed_author = 'Сборник'
                record.author_source = 'fallback'
            else:
                record.proposed_author = 'Неизвестный автор'
                record.author_source = 'fallback'
            filled += 1
        if filled:
            print(f"[POST-CHECK] Filled {filled} empty author records (fallback)")
            self.logger.log(f"[OK] POST-CHECK: Filled {filled} empty authors")

    def _postcheck_strip_digit_prefix_author(self) -> None:
        """Если proposed_author начинается с цифрового префикса (NN. или NN.Слово),
        откидываем префикс и сверяем кандидата с metadata_authors.
        Фиксируем только при подтверждении метой — мета используется для проверки, не замены.
        """
        _digit_re = re.compile(r'^\d+\.?\s*', re.UNICODE)
        _count = 0

        def _words(s: str):
            """Множество слов из строки (lower, без пунктуации)."""
            return set(re.sub(r'[^а-яёa-z]', ' ', s.lower().replace('ё', 'е')).split())

        for record in self.records:
            a = record.proposed_author
            if not a or not _digit_re.match(a):
                continue
            candidate = _digit_re.sub('', a).strip()
            if not candidate:
                continue
            meta = record.metadata_authors or ''
            if not meta:
                continue
            # Проверяем пересечение слов кандидата и меты
            cand_words = _words(candidate)
            meta_words = _words(meta)
            if not cand_words:
                continue
            # Считаем подтверждённым если хотя бы половина слов кандидата есть в мете
            overlap = cand_words & meta_words
            if len(overlap) >= max(1, len(cand_words) // 2):
                record.proposed_author = candidate
                _count += 1

        if _count:
            print(f"[POST-CHECK] Stripped digit prefix from {_count} author values (confirmed by metadata)")
            self.logger.log(f"[OK] POST-CHECK: Stripped digit prefix from {_count} author values")

    def _postcheck_strip_leading_number(self) -> None:
        """Убирает ведущий «N. » числовой префикс из названия серии (артефакт имени файла)."""
        _digit_prefix_re = re.compile(r'^\d+\.\s+', re.UNICODE)
        _count = 0
        for record in self.records:
            if not record.proposed_series:
                continue
            cleaned = _digit_prefix_re.sub('', record.proposed_series)
            if cleaned != record.proposed_series:
                record.proposed_series = record.metadata_series or cleaned
                record.series_source = 'metadata' if record.metadata_series else record.series_source
                _count += 1
        if _count:
            print(f"[POST-CHECK] Stripped digit prefix from {_count} series values")
            self.logger.log(f"[OK] POST-CHECK: Stripped digit prefix from {_count} series")

    def _postcheck_normalize_series_arc_number(self) -> None:
        """Нормализует «Серия N» → «Серия» когда у того же автора есть записи с «Серия».

        Случай: автор назвал первые тома «Словом и делом 1», а следующие — «Словом и делом».
        Все записи с «X N» (X + пробел + одна-две цифры) нормализуются к «X»,
        если (author, X) встречается хотя бы в одной записи того же автора.
        """
        import re as _re
        _TRAIL_DIGIT = _re.compile(r'^(.+?)\s+(\d{1,2})\s*$')

        # Строим множество (author_norm, series_norm) для всех записей без хвостовой цифры
        from series_normalizer import _nfc_lower_yo as _nky
        base_series: set = set()
        for rec in self.records:
            if not rec.proposed_series or '\\' in rec.proposed_series:
                continue
            if _TRAIL_DIGIT.match(rec.proposed_series):
                continue
            a = _nky(rec.proposed_author or '').strip()
            s = _nky(rec.proposed_series).strip()
            if a and s:
                base_series.add((a, s))

        _count = 0
        for rec in self.records:
            if not rec.proposed_series or '\\' in rec.proposed_series:
                continue
            m = _TRAIL_DIGIT.match(rec.proposed_series)
            if not m:
                continue
            base = m.group(1).strip()
            a = _nky(rec.proposed_author or '').strip()
            if (a, _nky(base)) in base_series:
                rec.proposed_series = base
                _count += 1

        if _count:
            print(f"[POST-CHECK] Normalized series+arc-number → base series: {_count} records")
            self.logger.log(f"[OK] POST-CHECK: Normalized series+arc-number in {_count} records")

    def _postcheck_strip_author_prefix_from_series(self) -> None:
        """Стрипит авторский префикс из названия серии.

        Паттерн «И. Фамилия - Серия» или «И. Фамилия Серия» возникает когда
        папка называется «Б. Акунин История Российского государства».
        Стрипим префикс если он совпадает с proposed_author (по фамилии).
        Обрабатывает оба компонента иерархической серии («Корень\\Подсерия»).
        """
        import re as _re
        # Паттерн: «И.» или «И. И.» перед фамилией в начале строки
        _AUTH_PREFIX = _re.compile(
            r'^(?:[А-ЯЁA-Z]\.\s*){1,2}([А-ЯЁA-Z][а-яёa-z]+)[\s\-–—]+',
        )

        def _strip_prefix(s: str, author: str) -> str:
            m = _AUTH_PREFIX.match(s)
            if not m:
                return s
            surname = m.group(1).lower().replace('ё', 'е')
            au_norm = author.lower().replace('ё', 'е')
            if surname in au_norm:
                return s[m.end():].strip()
            return s

        _count = 0
        for rec in self.records:
            if not rec.proposed_series or not rec.proposed_author:
                continue
            ps = rec.proposed_series
            if '\\' in ps:
                root, sub = ps.split('\\', 1)
                new_sub = _strip_prefix(sub.strip(), rec.proposed_author)
                if new_sub != sub.strip():
                    rec.proposed_series = root + '\\' + new_sub
                    _count += 1
            else:
                new_ps = _strip_prefix(ps, rec.proposed_author)
                if new_ps != ps:
                    rec.proposed_series = new_ps
                    _count += 1

        if _count:
            print(f"[POST-CHECK] Stripped author prefix from series in {_count} records")
            self.logger.log(f"[OK] POST-CHECK: Stripped author prefix from series in {_count} records")

    def _postcheck_strip_bracket_annotations_from_series(self) -> None:
        """Стрипит квадратно-скобочные аннотации из названия серии.

        Паттерны: «Дьяволы [Аберкромби]», «Хроники Хьёрварда [= Летописи]»,
        «Бег [Мюллер-Браун]» — все trailing [...] убираются из имени серии.
        Обрабатывает оба компонента иерархической серии.
        """
        import re as _re
        # Стрипим [...] с опциональным закрывающим » после скобки
        _BRACKET = _re.compile(r'\s*\[.*?\]\s*»?\s*$')

        def _strip(s: str) -> str:
            return _BRACKET.sub('', s).strip()

        _count = 0
        for rec in self.records:
            if not rec.proposed_series or '[' not in rec.proposed_series:
                continue
            ps = rec.proposed_series
            if '\\' in ps:
                root, sub = ps.split('\\', 1)
                new_root = _strip(root)
                new_sub = _strip(sub)
                new_ps = (new_root + '\\' + new_sub) if new_sub else new_root
            else:
                new_ps = _strip(ps)
            if new_ps != ps:
                rec.proposed_series = new_ps
                _count += 1

        if _count:
            print(f"[POST-CHECK] Stripped bracket annotations from series in {_count} records")
            self.logger.log(f"[OK] POST-CHECK: Stripped bracket annotations in {_count} records")

    def _postcheck_clear_author_as_series(self) -> None:
        """Очищает proposed_series если оно совпадает с proposed_author или является авторской папкой.

        Серия никогда не должна равняться имени автора. Случаи:
        1. proposed_series == proposed_author (прямое совпадение)
        2. proposed_series совпадает с именем папки, которая есть в author_folder_cache
           (Pass 1 ошибочно использовал авторскую папку как серию — коллекция\\Автор\\файл.fb2)
        """
        from pathlib import Path as _P
        _author_folder_names = {
            _P(k).name.lower().replace('ё', 'е')
            for k in self.author_folder_cache
        }

        _count = 0
        for record in self.records:
            if not record.proposed_series:
                continue
            ps_norm = record.proposed_series.lower().replace('ё', 'е').strip().rstrip('.')

            # Случай 1: серия == автор
            if record.proposed_author:
                au_norm = record.proposed_author.lower().replace('ё', 'е').strip().rstrip('.')
                if ps_norm == au_norm:
                    record.proposed_series = ''
                    record.series_source = ''
                    _count += 1
                    continue

            # Случай 2: серия — это имя авторской папки из кэша
            if ps_norm in _author_folder_names and 'folder_dataset' in (record.series_source or ''):
                record.proposed_series = ''
                record.series_source = ''
                _count += 1

        if _count:
            print(f"[POST-CHECK] Cleared series == author/author-folder in {_count} records")
            self.logger.log(f"[OK] POST-CHECK: Cleared series == author/author-folder in {_count} records")

    def _postcheck_build_subfolder_hierarchy(self) -> None:
        """Строит серию «Родительская\\Подсерия» когда файл вложен глубже одного уровня от автора.

        Для структуры Автор\\СерияВерхнего\\Подсерия\\файл.fb2 предлагаемая серия будет
        только «Подсерия». Этот постчек добавляет префикс «СерияВерхнего\\» если:
          - source = folder_dataset (папочный источник)
          - прямая родительская папка файла совпадает с proposed_series (текущая серия)
          - дедушка-папка не является авторской (не в author_folder_cache) и не корнем
          - дедушка-папка не является коллекционной папкой (не в collection_keywords)
        """
        from pathlib import Path as _P
        from extraction_constants import FILE_EXTENSION_FOLDER_NAMES

        # Нормализованные ключи author_folder_cache (lowercase paths)
        _author_cache_lower = {str(k).lower() for k in self.author_folder_cache}
        _coll_kw = {w.lower() for w in (self.collection_keywords or [])}
        _work_lower = str(self.work_dir).lower().rstrip('/\\')

        _count = 0
        for record in self.records:
            if record.series_source not in ('folder_dataset',):
                continue
            if not record.proposed_series:
                continue

            fp = _P(record.file_path)
            parent = fp.parent          # Подсерия-папка (Ком)
            grandparent = parent.parent  # Возможная серия верхнего уровня (Проект Э.К.С.П.А.Н.С.И.Я)

            # Корень — не обрабатываем
            if str(grandparent).lower().rstrip('/\\') == _work_lower:
                continue

            gp_name = grandparent.name
            if not gp_name:
                continue

            # Прозрачные папки расширений пропускаем
            if gp_name.lower() in FILE_EXTENSION_FOLDER_NAMES:
                continue

            # Дедушка не должен быть авторской папкой
            gp_abs = str(self.work_dir / grandparent).lower()
            if gp_abs in _author_cache_lower:
                continue

            # Дедушка — одиночная буква (алфавитный индекс, не серия)
            if len(gp_name.strip()) == 1:
                continue

            # Стрипим скобочный суффикс один раз для всех проверок ниже
            gp_name_stripped = re.sub(r'\s*\([^)]*\)\s*$', '', gp_name).strip()

            # Дедушка не должен совпадать с proposed_author (авторский псевдоним/логин)
            if record.proposed_author:
                gp_norm = gp_name_stripped.lower().replace('ё', 'е').strip()
                auth_norm = record.proposed_author.lower().replace('ё', 'е').strip()
                auth_parts = auth_norm.split()
                if (gp_norm == auth_norm
                        or gp_norm.startswith(auth_norm)
                        or (auth_parts and auth_parts[0] in gp_norm and len(gp_norm) <= len(auth_norm) + 5)):
                    continue
                # Псевдоним в скобках: "Дубина-Родион (Дарки)" → parenthetical == proposed_author
                gp_parens = re.findall(r'\(([^)]+)\)', gp_name)
                if any(p.lower().replace('ё', 'е').strip() == auth_norm for p in gp_parens):
                    continue

            # Слова metadata_authors совпадают со словами дедушки (sorted word set)
            # Ловит: "Дубина-Родион" vs metadata "Родион Дубина"
            if record.metadata_authors and record.metadata_authors != '[unknown]':
                _first_meta = record.metadata_authors.split(';')[0].split(',')[0].strip()
                if _first_meta:
                    def _mw(s):
                        return sorted(w.strip('.,;').lower().replace('ё', 'е')
                                      for w in s.replace('-', ' ').split() if len(w) > 1)
                    if _mw(gp_name_stripped) == _mw(_first_meta):
                        continue

            # Дедушка выглядит как "Фамилия-Имя": один дефис, оба слова с заглавной буквы
            # Ловит: "Дубовиков-Виталий" когда метаданные содержат неправильного автора
            if '-' in gp_name_stripped and ' ' not in gp_name_stripped:
                _gp_parts = gp_name_stripped.split('-')
                if (len(_gp_parts) == 2
                        and all(p and p[0].isupper() for p in _gp_parts)
                        and all(p[1:].islower() or not p[1:] for p in _gp_parts)):
                    continue

            # Дедушка не должен быть коллекционным keyword
            if any(gp_name.lower().startswith(kw) for kw in _coll_kw):
                continue

            # Родитель (прямая папка файла) не должен быть авторской папкой
            parent_abs = str(self.work_dir / parent).lower()
            if parent_abs in _author_cache_lower:
                continue

            parent_name_norm = parent.name.lower().replace('ё', 'е').strip()
            ps_norm = record.proposed_series.lower().replace('ё', 'е').strip()
            gp_norm = gp_name.lower().replace('ё', 'е').strip()
            gp_clean = re.sub(r'\s*\([^)]*\)\s*$', '', gp_name).strip()
            if not gp_clean:
                continue

            if parent_name_norm == ps_norm:
                # Случай А: родитель совпадает с текущей серией — prepend дедушку
                # "Девятимечье\Е. Фиолетовый Мир\1.fb2", series="Е. Фиолетовый Мир" → "Девятимечье\Е. Фиолетовый Мир"
                record.proposed_series = gp_clean + '\\' + record.proposed_series
                record.series_source = record.series_source + '+subfolder_hierarchy'
                _count += 1
            elif gp_norm == ps_norm:
                # Случай Б: дедушка совпадает с текущей серией — append родителя.
                # "Девятимечье\З. Синий мир\1.fb2", series="Девятимечье" → "Девятимечье\З. Синий мир"
                parent_clean = re.sub(r'\s*\([^)]*\)\s*$', '', parent.name).strip()
                if parent_clean:
                    record.proposed_series = record.proposed_series + '\\' + parent_clean
                    record.series_source = record.series_source + '+subfolder_hierarchy'
                    _count += 1

        if _count:
            print(f"[POST-CHECK] Built subfolder hierarchy for {_count} series values")
            self.logger.log(f"[OK] POST-CHECK: Built subfolder hierarchy in {_count} records")

    def _postcheck_enrich_folder_hierarchy(self) -> None:
        """Обогащает folder_hierarchy серии filename-префиксом когда filename-серия более полная."""
        def _ns(s: str) -> str:
            return re.sub(r'[!?.]+$', '', s).strip().lower().replace('ё', 'е')

        _fn_by_author: dict = {}
        _fn_count: dict = {}
        for record in self.records:
            if not record.proposed_series or 'filename' not in record.series_source:
                continue
            author = record.proposed_author or ''
            norm = _ns(record.proposed_series)
            _fn_count[(author, norm)] = _fn_count.get((author, norm), 0) + 1
            _fn_by_author.setdefault(author, {})[norm] = record.proposed_series

        _count = 0
        for record in self.records:
            if not record.proposed_series:
                continue
            if record.series_source not in ('folder_hierarchy', 'folder_meta_consensus'):
                continue
            author = record.proposed_author or ''
            fn_map = _fn_by_author.get(author, {})
            if not fn_map:
                continue
            folder_norm = _ns(record.proposed_series)
            best = None
            for fn_norm, fn_canonical in fn_map.items():
                if fn_norm == folder_norm:
                    continue
                if not fn_norm.endswith(folder_norm):
                    continue
                prefix_part = fn_norm[: len(fn_norm) - len(folder_norm)].rstrip('. ')
                if not prefix_part:
                    continue
                if _fn_count.get((author, fn_norm), 0) < 2:
                    continue
                best = fn_canonical
                break
            if best and best != record.proposed_series:
                record.proposed_series = best
                record.series_source = record.series_source + '+filename_enriched'
                _count += 1
        if _count:
            print(f"[POST-CHECK] Enriched {_count} folder_hierarchy series with filename prefix")
            self.logger.log(f"[OK] POST-CHECK: Enriched {_count} folder_hierarchy series")

    def _postcheck_series_not_equal_author(self) -> None:
        """Очищает серии которые совпадают с автором (5 типов конфликтов)."""
        # ===== Post-check: series must never equal author =====
        # Normalize both sides for comparison: strip trailing periods, lowercase.
        _series_eq_author_cleared = 0
        for record in self.records:
            if not record.proposed_series or not record.proposed_author:
                continue
            a_norm = self._norm_for_series_cmp(record.proposed_author)
            s_norm = self._norm_for_series_cmp(record.proposed_series)

            is_conflict = (
                # Exact match: series == author (e.g. "Фонд А" == "Фонд А.")
                s_norm == a_norm
                # Series starts with full author + space: "Фонд А Конторщица"
                or s_norm.startswith(a_norm + ' ')
                # Series starts with full author + period: "Берг Ираклий. Крепостной Пушкина"
                or s_norm.startswith(a_norm + '.')
            )

            # Тип А: folder_dataset series looks like a person name and shares
            # surname with the author — folder-author was not in name dicts so became series.
            # E.g. series="Буров Егор" (folder name), author="Буров Дмитрий" (from filename).
            if not is_conflict and record.series_source == 'folder_dataset':
                a_parts = a_norm.split()
                s_parts = s_norm.split()
                if (len(s_parts) <= 3 and len(a_parts) >= 1
                        and len(a_parts[0]) > 3
                        and s_parts and s_parts[0] == a_parts[0]):
                    is_conflict = True

            # Тип Б: co-author folder — series words are a permutation of author words.
            # E.g. author="Винтеркей Серж, Шумилин Артем", series="Шумилин Артем, Винтеркей Серж".
            if not is_conflict and record.series_source == 'folder_dataset':
                _strip_punct = re.compile(r'[.,;()]')
                s_words = set(_strip_punct.sub('', s_norm).split())
                a_words = set(_strip_punct.sub('', a_norm).split())
                if s_words and len(s_words) >= 2 and s_words == a_words:
                    is_conflict = True

            # Тип В: publisher subfolder contains author name mixed with series label.
            # E.g. author="Аберкромби Джо", series="Фэнтези Джо Аберкромби" (folder_dataset).
            # Rule: if all meaningful author words (len>2) appear in series (prefix-match),
            # then:
            #   - if metadata is available and does NOT contain author words → use metadata
            #   - if metadata is empty → clear series entirely
            # Protection: if metadata ALSO contains author words → do not replace (e.g. "Лето Элин").
            if not is_conflict and record.series_source == 'folder_dataset':
                _sp2 = re.compile(r'[.,;()\-]')
                a_meaningful = [w for w in _sp2.sub('', a_norm).split() if len(w) > 2]
                s_str = _sp2.sub('', s_norm)

                def _word_in_str(word, text):
                    """Match word in text allowing 1-2 char Russian inflection suffix."""
                    prefix = word[:max(3, len(word) - 1)]
                    return re.search(r'\b' + re.escape(prefix), text) is not None

                if (len(a_meaningful) >= 2
                        and all(_word_in_str(w, s_str) for w in a_meaningful)):
                    if record.metadata_series:
                        # Check that metadata does NOT also contain author words
                        meta_norm2 = self._norm_for_series_cmp(record.metadata_series)
                        meta_str = _sp2.sub('', meta_norm2)
                        meta_has_author = all(_word_in_str(w, meta_str) for w in a_meaningful)
                        if not meta_has_author:
                            is_conflict = True
                    else:
                        # No metadata fallback — series is pure "label+author", clear it
                        is_conflict = True

            # Тип Г: series contains only the author's surname as a folder label.
            # E.g. author="Скальци Джон", series="Пространство Скальци" (publisher subfolder).
            # Also handles plural surname family folders: "Войлошниковы" for "Войлошников Владимир, Войлошникова Ольга".
            # Requires: metadata available and metadata does NOT contain the surname.
            if not is_conflict and record.series_source == 'folder_dataset' and record.metadata_series:
                _sp2_g = re.compile(r'[.,;()\-]')
                a_norm_g = self._norm_for_series_cmp(record.proposed_author)
                a_surname_g = _sp2_g.sub('', a_norm_g).split()[0] if a_norm_g.strip() else ''
                if len(a_surname_g) > 3:
                    s_str_g = _sp2_g.sub('', s_norm)
                    # Use prefix match allowing 1-2 char Russian inflection
                    surname_prefix_g = a_surname_g[:max(4, len(a_surname_g) - 1)]
                    if re.search(r'\b' + re.escape(surname_prefix_g), s_str_g):
                        meta_norm_g = self._norm_for_series_cmp(record.metadata_series)
                        meta_str_g = _sp2_g.sub('', meta_norm_g)
                        if not re.search(r'\b' + re.escape(surname_prefix_g), meta_str_g):
                            is_conflict = True

            # Тип Д: publisher attribution prefix ("от автора X", "от авторов X").
            # E.g. folder "Fanzon. От автора Киллербота" → series="От автора Киллербота",
            # but metadata has the real series name.
            if not is_conflict and record.series_source == 'folder_dataset' and record.metadata_series:
                if s_norm.startswith('от автор'):
                    is_conflict = True

            # Тип Е: серия-через-дефис совпадает с автором-через-пробел.
            # E.g. proposed_series="Емельянов-Антон", proposed_author="Емельянов Антон" —
            # авторская папка Фамилия-Имя пропущена кэшем и попала в серию.
            if not is_conflict and '-' in record.proposed_series and record.series_source == 'folder_dataset':
                if s_norm.replace('-', ' ').strip() == a_norm:
                    is_conflict = True

            if is_conflict:
                # Если серия уже имеет иерархию "Автор\Серия" — автор-компонент лишний,
                # но настоящая серия (часть после \) валидна — берём её, не metadata.
                if '\\' in record.proposed_series:
                    parts = record.proposed_series.split('\\', 1)
                    tail = parts[1].strip()
                    if tail:
                        record.proposed_series = tail
                        # source остаётся folder_dataset
                        _series_eq_author_cleared += 1
                        continue
                # Дефисный автор: серия совпадает с автором потому что авторская папка
                # пропущена кэшем (напр. "Фамилия-Имя"). Реальная серия — прямой родительский
                # каталог файла, если он отличается от текущей proposed_series.
                if '-' in record.proposed_series and record.series_source == 'folder_dataset':
                    from pathlib import Path as _P
                    _fp_parent = _P(record.file_path).parent.name
                    _ps_norm = record.proposed_series.lower().replace('ё', 'е').strip()
                    _pp_norm = _fp_parent.lower().replace('ё', 'е').strip()
                    if _fp_parent and _pp_norm != _ps_norm and not self._contains_blacklist_word_regen(_fp_parent):
                        record.proposed_series = _fp_parent
                        # source остаётся folder_dataset
                        _series_eq_author_cleared += 1
                        continue
                # Проверяем metadata_series через blacklist перед заменой
                _meta_replacement = record.metadata_series or ''
                if _meta_replacement and self._contains_blacklist_word_regen(_meta_replacement):
                    _meta_replacement = ''
                record.proposed_series = _meta_replacement
                record.series_source = 'metadata' if _meta_replacement else ''
                _series_eq_author_cleared += 1

        if _series_eq_author_cleared:
            print(f"[POST-CHECK] Cleared {_series_eq_author_cleared} records where series == author")
            self.logger.log(f"[OK] POST-CHECK: Cleared {_series_eq_author_cleared} series==author conflicts")

    def _postcheck_clear_title_series_fp(self) -> None:
        """Очищает ложные серии когда серия+номер == название файла."""
        # ===== Post-check: clear false-positive series where series+number == file_title =====
        # Случай: "Коу Джонатан - Номер 11.fb2" → series="Номер", number="11", title="Номер 11"
        # proposed_series + series_number реконструируют file_title → это заголовок, не серия.
        # Защита: не очищаем если metadata подтверждает серию, source не filename,
        # или другой том того же автора+серии имеет подтверждённый source.
        _confirmed_series_pairs: set = set()
        _CONFIRMED_SRCS = {'filename+meta_confirmed', 'filename+meta_expanded',
                           'folder_dataset', 'folder_hierarchy', 'folder_meta_consensus',
                           'folder_metadata_confirmed', 'author-consensus (metadata-confirmed)'}
        for _r in self.records:
            if (_r.proposed_author or '') and (_r.proposed_series or ''):
                if ((_r.series_source or '') in _CONFIRMED_SRCS
                        or 'meta_confirmed' in (_r.series_source or '')):
                    _confirmed_series_pairs.add((
                        (_r.proposed_author or '').strip().lower().replace('ё', 'е'),
                        (_r.proposed_series or '').strip().lower().replace('ё', 'е'),
                    ))
        _title_series_fp_count = 0
        _title_num_re = re.compile(r'\s+\d{1,2}\s*$')
        # author-consensus без metadata → серия == title → ложная серия
        _ac_cleared = 0
        for record in self.records:
            if (record.series_source or '') != 'author-consensus':
                continue
            if not record.proposed_series or record.metadata_series:
                continue
            ft = (record.file_title or '').strip().lower().replace('ё', 'е')
            ps = record.proposed_series.strip().lower().replace('ё', 'е')
            if ft and ft == ps:
                _pair = (
                    (record.proposed_author or '').strip().lower().replace('ё', 'е'),
                    ps,
                )
                if _pair not in _confirmed_series_pairs:
                    record.proposed_series = ''
                    record.series_source = ''
                    record.series_number = ''
                    _ac_cleared += 1
        if _ac_cleared:
            print(f"[POST-CHECK] Cleared {_ac_cleared} author-consensus series==title (no metadata)")
            self.logger.log(f"[OK] POST-CHECK: Cleared {_ac_cleared} author-consensus series==title")

        for record in self.records:
            if not record.proposed_series:
                continue
            if 'filename' not in (record.series_source or ''):
                continue
            ft = (record.file_title or '').strip().lower().replace('ё', 'е')
            ps = record.proposed_series.strip().lower().replace('ё', 'е')
            sn = (record.series_number or '').strip()
            # Вариант 1: series + number (если number извлечён) совпадают с title
            reconstructed = (ps + ' ' + sn).strip() if sn else None
            match1 = reconstructed and ft == reconstructed
            # Вариант 2: title без концевого числа (1–2 цифры) совпадает с series
            ft_stripped = _title_num_re.sub('', ft).strip()
            match2 = ft_stripped == ps and ft_stripped != ft
            # Вариант 3: title начинается с "Series NNN..." где NNN ≥ 100 (трёхзначное)
            # Серийные тома не бывают трёхзначными — это часть заголовка
            # Пример: "Код 612. Кто убил Маленького принца" → series="Код" ложное
            ft_after = ft[len(ps):].lstrip() if ft.startswith(ps) else ''
            match3 = bool(ft_after and re.match(r'^\d{3,}', ft_after))
            if not match1 and not match2 and not match3:
                continue
            # metadata подтверждает серию — не трогаем.
            # Нормализуем пунктуацию при сравнении: «Ревизор: возвращение» содержит «Ревизор возвращение».
            ms = (record.metadata_series or '').strip().lower().replace('ё', 'е')
            ms_norm = re.sub(r'[:\-«»""„"\']+', '', ms).strip()
            ps_norm = re.sub(r'[:\-«»""„"\']+', '', ps).strip()
            if ms and (ps_norm in ms_norm or ps in ms):
                continue
            # Другой том того же автора+серии уже подтверждён → серия реальная.
            _pair = (
                (record.proposed_author or '').strip().lower().replace('ё', 'е'),
                ps,
            )
            if _pair in _confirmed_series_pairs:
                continue
            record.proposed_series = ''
            record.series_source = ''
            record.series_number = ''
            _title_series_fp_count += 1
        if _title_series_fp_count:
            print(f"[POST-CHECK] Cleared {_title_series_fp_count} false-positive series (series+number==title)")
            self.logger.log(f"[OK] POST-CHECK: Cleared {_title_series_fp_count} series==title false positives")

    def _postcheck_filename_prefix_pattern(self) -> None:
        """Определяет серию по общему префиксу имён файлов того же автора."""
        # ===== Post-check: filename prefix pattern — cross-file series detection =====
        # Если Author. Title.fb2 И Author. Title. Subtitle.fb2 лежат в одной папке,
        # то Title — название серии для обоих файлов.
        # Признак: стем одного файла является префиксом стема другого (у того же автора).
        _prefix_series_count = 0
        _prefix_groups: dict = {}
        for record in self.records:
            folder = str(Path(record.file_path).parent)
            author = record.proposed_author or ''
            _prefix_groups.setdefault((folder, author), []).append(record)

        for (folder, author), grp in _prefix_groups.items():
            if len(grp) < 2:
                continue
            # Извлечь часть имени файла после автора (Author. Title → Title)
            def _title_part(rec, _author=author, _self=self):
                stem = Path(rec.file_path).stem
                _a_norm = _self._norm_for_series_cmp(_author)
                for sep in ('. ', ' - '):
                    if sep in stem:
                        before, after = stem.split(sep, 1)
                        if _self._norm_for_series_cmp(before) == _a_norm or _self._norm_for_series_cmp(before) in _a_norm:
                            return after.strip()
                return stem.strip()

            titled = [(r, _title_part(r)) for r in grp]

            from difflib import SequenceMatcher

            def _is_prefix_match(ta: str, tb: str) -> bool:
                """True если ta является префиксом tb (точно или с небольшой опечаткой).
                Стратегия: берём первые len(ta) символов tb и сравниваем через SequenceMatcher.
                Порог схожести 0.85 — допускает 1-2 символа разницы в длинных словах.
                """
                if not ta or not tb:
                    return False
                # Точный match
                if tb.startswith(ta + '. ') or tb.startswith(ta + '.'):
                    return True
                # Нечёткий: tb должен быть длиннее ta, сравниваем prefix
                if len(tb) <= len(ta):
                    return False
                # Убедиться что после предполагаемого префикса идёт '. ' или конец
                cut = tb[:len(ta)]
                rest = tb[len(ta):]
                if not rest.startswith('. ') and not rest.startswith('.'):
                    return False
                ratio = SequenceMatcher(None, ta, cut).ratio()
                return ratio >= 0.85

            # Ищем пары A, B где title_A — префикс title_B (разделитель '. ')
            for rec_a, title_a in titled:
                if not title_a:
                    continue
                ta_norm = self._norm_for_series_cmp(title_a)
                for rec_b, title_b in titled:
                    if rec_b is rec_a or not title_b:
                        continue
                    tb_norm = self._norm_for_series_cmp(title_b)
                    if _is_prefix_match(ta_norm, tb_norm):
                        # Canonical series name: prefer existing proposed_series (e.g. from "filename")
                        canonical = rec_a.proposed_series or rec_b.proposed_series or title_a
                        if not rec_a.proposed_series:
                            rec_a.proposed_series = canonical
                            rec_a.series_source = 'filename_prefix_pattern'
                            _prefix_series_count += 1
                        if not rec_b.proposed_series:
                            rec_b.proposed_series = canonical
                            rec_b.series_source = 'filename_prefix_pattern'
                            _prefix_series_count += 1
                        break
        if _prefix_series_count:
            print(f"[POST-CHECK] Assigned series via filename prefix pattern: {_prefix_series_count} records")
            self.logger.log(f"[OK] POST-CHECK: filename prefix pattern → {_prefix_series_count} series assigned")

    def _postcheck_clear_large_numbers(self) -> None:
        """Очищает series_number >= 100 (год, номер главы, фрагмент заголовка)."""
        _count = 0
        for record in self.records:
            sn = (record.series_number or '').strip()
            if sn and re.match(r'^\d+$', sn) and int(sn) >= 100:
                record.series_number = ''
                _count += 1
        if _count:
            print(f"[POST-CHECK] Cleared {_count} oversized series numbers (>=100)")
            self.logger.log(f"[OK] POST-CHECK: Cleared {_count} oversized series numbers")

    def _postcheck_strip_service_words(self) -> None:
        """Убирает хвостовые сервисные слова (Книга, Том, Часть, Book, Vol) из серий."""
        _service_tail_re = re.compile(
            r'\s+(?:книга|том|часть|book|vol|volume)\.?\s*$',
            re.IGNORECASE | re.UNICODE,
        )
        _count = 0
        for record in self.records:
            if not record.proposed_series:
                continue
            cleaned = _service_tail_re.sub('', record.proposed_series).strip()
            if cleaned and cleaned != record.proposed_series:
                record.proposed_series = cleaned
                _count += 1
        if _count:
            print(f"[POST-CHECK] Stripped trailing service words from {_count} series values")
            self.logger.log(f"[OK] POST-CHECK: Stripped service words from {_count} series")

    def _postcheck_dedup_backslash_hierarchy(self) -> None:
        """Убирает дубль в иерархии: «Боец\\Боец» → «Боец»."""
        _count = 0
        for record in self.records:
            ps = record.proposed_series
            if ps and '\\' in ps:
                parts = ps.split('\\')
                p0 = parts[0].strip().lower().replace('ё', 'е')
                pl = parts[-1].strip().lower().replace('ё', 'е')
                if p0 and p0 == pl:
                    record.proposed_series = parts[0].strip()
                    _count += 1
        if _count:
            print(f"[POST-CHECK] Deduplicated identical backslash series in {_count} records")
            self.logger.log(f"[OK] POST-CHECK: Deduplicated backslash series in {_count} records")

    def _postcheck_dedup_consecutive_words(self) -> None:
        """Убирает повторяющиеся последовательные слова в серии."""
        _dedup_word_re = re.compile(r'\b(\w+)\b(\s+\1)+\b', re.IGNORECASE | re.UNICODE)
        _count = 0
        for record in self.records:
            if not record.proposed_series:
                continue
            cleaned = _dedup_word_re.sub(r'\1', record.proposed_series)
            if cleaned != record.proposed_series:
                record.proposed_series = cleaned.strip()
                _count += 1
        if _count:
            print(f"[POST-CHECK] Deduplicated words in {_count} series values")
            self.logger.log(f"[OK] POST-CHECK: Deduplicated words in {_count} series")

    def _postcheck_trim_to_metadata_prefix(self) -> None:
        """Обрезает filename-серию до metadata-префикса когда filename добавил лишние слова."""
        _count = 0
        for record in self.records:
            if not record.proposed_series or not record.metadata_series:
                continue
            if 'filename' not in record.series_source:
                continue
            if '\\' in record.proposed_series:
                continue
            ps_l = record.proposed_series.lower().replace('ё', 'е')
            ms_l = record.metadata_series.lower().replace('ё', 'е').strip()
            rest_after_meta = record.proposed_series[len(ms_l):].strip()
            if (len(ms_l) >= 6
                    and len(ms_l.split()) >= 2
                    and ps_l.startswith(ms_l)
                    and len(record.proposed_series) > len(ms_l)
                    and not record.proposed_series[len(ms_l)].isalpha()
                    and not re.search(r'\d', rest_after_meta)):
                record.proposed_series = record.metadata_series.strip()
                record.series_source = 'metadata'
                _count += 1
        if _count:
            print(f"[POST-CHECK] Trimmed filename series to metadata prefix in {_count} records")
            self.logger.log(f"[OK] POST-CHECK: Trimmed series to metadata prefix in {_count} records")

    def _postcheck_expand_truncated_series(self) -> None:
        """Расширяет усечённую filename-серию до полной версии из metadata."""
        def _nd(s: str) -> str:
            return s.replace('–', '-').replace('—', '-').replace('‒', '-')
        _count = 0
        for record in self.records:
            if not record.proposed_series or not record.metadata_series:
                continue
            if 'meta_expanded' in record.series_source:
                continue
            ps_l = _nd(record.proposed_series.lower().replace('ё', 'е').strip())
            ms_l = _nd(record.metadata_series.lower().replace('ё', 'е').strip())
            # Rule 1: proposed is a truncated prefix of metadata (filename sources only)
            if 'filename' in record.series_source:
                if len(ps_l) >= 3 and ms_l.startswith(ps_l) and len(ms_l) > len(ps_l):
                    extra = ms_l[len(ps_l):].strip()
                    if extra and (extra[0].isalnum() or extra[0] in '-–—:'):
                        # Preserve uppercase prefix from proposed (e.g. "РОС") if all-caps
                        ps_orig = record.proposed_series.strip()
                        ms_orig = _nd(record.metadata_series.strip())
                        if ps_orig == ps_orig.upper() and ps_orig.isalpha():
                            suffix = ms_orig[len(ps_l):]
                            expanded = ps_orig + suffix
                        else:
                            expanded = ms_orig
                        record.proposed_series = expanded
                        record.series_source = record.series_source + '+meta_expanded'
                        _count += 1
                        continue
            # Rule 2: meta = "PREFIX: SUBSERIES", proposed = "SUBSERIES"
            # Build "PREFIX\SUBSERIES" hierarchy (backslash = series separator in this system)
            # Applies to any source (filename, folder_dataset) since match is exact
            if ':' in record.metadata_series and len(ps_l) >= 3:
                colon_idx = ms_l.index(':')
                prefix_raw = record.metadata_series[:colon_idx].strip()
                subseries_l = ms_l[colon_idx + 1:].strip().lstrip('- ')
                if subseries_l and subseries_l == ps_l and prefix_raw:
                    record.proposed_series = prefix_raw + '\\' + record.proposed_series
                    record.series_source = record.series_source + '+meta_expanded'
                    _count += 1
        if _count:
            print(f"[POST-CHECK] Expanded truncated filename series via metadata in {_count} records")
            self.logger.log(f"[OK] POST-CHECK: Expanded series via metadata in {_count} records")

    def _clear_collection_folder_series(self) -> None:
        """Финальный пост-чек: папка серии всегда внутри папки автора.

        Если папка содержит книги разных авторов (коллекция), её имя не является серией
        ни из какого источника. Сбрасываем proposed_series для всех файлов в такой папке,
        если серия совпадает с именем папки.
        """
        from collections import defaultdict
        from pathlib import Path as _P

        folder_files: dict = defaultdict(list)
        for record in self.records:
            folder_files[str(_P(record.file_path).parent)].append(record)

        cleared = 0
        for folder_path, files_in_folder in folder_files.items():
            # Используем proposed_author (нормализован pass3) для определения авторского состава.
            unique_authors = {
                f.proposed_author.strip()
                for f in files_in_folder
                if f.proposed_author and f.proposed_author.strip() not in ('', 'Сборник')
            }
            if len(unique_authors) <= 1:
                continue

            folder_name_norm = _P(folder_path).name.lower().replace('ё', 'е').strip()

            for record in files_in_folder:
                if not record.proposed_series:
                    continue
                # Папочный источник авторитетен — пользователь сам создал структуру
                if record.series_source in ('folder_dataset', 'folder_hierarchy',
                                            'folder_meta_consensus', 'folder_metadata_confirmed'):
                    continue
                # Записи, расширенные через метаданные — надёжны, не сбрасываем
                if 'meta_expanded' in record.series_source:
                    continue
                ps_norm = record.proposed_series.lower().replace('ё', 'е').strip()
                if ps_norm == folder_name_norm or folder_name_norm.startswith(ps_norm) or ps_norm.startswith(folder_name_norm) or (len(ps_norm) >= 5 and ps_norm in folder_name_norm):
                    record.proposed_series = ''
                    record.series_source = ''
                    cleared += 1

        if cleared:
            print(f"[POST-CHECK] Cleared {cleared} collection-folder series (multi-author folders)")
            self.logger.log(f"[OK] POST-CHECK: Cleared {cleared} series from multi-author collection folders")

    def _save_csv(self) -> None:
        """Save records to CSV file."""

        # Финальная очистка: серия из папки-коллекции (несколько авторов).
        # Запускается ПОСЛЕ всех пасов и rescue-блоков — чтобы перекрыть любые источники.
        self._clear_collection_folder_series()

        # ===== Post-check: expand truncated metadata series using longer version from same author =====
        # Случай: metadata_series книги содержит только начало названия ("Режиссер"),
        # а у других книг того же автора есть полное название ("Режиссер Советского Союза").
        # Расширяем усечённые значения до полного.
        _STRONG = {'filename+meta_confirmed', 'filename', 'folder_dataset',
                   'folder_hierarchy', 'folder_meta_consensus'}
        _auth_long: dict = {}  # author → list of (proposed_series_lower, proposed_series_original)
        for _r in self.records:
            if _r.proposed_author and _r.proposed_series and _r.series_source in _STRONG:
                _auth_long.setdefault(_r.proposed_author, []).append(
                    (_r.proposed_series.lower().replace('ё', 'е').strip(), _r.proposed_series)
                )
        _meta_expand2_count = 0
        for _r in self.records:
            if not _r.proposed_series or _r.series_source != 'metadata':
                continue
            _ps_l = _r.proposed_series.lower().replace('ё', 'е').strip()
            for _full_l, _full in _auth_long.get(_r.proposed_author, []):
                if _full_l.startswith(_ps_l) and len(_full_l) > len(_ps_l):
                    _extra = _full_l[len(_ps_l):].strip()
                    if _extra and (_extra[0].isalnum() or _extra[0] in '-–— '):
                        _r.proposed_series = _full
                        _r.series_source = 'metadata+author_expanded'
                        _meta_expand2_count += 1
                        break
        if _meta_expand2_count:
            print(f"[POST-CHECK] Expanded {_meta_expand2_count} truncated metadata series via author group")
            self.logger.log(f"[OK] POST-CHECK: Expanded {_meta_expand2_count} truncated metadata series via author group")

        # ===== Post-check: одиночная metadata-серия без подтверждения в пути =====
        # Если у автора ровно ОДИН файл с данным значением серии из metadata,
        # и это значение не встречается ни в имени файла, ни в именах папок пути,
        # и series_number пустой — такую серию не принимаем.
        # Цель: отсечь артефакты metadata вроде «The Pact - ru (версии)».
        _meta_singleton_count = 0
        from collections import defaultdict as _dd
        import unicodedata as _ud_s
        def _sn_norm(s: str) -> str:
            return _ud_s.normalize('NFC', s or '').rstrip('. ').strip().lower().replace('ё', 'е')
        _author_series_cnt: dict = _dd(lambda: _dd(int))
        for _rec in self.records:
            if not _rec.proposed_series or _rec.series_source != 'metadata':
                continue
            _author_series_cnt[_sn_norm(_rec.proposed_author or '')][_sn_norm(_rec.proposed_series)] += 1

        for _rec in self.records:
            if not _rec.proposed_series or _rec.series_source != 'metadata':
                continue
            if _rec.series_number:
                continue  # есть номер тома — оставляем
            _ak = _sn_norm(_rec.proposed_author or '')
            _sk = _sn_norm(_rec.proposed_series)
            if _author_series_cnt[_ak][_sk] > 1:
                continue  # несколько файлов с этой серией — оставляем
            # Проверяем вхождение значения серии в путь файла (папки + имя файла)
            _ser_lc = _rec.proposed_series.lower().replace('ё', 'е')
            _found_in_path = any(
                _ser_lc in _part.lower().replace('ё', 'е')
                for _part in Path(_rec.file_path).parts
            )
            if _found_in_path:
                continue
            _rec.proposed_series = ''
            _rec.series_source = ''
            _meta_singleton_count += 1
        if _meta_singleton_count:
            print(f"[POST-CHECK] Cleared {_meta_singleton_count} singleton metadata series not found in file path")
            self.logger.log(f"[OK] POST-CHECK: Cleared {_meta_singleton_count} uncorroborated singleton metadata series")

        # Sort by file_path
        self.records.sort(key=lambda r: r.file_path)
        
        # Write to CSV
        with open(self.output_csv, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Write header
            writer.writerow([
                'file_path',
                'metadata_authors',
                'proposed_author',
                'author_source',
                'metadata_series',
                'proposed_series',
                'series_source',
                'series_number',
                'file_title',
                'metadata_genre',
                'delete_flag'
            ])

            # Write data
            for record in self.records:
                writer.writerow([
                    record.file_path,
                    record.metadata_authors,
                    record.proposed_author,
                    record.author_source,
                    record.metadata_series,
                    record.proposed_series,
                    record.series_source,
                    record.series_number,
                    record.file_title,
                    record.metadata_genre if record.metadata_genre and record.metadata_genre != 'None' else '',
                    'DELETE' if getattr(record, 'delete_flag', False) else ''
                ])


def main():
    """Main entry point."""
    config_path = sys.argv[1] if len(sys.argv) > 1 else 'config.json'
    
    service = RegenCSVService(config_path)
    success = service.regenerate()
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
