"""
Утилиты для нормализации и конвертации авторов.
Содержит функции обработки авторов БЕЗ парсинга.
"""

from pathlib import Path
from typing import List, Dict, Optional
try:
    from logger import Logger
except ImportError:
    from .logger import Logger


class AuthorUtils:
    """Утилиты для работы с авторами."""
    
    def __init__(self, settings_manager, fb2_author_extractor):
        """
        Инициализация.
        
        Args:
            settings_manager: SettingsManager с конфигурацией
            fb2_author_extractor: FB2AuthorExtractor с методами нормализации
        """
        self.logger = Logger()
        self.settings = settings_manager
        self.extractor = fb2_author_extractor
        self.surname_conversions = settings_manager.get_author_surname_conversions()
    
    def apply_surname_conversions(self, authors_str: str) -> str:
        """
        Применить конвертации фамилий к строке авторов.
        
        Приоритет:
        1. Точное совпадение целой строки (например "Гоблин (MeXXanik)" -> "Гоблин MeXXanik")
        2. Затем разбор по авторам и поиск фамилий для конвертации
        
        Args:
            authors_str: Строка авторов в формате "Имя Фамилия; Имя2 Фамилия2"
        
        Returns:
            Строка авторов с применёнными конвертациями фамилий
        """
        if not authors_str or not self.surname_conversions:
            return authors_str
        
        # ПРИОРИТЕТ 1: Проверить точное совпадение целой строки
        if authors_str in self.surname_conversions:
            return self.surname_conversions[authors_str]
        
        # ПРИОРИТЕТ 2: Разделить авторов и искать фамилии
        authors = authors_str.split(';')
        converted_authors = []
        
        for author in authors:
            author = author.strip()
            if not author:
                continue
            
            # Попытаться найти фамилию для конвертации
            parts = author.split()
            if len(parts) >= 2:
                potential_surname_last = parts[-1]
                potential_surname_first = parts[0]
                
                converted = False
                
                # Проверить последний элемент
                if potential_surname_last in self.surname_conversions:
                    converted_surname = self.surname_conversions[potential_surname_last]
                    converted_author = ' '.join(parts[:-1] + [converted_surname])
                    converted_authors.append(converted_author)
                    converted = True
                # Проверить первый элемент
                elif potential_surname_first in self.surname_conversions:
                    converted_surname = self.surname_conversions[potential_surname_first]
                    converted_author = ' '.join([converted_surname] + parts[1:])
                    converted_authors.append(converted_author)
                    converted = True
                
                if not converted:
                    converted_authors.append(author)
            else:
                # Если только одно слово, проверим его как фамилию
                if author in self.surname_conversions:
                    converted_authors.append(self.surname_conversions[author])
                else:
                    converted_authors.append(author)
        
        return "; ".join(converted_authors)
    
    def apply_surname_conversions_to_records(self, records) -> list:
        """
        Применить конвертации фамилий ко всем записям (для proposed_author).
        
        Args:
            records: Список BookRecord
            
        Returns:
            Список записей с применёнными конвертациями фамилий
        """
        if not self.surname_conversions:
            return records
        
        conversions_applied = 0
        for record in records:
            if record.proposed_author and record.proposed_author != "Сборник":
                original = record.proposed_author
                record.proposed_author = self.apply_surname_conversions(record.proposed_author)
                if original != record.proposed_author:
                    conversions_applied += 1
                    self.logger.log(f"[PASS 5] Конвертация: '{original}' -> '{record.proposed_author}'")
        
        if conversions_applied > 0:
            self.logger.log(f"[PASS 5] Всего применено конвертаций: {conversions_applied}")
        
        # Финальная нормализация всех proposed_author после конвертаций
        for record in records:
            if record.proposed_author and record.proposed_author != "Сборник":
                original = record.proposed_author
                record.proposed_author = self.extractor._normalize_author_format(record.proposed_author)
                if original != record.proposed_author:
                    self.logger.log(f"[PASS 5] Нормализация: '{original}' -> '{record.proposed_author}'")
        
        return records
    
    def build_authors_map(self, records) -> Dict[str, list]:
        """
        Построить словарь фамилия -> [список полных имён] из всех предложенных авторов.
        
        Args:
            records: Список BookRecord
        
        Returns:
            Словарь {фамилия.lower(): [полное_имя1, полное_имя2, ...]}
        """
        authors_map = {}
        
        for record in records:
            if not record.proposed_author or record.proposed_author == "Сборник":
                continue
            
            # Парсить полные имена (не аббревиатуры)
            if '.' in record.proposed_author:
                continue
            
            # Для каждого автора из proposed_author
            for author_part in record.proposed_author.split(','):
                author_part = author_part.strip()
                if not author_part:
                    continue
                
                # Парсить "Фамилия Имя"
                parts = author_part.split()
                if len(parts) >= 2:
                    surname = parts[0].lower()
                    if surname not in authors_map:
                        authors_map[surname] = []
                    if author_part not in authors_map[surname]:
                        authors_map[surname].append(author_part)
        
        # Также собрать из metadata_authors
        for record in records:
            if not record.metadata_authors:
                continue
            
            for author_part in record.metadata_authors.split(';'):
                author_part = author_part.strip()
                if not author_part or '.' in author_part:
                    continue
                
                # Парсить "Имя Фамилия" и преобразовать в "Фамилия Имя"
                parts = author_part.split()
                if len(parts) >= 2:
                    first_name = parts[0]
                    surname = parts[-1]
                    
                    surname_lower = surname.lower()
                    
                    full_name = f"{surname} {first_name}"
                    if surname_lower not in authors_map:
                        authors_map[surname_lower] = []
                    if full_name not in authors_map[surname_lower]:
                        authors_map[surname_lower].append(full_name)
        
        return authors_map
    
    def expand_abbreviated_authors(self, records) -> list:
        """
        Раскрыть сокращённых авторов типа "А.Фамилия" до полных имён.
        
        Args:
            records: Список BookRecord
        
        Returns:
            Обновленный список с раскрытыми авторами
        """
        # Построить словарь полных имён
        authors_map = self.build_authors_map(records)
        
        if not authors_map:
            self.logger.log("Словарь полных имён пуст, раскрытие невозможно")
            return records
        
        self.logger.log(f"Построен словарь из {len(authors_map)} фамилий для раскрытия")
        
        # Раскрыть аббревиатуры в каждой записи
        expanded_count = 0
        for record in records:
            if not record.proposed_author:
                continue
            
            authors_list = [a.strip() for a in record.proposed_author.split(',')]
            expanded_authors = []
            
            for author in authors_list:
                if not author:
                    continue
                
                # Если это аббревиатура (содержит точку), раскрыть её
                if '.' in author:
                    expanded = self.extractor.expand_abbreviated_author(author, authors_map)
                    if expanded != author:
                        expanded_count += 1
                        expanded_authors.append(expanded)
                    else:
                        expanded_authors.append(author)
                else:
                    # Попытаться расширить фамилию через metadata
                    expanded = self.extractor.expand_surname_to_fullname(author, record.metadata_authors)
                    if expanded != author:
                        expanded_count += 1
                        expanded_authors.append(expanded)
                    else:
                        expanded_authors.append(author)
            
            # Обновить proposed_author
            if expanded_authors:
                sorted_authors = []
                for author_str in expanded_authors:
                    if ',' in author_str:
                        authors_list = [a.strip() for a in author_str.split(',') if a.strip()]
                        authors_list.sort()
                        sorted_authors.append(", ".join(authors_list))
                    else:
                        sorted_authors.append(author_str)
                record.proposed_author = ", ".join(sorted_authors)
        
        self.logger.log(f"Раскрыто аббревиатур: {expanded_count}")
        return records
    
    def apply_author_consensus(self, records) -> list:
        """
        Применить консенсус при расхождениях авторов.
        
        Args:
            records: Список BookRecord
        
        Returns:
            Обновленный список с применённым консенсусом
        """
        consensus_count = 0
        
        # Построить отображение папок -> файлы
        folder_to_records = {}
        for record in records:
            file_path = Path(record.file_path)
            parent_folder = str(file_path.parent)
            
            if parent_folder not in folder_to_records:
                folder_to_records[parent_folder] = []
            folder_to_records[parent_folder].append(record)
        
        # Найти корневые папки датасета
        dataset_roots = set()
        for folder_path, records_in_folder in folder_to_records.items():
            has_dataset = any(r.author_source.startswith("folder_dataset") 
                            for r in records_in_folder)
            if has_dataset:
                dataset_roots.add(folder_path)
        
        # Для каждой корневой папки датасета - найти консенсус
        processed_folders = set()
        
        for root_folder in dataset_roots:
            if root_folder in processed_folders:
                continue
            
            all_root_records = []
            root_path = Path(root_folder)
            
            all_root_records.extend(folder_to_records.get(root_folder, []))
            
            for folder_path, records_in_folder in folder_to_records.items():
                folder_path_obj = Path(folder_path)
                try:
                    folder_path_obj.relative_to(root_path)
                    all_root_records.extend(records_in_folder)
                except ValueError:
                    pass
            
            # Найти консенсус среди folder_dataset файлов
            folder_dataset_records = [r for r in all_root_records 
                                      if r.author_source.startswith("folder_dataset")]
            
            if not folder_dataset_records:
                continue
            
            # Найти консенсус
            folder_authors = {}
            for record in folder_dataset_records:
                author = record.proposed_author
                if author:
                    folder_authors[author] = folder_authors.get(author, 0) + 1
            
            if not folder_authors:
                continue
            
            consensus_author = max(folder_authors.items(), key=lambda x: x[1])[0]
            consensus_author = self.extractor._normalize_author_format(consensus_author) if consensus_author else ""
            
            # ПРИМЕНИТЬ КОНСЕНСУС КО ВСЕМ файлам
            for record in all_root_records:
                if record.proposed_author == "Сборник":
                    continue
                
                if record.author_source.startswith("folder_dataset"):
                    continue
                
                if record.proposed_author == consensus_author:
                    continue
                
                record.author_source = "folder_dataset"
                record.proposed_author = consensus_author
                consensus_count += 1
            
            # Отметить папки как обработанные
            for folder_path in folder_to_records.keys():
                folder_path_obj = Path(folder_path)
                try:
                    folder_path_obj.relative_to(root_path)
                    processed_folders.add(folder_path)
                except ValueError:
                    pass
        
        return records
