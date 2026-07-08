"""
Модуль для обработки и извлечения информации об авторе из названия файла или пути.

Использует конфигурационные списки паттернов для поиска авторов в:
- Названиях файлов (author_series_patterns_in_files)
- Структурах папок (author_series_patterns_in_folders)
- Отдельных паттернах имён авторов (author_name_patterns)

Приоритет источников (от низшего к высшему):
1. FOLDER_STRUCTURE - структура папок
2. FILENAME - название файла
3. FB2_METADATA - метаданные FB2 файла
"""

import re
from typing import Optional, List, Dict, Tuple, Any

try:
    from settings_manager import SettingsManager
    from pattern_converter import compile_patterns
    from extraction_constants import (
        AuthorExtractionPriority,
        ConfidenceLevel,
        FilterReason,
        ExtractionResult
    )
except ImportError:
    from .settings_manager import SettingsManager
    from .pattern_converter import compile_patterns
    from .extraction_constants import (
        AuthorExtractionPriority,
        ConfidenceLevel,
        FilterReason,
        ExtractionResult
    )


class AuthorProcessor:
    """Класс для обработки и извлечения авторов из файлов и путей."""
    
    def __init__(self, config_path: str = 'config.json', folder_parse_limit: Optional[int] = None):
        """
        Инициализация процессора авторов.
        
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
        self.author_patterns = None
        self._load_patterns()
    
    def _load_patterns(self):
        """Загрузить паттерны из конфигурации и скомпилировать их."""
        # Загружаем паттерны для поиска в названиях файлов
        file_patterns_raw = self.settings.get_author_series_patterns_in_files()
        self.file_patterns = compile_patterns(file_patterns_raw) if file_patterns_raw else []
        
        # Загружаем паттерны для поиска в структурах папок
        folder_patterns_raw = self.settings.get_author_series_patterns_in_folders()
        self.folder_patterns = compile_patterns(folder_patterns_raw) if folder_patterns_raw else []
        
        # Загружаем паттерны для парсинга имён авторов
        author_patterns_raw = self.settings.get_author_name_patterns()
        self.author_patterns = compile_patterns(author_patterns_raw) if author_patterns_raw else []
    
    def extract_author_from_filename(self, filename: str) -> Optional[List[ExtractionResult]]:
        """
        Извлечь информацию об авторе из названия файла.
        
        Args:
            filename: Название файла (без расширения)
        
        Returns:
            Список результатов извлечения (может быть несколько совпадений)
            или None если ничего не найдено
        """
        if not filename or not self.file_patterns:
            return None
        
        results = []
        
        # Применить все паттерны файлов
        for pattern_index, pattern_tuple in enumerate(self.file_patterns):
            # pattern_tuple = (pattern_string, compiled_regex, group_names)
            pattern_str, pattern_regex, group_names = pattern_tuple
            match = pattern_regex.search(filename)
            if match:
                # Попытаться получить группу 'author'
                try:
                    author_value = match.group('author')
                    if author_value:
                        # Проверить черный список
                        is_blacklisted, reasons = self._is_blacklisted(author_value)
                        if not is_blacklisted:
                            result = ExtractionResult(
                                value=author_value,
                                priority=AuthorExtractionPriority.FILENAME,
                                confidence=0.70,
                                pattern_used=pattern_str,
                                pattern_index=pattern_index
                            )
                            results.append(result)
                except (IndexError, AttributeError):
                    pass
        
        return results if results else None
    
    def extract_author_from_filepath(self, filepath: str) -> Optional[List[ExtractionResult]]:
        """
        Извлечь информацию об авторе из пути файла (анализ структуры папок).
        
        Логика: идти от папки файла ВВЕРХ на folder_parse_limit уровней.
        Например, для /home/user/books/Isaac_Asimov/Stories/Foundation.fb2
        с folder_parse_limit=3 проверяем: Stories -> Isaac_Asimov -> books
        
        Args:
            filepath: Полный путь к файлу
        
        Returns:
            Список результатов извлечения или None
        """
        if not filepath or not self.folder_patterns:
            return None
        
        results = []
        
        # Получить родительскую папку файла
        from pathlib import Path
        file_path = Path(filepath)
        parent_path = file_path.parent
        
        # Идти вверх на folder_parse_limit уровней
        parse_limit = self.folder_parse_limit or 5
        current_path = parent_path
        
        for level in range(parse_limit):
            # Получить имя папки
            folder_name = current_path.name
            
            # Пропустить пустые имена и папки "."
            if not folder_name or folder_name == '.':
                # Попробовать подняться еще выше
                if current_path.parent != current_path:  # Не корневая папка
                    current_path = current_path.parent
                    continue
                else:
                    break  # Достигли корня
            
            # Применить все паттерны папок к названию папки
            for pattern_index, pattern_tuple in enumerate(self.folder_patterns):
                # pattern_tuple = (pattern_string, compiled_regex, group_names)
                pattern_str, pattern_regex, group_names = pattern_tuple
                match = pattern_regex.search(folder_name)
                if match:
                    # Попытаться получить группу 'author'
                    try:
                        author_value = match.group('author')
                        if author_value:
                            # Проверить черный список
                            is_blacklisted, reasons = self._is_blacklisted(author_value)
                            if not is_blacklisted:
                                result = ExtractionResult(
                                    value=author_value,
                                    priority=AuthorExtractionPriority.FOLDER_STRUCTURE,
                                    confidence=0.65,
                                    pattern_used=pattern_str,
                                    pattern_index=pattern_index
                                )
                                results.append(result)
                                # Остановиться на первом найденном совпадении в папке
                                break
                    except (IndexError, AttributeError):
                        pass
            
            # Если нашли результат, остановиться
            if results:
                break
            
            # Подняться на уровень выше
            if current_path.parent != current_path:  # Не корневая папка
                current_path = current_path.parent
            else:
                break  # Достигли корня
            
            # Если нашли результат в этой папке, больше не ищем
            if results:
                break
        
        return results if results else None
    
    def parse_author_name(self, author_string: str) -> Optional[Dict[str, Any]]:
        """
        Разобрать строку с именем автора используя конфигурационные паттерны.
        
        Args:
            author_string: Строка с именем автора
        
        Returns:
            Словарь с компонентами имени автора или None
            Структура: {
                'full_name': str,        # Полное имя
                'first_name': str,       # Имя (если извлечено)
                'last_name': str,        # Фамилия (если извлечено)
                'initials': str,         # Инициалы (если есть)
                'pattern': str,          # Использованный паттерн
                'groups': dict           # Все извлечённые группы
            }
        """
        # TODO: Реализовать логику парсинга имени автора
        # - Применить паттерны author_patterns
        # - Извлечь компоненты имени
        # - Нормализовать регистр (используя abbreviations_preserve_case)
        # - Вернуть структурированный результат
        pass
    
    def extract_author_combined(self, filename: str, filepath: str) -> Optional[Dict[str, Any]]:
        """
        Попытаться извлечь автора, комбинируя различные методы.
        
        Args:
            filename: Название файла
            filepath: Полный путь к файлу
        
        Returns:
            Словарь с наиболее вероятным вариантом автора
        """
        # TODO: Реализовать комбинированную логику
        # - Попробовать extract_author_from_filename
        # - Попробовать extract_author_from_filepath
        # - Выбрать результат с наибольшей уверенностью
        # - Вернуть результат с указанием источника
        pass
    
    def _merge_with_priority(self, results_by_priority: Dict[int, List[ExtractionResult]]) -> Optional[Dict[str, Any]]:
        """
        Слить результаты с учетом приоритетов.
        
        Args:
            results_by_priority: Словарь {priority: [ExtractionResult, ...]}
        
        Returns:
            Финальный результат или None
        """
        # TODO: Реализовать логику слияния
        # - Пройти по AuthorExtractionPriority.ORDER от начала к концу
        # - Для каждого приоритета взять результаты с наивысшей уверенностью
        # - Первый найденный - финальный, остальные - альтернативы
        # - Вернуть структурированный результат
        pass
    
    def _is_blacklisted(self, value: str) -> Tuple[bool, List[str]]:
        """
        Проверить, содержится ли значение в черном списке.
        
        Args:
            value: Значение для проверки
        
        Returns:
            (is_blacklisted, reasons) - флаг и список причин совпадения
        """
        if not value:
            return False, []
        
        try:
            blacklist = self.settings.get_filename_blacklist()
            if not blacklist:
                return False, []
            
            value_lower = value.lower()
            reasons = []
            
            for item in blacklist:
                item_lower = item.lower()
                
                # Точное совпадение
                if value_lower == item_lower:
                    reasons.append(f"exact_match: {item}")
                    return True, reasons
                
                # Совпадение подстроки (если слово целиком содержится)
                if item_lower in value_lower:
                    reasons.append(f"substring_match: {item}")
                    return True, reasons
            
            return False, reasons
        except Exception:
            return False, []
    
    def reload_patterns(self):
        """Перезагрузить паттерны из конфигурации."""
        self._load_patterns()


if __name__ == '__main__':
    # Простой тест
    processor = AuthorProcessor()
    print("AuthorProcessor инициализирован")
    print(f"Паттерны в файлах: {len(processor.file_patterns)}")
    print(f"Паттерны в папках: {len(processor.folder_patterns)}")
    print(f"Паттерны для имён: {len(processor.author_patterns)}")
    print(f"Предел папок при парсинге: {processor.folder_parse_limit}")
    print()
    print(f"Приоритеты извлечения авторов:")
    for priority in AuthorExtractionPriority.ORDER:
        print(f"  {priority}: {AuthorExtractionPriority.get_name(priority)}")

