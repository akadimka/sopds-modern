"""
Модуль для обработки и извлечения информации о серии из названия файла или пути.

Использует конфигурационные списки паттернов для поиска серий в:
- Названиях файлов (author_series_patterns_in_files)
- Структурах папок (author_series_patterns_in_folders)

Приоритет источников (от низшего к высшему):
1. FOLDER_STRUCTURE - структура папок
2. FILENAME - название файла
3. FB2_METADATA - метаданные FB2 файла
"""

import re
from typing import Optional, List, Dict, Tuple, Any
from collections import defaultdict

try:
    from settings_manager import SettingsManager
    from pattern_converter import compile_patterns
    from extraction_constants import (
        SeriesExtractionPriority,
        ConfidenceLevel,
        FilterReason,
        ExtractionResult
    )
    from series_normalizer import SeriesNormalizer
    from series_helpers import (
        _nfc_lower_yo, _strip_author_suffix, _bl_matches,
        _strip_author_from_stem, _title_phrases, extract_series_base_from_filename,
        is_series_collection_folder, has_service_marker, get_folder_depth
    )
except ImportError:
    from .settings_manager import SettingsManager
    from .pattern_converter import compile_patterns
    from .extraction_constants import (
        SeriesExtractionPriority,
        ConfidenceLevel,
        FilterReason,
        ExtractionResult
    )
    from .series_normalizer import SeriesNormalizer
    from .series_helpers import (
        _nfc_lower_yo, _strip_author_suffix, _bl_matches,
        _strip_author_from_stem, _title_phrases, extract_series_base_from_filename,
        is_series_collection_folder, has_service_marker, get_folder_depth
    )


class SeriesProcessor:
    """Класс для обработки и извлечения информации о серии из файлов и путей."""
    
    def __init__(self, config_path: str = 'config.json', folder_parse_limit: Optional[int] = None):
        """
        Инициализация процессора серий.
        
        Args:
            config_path: Путь к файлу конфигурации
            folder_parse_limit: Предел количества папок при парсинге от файла.
                               Если None, загружается из конфигурации.
        """
        self.settings = SettingsManager(config_path)
        
        # Используем переданное значение или загружаем из конфигурации
        if folder_parse_limit is not None:
            self.folder_parse_limit = int(folder_parse_limit)
        else:
            self.folder_parse_limit = self.settings.get_folder_parse_limit()
        
        self.file_patterns = None
        self.folder_patterns = None
        self.sequence_patterns = None
        self.normalizer = SeriesNormalizer()
        self._load_patterns()
    
    def _load_patterns(self):
        """Загрузить паттерны из конфигурации и скомпилировать их."""
        # Загружаем паттерны для поиска в названиях файлов
        file_patterns_raw = self.settings.get_author_series_patterns_in_files()
        self.file_patterns = compile_patterns(file_patterns_raw) if file_patterns_raw else []
        
        # Загружаем паттерны для поиска в структурах папок
        folder_patterns_raw = self.settings.get_author_series_patterns_in_folders()
        self.folder_patterns = compile_patterns(folder_patterns_raw) if folder_patterns_raw else []
        
        # Загружаем паттерны для поиска номеров в последовательности
        sequence_patterns_raw = self.settings.get_sequence_patterns()
        self.sequence_patterns = compile_patterns(sequence_patterns_raw) if sequence_patterns_raw else []
    
    def extract_series_from_filename(self, filename: str) -> Optional[List[ExtractionResult]]:
        """
        Извлечь информацию о серии из названия файла.

        Args:
            filename: Название файла (без расширения)

        Returns:
            Список результатов извлечения (может быть несколько совпадений)
            или None если ничего не найдено
        """
        if not filename or not self.file_patterns:
            return None

        results = []
        filename_lower = filename.lower()

        # Загрузить blacklist
        blacklist = set(self.settings.get_list('filename_blacklist')) if self.settings else set()

        for pattern in self.file_patterns:
            try:
                match = pattern.search(filename)
                if match:
                    series_group = match.groupdict().get('series')
                    if series_group:
                        series_clean = series_group.strip()

                        # Проверить против blacklist
                        if any(_bl_matches(bl, series_clean) for bl in blacklist):
                            continue

                        # Извлечь номер если есть
                        sequence_result = self.extract_sequence_number(filename)
                        sequence_num = sequence_result[0].extracted_value if sequence_result else None

                        result = ExtractionResult(
                            extracted_value=series_clean,
                            source_priority=SeriesExtractionPriority.FILENAME,
                            confidence=ConfidenceLevel.MEDIUM,  # 0.60-0.80
                            sequence_number=sequence_num,
                            raw_match=match.group(0),
                            pattern_used=str(pattern.pattern)
                        )
                        results.append(result)

            except Exception:
                # Пропустить невалидные паттерны
                continue

        return results if results else None
    
    def extract_series_from_filepath(self, filepath: str) -> Optional[List[ExtractionResult]]:
        """
        Извлечь информацию о серии из пути файла (анализ структуры папок).

        Args:
            filepath: Полный путь к файлу

        Returns:
            Список результатов извлечения или None
        """
        if not filepath or not self.folder_patterns:
            return None

        from pathlib import Path
        path = Path(filepath)
        folders = list(path.parents)[:self.folder_parse_limit]  # Ограничить глубину

        results = []
        blacklist = set(self.settings.get_list('filename_blacklist')) if self.settings else set()

        for folder in folders:
            folder_name = folder.name
            if not folder_name:
                continue

            for pattern in self.folder_patterns:
                try:
                    match = pattern.search(folder_name)
                    if match:
                        series_group = match.groupdict().get('series')
                        if series_group:
                            series_clean = series_group.strip()

                            # Проверить против blacklist
                            if any(_bl_matches(bl, series_clean) for bl in blacklist):
                                continue

                            result = ExtractionResult(
                                extracted_value=series_clean,
                                source_priority=SeriesExtractionPriority.FOLDER_STRUCTURE,
                                confidence=ConfidenceLevel.MEDIUM,  # 0.60-0.80
                                raw_match=match.group(0),
                                pattern_used=str(pattern.pattern)
                            )
                            results.append(result)

                except Exception:
                    continue

        return results if results else None
    
    def extract_sequence_number(self, text: str) -> Optional[List[ExtractionResult]]:
        """
        Извлечь номер последовательности/книги в серии.

        Args:
            text: Текст для поиска номера (обычно часть имени файла или папки)

        Returns:
            Список результатов извлечения (содержит число)
        """
        if not text or not self.sequence_patterns:
            return None

        results = []

        for pattern in self.sequence_patterns:
            try:
                match = pattern.search(text)
                if match:
                    number_group = match.groupdict().get('number') or match.groupdict().get('sequence')
                    if number_group:
                        try:
                            number = int(number_group.strip())
                            result = ExtractionResult(
                                extracted_value=str(number),
                                source_priority=SeriesExtractionPriority.FILENAME,  # Или SEQUENCE
                                confidence=ConfidenceLevel.HIGH,  # Номера обычно надежны
                                sequence_number=number,
                                raw_match=match.group(0),
                                pattern_used=str(pattern.pattern)
                            )
                            results.append(result)
                        except ValueError:
                            continue
            except Exception:
                continue

        return results if results else None
    
    def extract_series_combined(self, filename: str, filepath: str) -> Optional[Dict[str, Any]]:
        """
        Попытаться извлечь информацию о серии, комбинируя различные методы.

        Args:
            filename: Название файла
            filepath: Полный путь к файлу

        Returns:
            Словарь с наиболее вероятной информацией о серии
        """
        candidates = []

        # Попробовать из filename
        filename_results = self.extract_series_from_filename(filename)
        if filename_results:
            for result in filename_results:
                candidates.append({
                    'series': result.extracted_value,
                    'source': 'filename',
                    'confidence': result.confidence,
                    'sequence': result.sequence_number
                })

        # Попробовать из filepath
        filepath_results = self.extract_series_from_filepath(filepath)
        if filepath_results:
            for result in filepath_results:
                candidates.append({
                    'series': result.extracted_value,
                    'source': 'folder',
                    'confidence': result.confidence,
                    'sequence': result.sequence_number
                })

        if not candidates:
            return None

        # Выбрать лучший результат
        best = max(candidates, key=lambda x: x['confidence'])

        return {
            'series_name': best['series'],
            'source': best['source'],
            'confidence': best['confidence'],
            'sequence_number': best['sequence'],
            'all_candidates': candidates
        }
    
    def categorize_series(self, series_name: str) -> Optional[Dict[str, Any]]:
        """
        Определить категорию/жанр серии на основе названия.

        Args:
            series_name: Название серии

        Returns:
            Словарь с информацией о категории или None
            Структура: {
                'category': str,         # Определённая категория
                'keywords': list,        # Найденные ключевые слова
                'confidence': float      # Уверенность определения
            }
        """
        if not series_name:
            return None

        # Загрузить категории из конфигурации
        category_words = self.settings.get_dict('series_category_words') if self.settings else {}

        series_lower = series_name.lower()
        found_keywords = []
        best_category = None
        max_confidence = 0.0

        for category, keywords in category_words.items():
            category_keywords = []
            for keyword in keywords:
                if keyword.lower() in series_lower:
                    category_keywords.append(keyword)
                    confidence = len(keyword) / len(series_name)  # Простая метрика
                    if confidence > max_confidence:
                        max_confidence = confidence
                        best_category = category
            if category_keywords:
                found_keywords.extend(category_keywords)

        if not best_category:
            return None

        return {
            'category': best_category,
            'keywords': found_keywords,
            'confidence': min(max_confidence, 1.0)  # Ограничить до 1.0
        }
    
    def apply_author_consensus(self, records: List) -> int:
        """
        Применить консенсус авторов в папке.

        Args:
            records: Список BookRecord объектов

        Returns:
            Количество примененных изменений
        """

        # Группировать по папке
        groups = defaultdict(list)
        for record in records:
            from pathlib import Path
            folder = Path(record.file_path).parent
            groups[folder].append(record)

        consensus_count = 0

        for folder, group_records in groups.items():
            # Источники с высоким приоритетом (filename важнее metadata)
            _HIGH_PRIORITY = {'folder_dataset', 'folder_hierarchy', 'filename', 'filename+meta_expanded'}
            _LOW_PRIORITY  = {'metadata', 'consensus', ''}

            high_priority = [r for r in group_records if r.author_source in _HIGH_PRIORITY]
            all_sourced   = [r for r in group_records if r.author_source]

            if not all_sourced:
                continue

            # Если есть хотя бы один файл с filename-источником — он авторитетен для всей папки.
            # Используем только высокоприоритетные файлы для формирования консенсуса.
            consensus_pool = high_priority if high_priority else all_sourced

            author_counts = {}
            for record in consensus_pool:
                if record.proposed_author and record.proposed_author != 'Сборник':
                    author_counts[record.proposed_author] = author_counts.get(record.proposed_author, 0) + 1

            if not author_counts:
                continue

            consensus_author = max(author_counts, key=author_counts.get)

            # Требуем строгое большинство: консенсус-автор должен занимать ≥50% high-priority записей.
            # Это блокирует сборники, где десятки авторов и нет доминирующего.
            total_hp = len(high_priority) if high_priority else len(all_sourced)
            if total_hp > 0:
                consensus_share = author_counts[consensus_author] / total_hp
                if consensus_share < 0.5:
                    continue  # нет большинства — не применяем

            # Применить ко всем файлам с низкоприоритетным или пустым источником
            for record in group_records:
                if record.author_source in _HIGH_PRIORITY:
                    continue  # не трогаем авторитетные источники
                if record.proposed_author in ('Сборник', 'Соавторство'):
                    continue  # явный коллективный маркер — не перезаписываем консенсусом
                if record.proposed_author == consensus_author:
                    continue  # уже правильно
                if record.proposed_author and record.proposed_author != 'Сборник' and not high_priority:
                    continue  # без filename-донора не перезаписываем metadata
                # Защита: полное имя из metadata (≥2 слова) не перезаписываем консенсусом папки.
                # В сборнике у каждой книги свой автор — FB2-метаданные авторитетны.
                if (record.author_source == 'metadata'
                        and record.proposed_author
                        and len(record.proposed_author.split()) >= 2):
                    continue
                record.proposed_author = consensus_author
                record.author_source = 'consensus'
                consensus_count += 1

        return consensus_count

    def apply_series_consensus(self, records: List) -> int:
        """
        Применить консенсус серий.

        Args:
            records: Список BookRecord объектов

        Returns:
            Количество примененных изменений
        """
        # Группировать по автору
        author_groups = defaultdict(list)
        for record in records:
            author = record.proposed_author or '[unknown]'
            author_groups[author].append(record)

        series_consensus_count = 0

        for author, author_records in author_groups.items():
            # Карта серия → записи
            series_base_map = {}

            for record in author_records:
                if record.extracted_series_candidate:
                    normalized = self.normalizer.normalize_series_for_consensus(record.extracted_series_candidate)
                    if normalized not in series_base_map:
                        series_base_map[normalized] = []
                    series_base_map[normalized].append(record)

            # Для каждой базы серий
            for series_base, source_records in series_base_map.items():
                for target_record in author_records:
                    if target_record.proposed_series and target_record.series_source not in ('metadata', 'metadata_folder_confirmed'):
                        continue

                    if target_record.extracted_series_candidate:
                        continue

                    # Проверить, содержит ли имя файла базу серии
                    from pathlib import Path
                    filename = Path(target_record.file_path).stem
                    filename_normalized = _nfc_lower_yo(filename)
                    series_base_normalized = _nfc_lower_yo(series_base)

                    if len(series_base_normalized) < 2:
                        continue

                    if series_base_normalized in filename_normalized:
                        # Проверить позицию
                        dash_pos = filename_normalized.find(' - ')
                        dot_pos = filename_normalized.find('. ')
                        base_pos = filename_normalized.find(series_base_normalized)

                        applies = False
                        if dash_pos >= 0 and base_pos > dash_pos:
                            applies = True
                        elif dot_pos >= 0 and base_pos > dot_pos:
                            applies = True
                        elif base_pos == 0:
                            applies = True

                        if applies:
                            if target_record.series_source == 'no_series_folder':
                                continue
                            target_record.proposed_series = series_base
                            target_record.series_source = 'author-consensus'

                            # Проверить подтверждение метаданными
                            if (target_record.metadata_series and
                                self.normalizer.normalize_series_for_consensus(target_record.metadata_series) == series_base):
                                target_record.series_source = 'author-consensus (metadata-confirmed)'

                            series_consensus_count += 1

        return series_consensus_count

    def reload_patterns(self):
        """Перезагрузить паттерны из конфигурации."""
        self._load_patterns()


if __name__ == '__main__':
    # Простой тест
    processor = SeriesProcessor()

