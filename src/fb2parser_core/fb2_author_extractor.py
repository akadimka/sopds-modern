"""
Модуль для парсинга FB2 файлов и извлечения информации об авторах.

Реализует многоуровневую стратегию извлечения авторов:
1. Структура папок (FOLDER_STRUCTURE)
2. Название файла (FILENAME)
3. Метаданные FB2 (FB2_METADATA)

Использует приоритезацию из extraction_constants.AuthorExtractionPriority
"""

import xml.etree.ElementTree as ET
import re
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

try:
    from author_processor import AuthorProcessor
    from extraction_constants import (
        AuthorExtractionPriority,
        ConfidenceLevel,
        FilterReason,
        ExtractionResult
    )
    from settings_manager import SettingsManager
except ImportError:
    from .author_processor import AuthorProcessor
    from .extraction_constants import (
        AuthorExtractionPriority,
        ConfidenceLevel,
        FilterReason,
        ExtractionResult
    )
    from .settings_manager import SettingsManager


class FB2AuthorExtractor:
    """Извлечение информации об авторах из FB2 файлов."""
    
    def __init__(self, config_path: str = 'config.json'):
        """
        Инициализация экстрактора авторов FB2.
        
        Args:
            config_path: Путь к файлу конфигурации
        """
        self.settings = SettingsManager(config_path)
        self.author_processor = AuthorProcessor(config_path)
        
        # Загрузить списки имен для определения порядка слов
        self.male_names = set(name.lower() for name in self.settings.get_male_names())
        self.female_names = set(name.lower() for name in self.settings.get_female_names())
        
        # Загрузить список аббревиатур для исключения из составных фамилий
        self.abbreviations_preserve_case = set(
            self.settings.settings.get('abbreviations_preserve_case', [])
        )
        
        # Маркеры сборников/антологий из конфига
        self.anthology_markers = self.settings.get_list('collection_keywords')
    
    def resolve_author_by_priority(
        self,
        fb2_filepath: str,
        folder_parse_limit: int = 3,
        all_series_authors_str: str = ""
    ) -> Tuple[str, str]:
        """
        Простой метод для получения автора по приоритетам источников.
        
        Приоритет извлечения в зависимости от folder_parse_limit:
        - folder_parse_limit > 0: папка → файл → метаданные (структурированное хранилище)
        - folder_parse_limit == 0: файл → метаданные (неструктурированное, разные авторы в папке)
        
        Для источников 1 и 2 используется fuzzy matching для верификации:
        - Кандидат сравнивается с авторами из метаданных
        - Если похожесть > 70%, принимается
        - Если не похож ни на кого в метаданных, отклоняется
        
        Правило множественных авторов:
        - Если авторов <= 2: берем имена
        - Если авторов > 2: возвращаем "Соавторство"
        
        Args:
            fb2_filepath: Полный путь к FB2 файлу
            folder_parse_limit: Глубина парсинга папок (int):
                - 0: не парсим папки вообще (приоритет: файл → метаданные)
                - N>0: парсим максимум N уровней вверх (приоритет: папка → файл → метаданные)
            all_series_authors_str: Строка со всеми авторами из всех файлов серии/папки (для расширения)
        
        Returns:
            (author_name, source) где source in ['folder_dataset', 'folder', 'filename', 'metadata', '']
            Если ничего не найдено, возвращает ('', '')
        """
        try:
            fb2_path = Path(fb2_filepath)
            
            # Получить авторов из метаданных один раз (источник истины)
            metadata_author = self._extract_author_from_metadata(fb2_path)
            
            # 1. Попытка получить автора из структуры папок (если folder_parse_limit > 0)
            if folder_parse_limit > 0:
                # folder_parse_limit > 0: парсим папки на N уровней (систематическая структура - folder_dataset)
                try:
                    author = self._extract_author_from_folder_structure(fb2_path, folder_parse_limit)
                    if author:
                        # Проверить: если в папке указано несколько авторов (запятая), не проверять против метаданных
                        # Если это список авторов, мы ему доверяем  
                        if ',' in author or ';' in author:
                            # Это список авторов из папки - принимаем как есть без верификации
                            # (Расширение аббревиатур произойдёт позже в RegenCSVService._expand_abbreviated_authors)
                            author = self._normalize_author_count(author)
                            author = self._normalize_author_format(author) if author else ""
                            if not author:
                                # Если нормализация не сработала, доверяем исходному списку
                                author = " ".join(self._extract_author_from_folder_structure(fb2_path, folder_parse_limit).split())
                            if author:
                                return author, 'folder_dataset'  # ИСПРАВЛЕНО: folder_dataset вместо folder
                        elif self._verify_author_against_metadata(author, metadata_author):
                            # Одиночный автор - проверяем против метаданных
                            author = self._normalize_author_count(author)
                            author = self._normalize_author_format(author)
                            if author:
                                return author, 'folder_dataset'  # ИСПРАВЛЕНО: folder_dataset вместо folder
                except Exception as e:
                    pass  # Продолжаем к следующему источнику
            
            # 2. Попытка получить автора из имени файла
            try:
                author = self._extract_author_from_filename(fb2_path)
                if author:
                    # Получить ВСЕ авторов из метаданных для потенциального расширения
                    all_metadata_authors = self._extract_all_authors_from_metadata(fb2_path)
                    
                    # КЛЮЧЕВАЯ ПРОВЕРКА: Если есть несколько авторов (разделены запятой или точкой с запятой)
                    # обработать их сразу (как для явного паттерна в скобках, так и для авторов в имени файла)
                    if ',' in author or ';' in author:
                        # Несколько авторов - обработать каждого
                        separator = ',' if ',' in author else ';'
                        authors_list = [a.strip() for a in author.split(separator) if a.strip()]
                        expanded_authors = []
                        
                        for single_author in authors_list:
                            # Для каждого автора: проверить если это полное имя или попробовать расширить
                            words = single_author.split()
                            is_full_name = len(words) >= 2 and all(word.replace('.', '').replace('-', '').replace('ё', 'е').replace('Ё', 'Е').isalpha() for word in words)
                            
                            if is_full_name:
                                # Полное имя - нормализуем
                                normalized = self._normalize_author_format(single_author)
                                if normalized:
                                    expanded_authors.append(normalized)
                                else:
                                    expanded_authors.append(single_author)
                            else:
                                # Сокращение или одно слово - пытаемся расширить
                                expanded_single = ""
                                
                                # Попытка 1: поиск по частичному имени в серии
                                if all_series_authors_str:
                                    expanded_single = self._find_author_by_partial_name(single_author, all_series_authors_str)
                                
                                # Попытка 2: поиск в метаданных файла
                                if not expanded_single and all_metadata_authors:
                                    expanded_single = self._find_author_by_partial_name(single_author, all_metadata_authors)
                                
                                # Попытка 3: расширение фамилий в серии
                                if not expanded_single and all_series_authors_str:
                                    expanded_single = self._expand_surnames_from_metadata(single_author, all_series_authors_str)
                                
                                # Попытка 4: расширение фамилий в метаданных файла
                                if not expanded_single and all_metadata_authors:
                                    expanded_single = self._expand_surnames_from_metadata(single_author, all_metadata_authors)
                                
                                if expanded_single:
                                    expanded_authors.append(expanded_single)
                                else:
                                    expanded_authors.append(single_author)
                        
                        # Нормализовать и вернуть список
                        result_str = ", ".join(expanded_authors)
                        normalized = self._normalize_author_format(result_str)
                        if normalized:
                            return normalized, 'filename'
                        else:
                            return result_str, 'filename'
                    
                    # Если это один автор - проверить если он полный
                    words = author.split()
                    is_full_name = len(words) >= 2 and all(word.replace('.', '').replace('-', '').replace('ё', 'е').replace('Ё', 'Е').isalpha() for word in words)
                    
                    if is_full_name:
                        # Автор уже полный - нормализуем и возвращаем как есть
                        normalized = self._normalize_author_format(author)
                        if normalized:
                            return normalized, 'filename'
                        else:
                            # Нормализация не сработала - возвращаем исходный
                            return author, 'filename'
                    
                    # Если это сокращение/одно слово, пытаемся расширить из метаданных
                    expanded_author = ""
                    
                    # Попытка 1: новый метод поиска по частичному имени - СНАЧАЛА ищем во всей серии
                    if all_series_authors_str:
                        expanded_author = self._find_author_by_partial_name(author, all_series_authors_str)
                    
                    # Попытка 2: ищем в metadata этого файла
                    if not expanded_author and all_metadata_authors:
                        expanded_author = self._find_author_by_partial_name(author, all_metadata_authors)
                    
                    # Попытка 3: старый метод расширения фамилий в metadata серии
                    if not expanded_author and all_series_authors_str:
                        expanded_author = self._expand_surnames_from_metadata(author, all_series_authors_str)
                    
                    # Попытка 4: старый метод расширения фамилий в metadata этого файла
                    if not expanded_author and all_metadata_authors:
                        expanded_author = self._expand_surnames_from_metadata(author, all_metadata_authors)
                    
                    # Попытка 5: старый метод с одиночным metadata автором
                    if not expanded_author and metadata_author:
                        expanded_author = self._expand_surnames_from_metadata(author, metadata_author)
                    
                    if expanded_author:
                        normalized = self._normalize_author_format(expanded_author)
                        if normalized:
                            return normalized, 'filename'
                        else:
                            return expanded_author, 'filename'
                    else:
                        return author, 'filename'
            
            except Exception as e:
                pass  # Продолжаем к следующему источнику
            
            # 3. Попытка получить автора из метаданных FB2
            try:
                author = self._extract_author_from_metadata(fb2_path)
                if author:
                    metadata_author = author  # Сохранить для потенциального использования позже
                    
                    # Нормализовать автора из метаданных
                    normalized = self._normalize_author_format(author)
                    if normalized:
                        return normalized, 'metadata'
                    else:
                        return author, 'metadata'
            except Exception as e:
                pass  # Продолжаем к следующему источнику
            
            # Если ничего не получилось - вернуть пустое значение
            return '', ''
        except Exception as e:
            return '', ''
    
    def _normalize_author_format(self, author_string: str) -> str:
        """
        Нормализовать формат автора/авторов.
        
        Правила:
        1. Если "Соавторство", оставить как есть
        2. Если один автор, нормализовать в формат "Фамилия Имя"
        3. Если два автора, нормализовать каждого и отсортировать по алфавиту
        
        Нормализация ФИ:
        - Взять максимум 2 слова (игнорировать отчество и прочее)
        - Между ними только один пробел
        - Дефис допускается в составных именах/фамилиях
        - Конвертация ё -> е для нормализации
        
        Args:
            author_string: Строка с одним или несколькими авторами
        
        Returns:
            Нормализованная строка
        """
        if not author_string or author_string == "Соавторство":
            return author_string
        
        try:
            # Конвертировать ё -> е для нормализации
            author_string = author_string.replace('ё', 'е')
            
            # Проверить есть ли несколько авторов (разделены ; или ,)
            authors_list = []
            separator = None
            
            if ';' in author_string:
                separator = ';'
                authors_list = [a.strip() for a in author_string.split(';') if a.strip()]
            elif ',' in author_string:
                separator = ','
                authors_list = [a.strip() for a in author_string.split(',') if a.strip()]
            
            if not authors_list:
                # Нет разделителей - один автор
                authors_list = [author_string.strip()]
            
            # Нормализовать каждого автора
            normalized_authors = []
            for author in authors_list:
                normalized = self._normalize_single_author(author)
                if normalized and normalized != "Соавторство":
                    normalized_authors.append(normalized)
                elif normalized == "Соавторство":
                    return "Соавторство"
            
            if not normalized_authors:
                return ""
            
            # Если авторов > 2 после нормализации
            if len(normalized_authors) > 2:
                return "Соавторство"
            
            # Отсортировать по алфавиту
            normalized_authors.sort()
            
            # Объединить запятой для нескольких авторов
            if len(normalized_authors) > 1:
                return ", ".join(normalized_authors)
            else:
                return normalized_authors[0]
        
        except Exception:
            return author_string
    
    def _normalize_single_author(self, author_name: str) -> str:
        """
        Нормализовать одного автора в формат "Фамилия Имя".
        
        Правила:
        - Результат должен быть ровно 2 слова (Фамилия Имя)
        - Каждое слово должно начинаться с большой буквы
        - Между словами только один пробел
        - Допускаются дефисы в составных именах/фамилиях
        - Порядок определяется по списку имен: если одно из слов есть в списке - оно Имя, другое - Фамилия
        - Обработка аббревиатур типа "А.Фамилия" (оставить как есть)
        - Конвертация ё -> е для нормализации
        
        Args:
            author_name: Имя автора (может быть в разных форматах)
        
        Returns:
            Нормализованное имя вида "Фамилия Имя" или пустая строка
        """
        if not author_name or author_name == "Соавторство":
            return author_name
        
        try:
            # Конвертировать ё -> е для нормализации
            author_name = author_name.replace('ё', 'е')
            
            # Убрать лишние пробелы
            author_name = " ".join(author_name.split())
            
            # Проверить: если это аббревиатура типа "А.Фамилия", оставить как есть
            if '.' in author_name:
                # Это может быть аббревиатура - проверим
                parts = author_name.split()
                if len(parts) == 2 and parts[0].endswith('.') and len(parts[0]) <= 3:
                    # Формат "А.Фамилия" или "А.B.Фамилия"
                    return author_name
            
            # Разбить на слова
            words = author_name.split()
            
            # Если одно слово и оно есть в списке male_names или female_names, разрешить
            if len(words) == 1:
                word_lower = words[0].lower()
                if word_lower in self.male_names or word_lower in self.female_names:
                    return words[0]
                # Можно добавить отдельный список псевдонимов, если нужно
                return ""
            # Нужно ровно 2 слова для обычных случаев
            if len(words) != 2:
                return ""
            
            # Проверить что каждое слово корректное
            cleaned_words = []
            for word in words:
                # Отбросить цифры и специальные символы в конце
                # Оставить только буквы, дефисы
                clean_word = ""
                for char in word:
                    if char.isalpha() or char == '-':
                        clean_word += char
                    else:
                        break  # Остановиться при первом некорректном символе
                
                if not clean_word:
                    return ""  # Слово не содержит букв - отбросить
                
                # Проверить что начинается с большой буквы
                if not clean_word[0].isupper():
                    return ""
                
                cleaned_words.append(clean_word)
            
            if len(cleaned_words) != 2:
                return ""
            
            # Определить порядок слов на основе списка имен
            word1_lower = cleaned_words[0].lower()
            word2_lower = cleaned_words[1].lower()
            
            word1_is_name = word1_lower in self.male_names or word1_lower in self.female_names
            word2_is_name = word2_lower in self.male_names or word2_lower in self.female_names

            # Если оба или ни один не в списке имен - оставить как есть
            if word1_is_name and not word2_is_name:
                # Первое слово - имя, второе - фамилия
                # Нужно переставить: фамилия имя
                return f"{cleaned_words[1]} {cleaned_words[0]}"
            elif not word1_is_name and word2_is_name:
                # Первое слово - фамилия, второе - имя (уже правильный порядок)
                return f"{cleaned_words[0]} {cleaned_words[1]}"
            else:
                # Оба в списке имен или оба не в списке - оставить как есть
                return f"{cleaned_words[0]} {cleaned_words[1]}"
        
        except Exception:
            return ""
    
    def _normalize_author_count(self, author_string: str) -> str:
        """
        Нормализовать количество авторов и формат.
        
        Правило:
        - Если авторов <= 2: нормализует формат и возвращает
        - Если авторов > 2: возвращает "Соавторство"
        
        Авторы разделены символом ';' или ','
        Конвертация ё -> е для нормализации
        
        Args:
            author_string: Строка с одним или несколькими авторами
        
        Returns:
            Нормализованная строка или "Соавторство"
        """
        if not author_string:
            return ""
        
        try:
            # Конвертировать ё -> е для нормализации
            author_string = author_string.replace('ё', 'е')
            
            # Разбить авторов по разделителям
            authors = []
            for sep in [';', ',']:
                if sep in author_string:
                    authors = [a.strip() for a in author_string.split(sep) if a.strip()]
                    break
            
            # Если разделителей не найдено - это один автор
            if not authors:
                authors = [author_string.strip()]
            
            # Если авторов > 2, то "Соавторство"
            if len(authors) > 2:
                return "Соавторство"
            
            # Если авторов > 1, нужно нормализовать каждого
            if len(authors) > 1:
                normalized_authors = []
                for author in authors:
                    normalized = self._normalize_author_format(author)
                    if not normalized and author:
                        # Если формальная нормализация не сработала,
                        # но автор содержит точку (вероятно аббревиатура),
                        # оставляем как есть
                        normalized = author.strip()
                    if normalized:
                        normalized_authors.append(normalized)
                
                if normalized_authors:
                    return ", ".join(normalized_authors)
                else:
                    # Если ничего не нормализовалось, оставляем исходное
                    return author_string
            
            # Один автор - нормализовать и вернуть
            return self._normalize_author_format(author_string)
        
        except Exception:
            return author_string
    
    def _verify_author_against_metadata(
        self, 
        candidate_author: str, 
        metadata_author: str
    ) -> bool:
        """
        Проверить, похож ли кандидат на автора из метаданных.
        
        Использует несколько стратегий:
        1. Полное совпадение строк (100%)
        2. Проверка что хотя бы одно слово из metadata есть в candidate
        3. Fuzzy matching для похожести (70%)
        
        Если метаданные пусты, кандидат отклоняется.
        
        Args:
            candidate_author: Предполагаемый автор из папки/имени файла
            metadata_author: Автор из метаданных FB2
        
        Returns:
            True если автор подтверждается, False иначе
        """
        if not candidate_author or not metadata_author:
            return False
        
        try:
            from difflib import SequenceMatcher
            
            # Нормализовать строки для сравнения
            cand_lower = candidate_author.lower().strip()
            meta_lower = metadata_author.lower().strip()
            
            # 1. Проверить полное совпадение
            if cand_lower == meta_lower:
                return True
            
            # 2. Проверить что хотя бы одно слово из metadata есть в candidate
            # Это помогает при разном порядке слов: "Иван Петров" vs "Петров Иван"
            meta_words = set(meta_lower.split())
            cand_words = set(cand_lower.replace(',', ' ').split())  # Убрать запятые (для списков авторов)
            
            # Если найдено хотя бы 50% слов из метаданных в кандидате, это хороший знак
            if meta_words and cand_words:
                overlap = len(meta_words & cand_words) / len(meta_words)
                if overlap >= 0.5:  # Хотя бы половина слов совпадает
                    return True
            
            # 3. Fuzzy matching для последней проверки
            similarity = SequenceMatcher(None, cand_lower, meta_lower).ratio()
            if similarity >= 0.70:
                return True
            
            return False
        except Exception:
            return False

    def extract_all_authors(
        self,
        fb2_filepath: str,
        apply_priority: bool = True
    ) -> Dict[str, Any]:
        """
        Комбинированное извлечение авторов из всех источников.
        
        Args:
            fb2_filepath: Полный путь к FB2 файлу
            apply_priority: Применять ли приоритизацию результатов
        
        Returns:
            Структурированный результат с авторами и метаданными:
            {
                'primary_author': {
                    'name': str,
                    'priority': int,
                    'source': str,
                    'confidence': float,
                    ...
                },
                'alternative_authors': [...],
                'all_results_by_priority': {
                    priority: [ExtractionResult, ...],
                    ...
                },
                'processing_info': {
                    'fb2_path': str,
                    'file_name': str,
                    'folder_path': str,
                    ...
                }
            }
        """
        # TODO: Реализовать полный процесс извлечения
        # 1. Получить информацию о пути к файлу
        # 2. Вызвать extract_from_folder_structure()
        # 3. Вызвать extract_from_filename()
        # 4. Вызвать extract_from_fb2_metadata()
        # 5. Слить результаты используя merge_results_by_priority()
        # 6. Вернуть структурированный результат
        pass
    
    def extract_from_folder_structure(self, fb2_filepath: str) -> List[ExtractionResult]:
        """
        Извлечение авторов из структуры папок.
        
        Приоритет: AuthorExtractionPriority.FOLDER_STRUCTURE (1)
        
        Args:
            fb2_filepath: Полный путь к FB2 файлу
        
        Returns:
            Список результатов (может быть пуст если ничего не найдено)
        """
        # TODO: Реализовать извлечение из структуры папок
        # - Получить папку файла
        # - Применить author_processor.extract_author_from_filepath()
        # - Проверить результаты против filename_blacklist
        # - Вернуть список ExtractionResult с приоритетом FOLDER_STRUCTURE
        pass
    
    def extract_from_filename(self, fb2_filepath: str) -> List[ExtractionResult]:
        """
        Извлечение авторов из названия файла.
        
        Приоритет: AuthorExtractionPriority.FILENAME (2)
        
        Args:
            fb2_filepath: Полный путь к FB2 файлу
        
        Returns:
            Список результатов (может быть пуст если ничего не найдено)
        """
        # TODO: Реализовать извлечение из названия файла
        # - Получить имя файла без расширения
        # - Применить author_processor.extract_author_from_filename()
        # - Проверить результаты против filename_blacklist
        # - Вернуть список ExtractionResult с приоритетом FILENAME
        pass
    
    def extract_from_fb2_metadata(self, fb2_filepath: str) -> List[ExtractionResult]:
        """
        Извлечение авторов из метаданных FB2 файла.
        
        Приоритет: AuthorExtractionPriority.FB2_METADATA (3)
        
        Args:
            fb2_filepath: Полный путь к FB2 файлу
        
        Returns:
            Список результатов (может быть пуст если ничего не найдено)
        """
        # TODO: Реализовать извлечение из метаданных FB2
        # - Прочитать и спарсить XML FB2 файла
        # - Найти раздел <description>/<title-info>/<author>
        # - Извлечь first-name, last-name, nickname
        # - Применить автора_processor для нормализации
        # - Проверить против filename_blacklist
        # - Вернуть список ExtractionResult с приоритетом FB2_METADATA
        pass
    
    def merge_results_by_priority(
        self,
        results_by_priority: Dict[int, List[ExtractionResult]]
    ) -> Tuple[Optional[ExtractionResult], List[ExtractionResult]]:
        """
        Слить результаты с учетом приоритетов.
        
        Логика:
        - Итерировать по AuthorExtractionPriority.ORDER
        - Первый найденный результат (не отфильтрованный) становится основным
        - Остальные результаты становятся альтернативами
        - Результаты с более низким приоритетом идут в конец
        
        Args:
            results_by_priority: Словарь {priority: [ExtractionResult, ...]}
        
        Returns:
            (primary_result, alternative_results)
        """
        # TODO: Реализовать слияние по приоритетам
        # - Пройти по AuthorExtractionPriority.ORDER
        # - Найти первый не отфильтрованный результат
        # - Этот становится основным
        # - Остальные - альтернативы
        pass
    
    def _apply_blacklist_filter(
        self,
        value: str
    ) -> Tuple[bool, List[str]]:
        """
        Применить фильтр черного списка к значению.
        
        Args:
            value: Значение для фильтрации
        
        Returns:
            (is_filtered, reasons) - был ли отфильтрован и почему
        """
        # TODO: Реализовать фильтрацию
        # - Получить filename_blacklist из конфигурации
        # - Проверить точное совпадение (case-insensitive)
        # - Проверить совпадение подстроки
        # - Вернуть результат
        pass
    
    def _extract_author_from_folder_structure(self, fb2_path: Path, folder_parse_limit: int = 3) -> str:
        """
        Извлечь автора из структуры папок.
        
        Ищет имя автора в названии папки, поднимаясь вверх на folder_parse_limit уровней.
        Останавливается на первой папке, где найдены авторы.
        """
        try:
            # Поднимаемся вверх по folder_parse_limit уровней, ищем авторов в названиях папок
            current_path = fb2_path.parent
            for level in range(folder_parse_limit):
                if current_path.parts:
                    folder_name = current_path.name
                    
                    # Паттерн: "(Author)" где может быть несколько авторов
                    import re
                    bracket_patterns = [
                        r'\(([^)]+)\)(?:\s*$|\s+[-–])',  # В конце или перед дефисом: (Author) - или (Author)
                        r'(?:^|\s+)(?:[-–]\s+)?\(([^)]+)\)',  # В начале или после дефиса: - (Author)
                    ]
                    
                    for pattern in bracket_patterns:
                        match = re.search(pattern, folder_name)
                        if match:
                            author_candidate = match.group(1).strip()
                            if author_candidate and not self._is_blacklisted_value(author_candidate):
                                return author_candidate
                    
                    # Поднимаемся на один уровень вверх
                    parent = current_path.parent
                    if parent == current_path:  # Достигли корня
                        break
                    current_path = parent
            
            # Если парсинг скобок в папках не сработал, использовать author_processor
            folder_path = str(fb2_path.parent)
            result = self.author_processor.extract_author_from_filepath(folder_path)
            if result:
                # Результат - список ExtractionResult, берем первый
                author_name = result[0].value if hasattr(result[0], 'value') else str(result[0])
                if author_name:
                    return author_name
        except Exception as e:
            pass
        
        return ''
    
    def _extract_author_from_folder_structure_with_limit(self, fb2_path: Path, limit_folder: Path) -> str:
        """
        Извлечь автора из структуры папок с ограничением до определённой папки.
        
        Ищет автора от файла вверх до папки limit_folder, но не включает саму папку.
        
        Args:
            fb2_path: Путь к FB2 файлу
            limit_folder: Папка, до которой парсить (не включая её)
        
        Returns:
            Имя автора или пустая строка
        """
        try:
            import re
            # Получить путь к файлу
            current_path = fb2_path.parent
            limit_path = limit_folder
            
            # Идем вверх по папкам от файла до лимита
            while current_path != limit_path and current_path != current_path.parent:
                folder_name = current_path.name
                
                # Попытаться прямого парсинга скобок в названии папки
                bracket_patterns = [
                    r'\(([^)]+)\)(?:\s*$|\s+[-–])',  # В конце или перед дефисом
                    r'(?:^|\s+)(?:[-–]\s+)?\(([^)]+)\)',  # В начале или после дефиса
                ]
                
                author_found = False
                for pattern in bracket_patterns:
                    match = re.search(pattern, folder_name)
                    if match:
                        author_candidate = match.group(1).strip()
                        if author_candidate and not self._is_blacklisted_value(author_candidate):
                            return author_candidate
                
                # Если прямой парсинг не дал результата, использовать author_processor
                result = self.author_processor.extract_author_from_filename(folder_name)
                if result:
                    author_name = result[0].value if hasattr(result[0], 'value') else str(result[0])
                    if author_name:
                        return author_name
                
                # Поднимаемся на уровень выше
                current_path = current_path.parent
        except Exception as e:
            pass
        
        return ''
    
    def _extract_author_from_filename(self, fb2_path: Path) -> str:
        """
        Извлечь автора из названия файла.
        
        ПРАВИЛЬНАЯ ЛОГИКА: Проверять каждое полученное значение на валидность.
        Если значение в черном списке или не выглядит как имя автора - отбрасывать.
        
        ПОРЯДОК ПОПЫТОК:
        1. СНАЧАЛА: Проверить явный автор в скобках "(Автор)" в конце - это самый явный маркер!
        2. Попробовать динамический pattern matching
        3. Если получилось невалидное значение - отбросить
        4. Если скобки содержат невалидное значение - отбросить  
        5. Fallback на author_processor
        """
        try:
            # Убедиться, что fb2_path это Path объект (может быть передана строка)
            if isinstance(fb2_path, str):
                fb2_path = Path(fb2_path)
            
            filename = fb2_path.stem  # Имя без расширения
            
            # ПОПЫТКА 1 (ПРИОРИТЕТ): Проверить явный автор в скобках "(Автор)" в конце имени файла
            # Это ЯВНЫЙ маркер, поэтому проверяем его ДО pattern matching
            bracket_patterns = [
                r'\(([^)]+)\)(?:\s*$)',  # В конце: "... (Автор)"
            ]
            
            for pattern in bracket_patterns:
                match = re.search(pattern, filename)
                if match:
                    author_candidate = match.group(1).strip()
                    # Проверить валидность кандидата
                    if author_candidate and self._is_valid_author_candidate(author_candidate):
                        return author_candidate
                    # Если невалидный - продолжаем поиск
            
            # ПОПЫТКА 2: Использовать динамический pattern matching
            pattern_dict = self._select_best_pattern(filename, pattern_type='files')
            
            if pattern_dict:
                # Найден подходящий паттерн - извлечь автора
                author = self._extract_author_from_filename_with_pattern(filename, pattern_dict)
                if author and self._is_valid_author_candidate(author):
                    # Получено ВАЛИДНОЕ значение из паттерна
                    # Если содержит инициалы "А.Фамилия", попробовать расширить из метаданных
                    if re.match(r'^[А-Яа-я]\.[А-Яа-я]', author):
                        all_metadata_authors = self._extract_all_authors_from_metadata(fb2_path)
                        if all_metadata_authors:
                            # Попробовать найти совпадение по фамилии
                            surname = author.split('.')[-1]
                            if surname.lower() in all_metadata_authors.lower():
                                # Нашли соответствие по фамилии
                                authors_list = all_metadata_authors.split('; ')
                                for auth in authors_list:
                                    if surname.lower() in auth.lower():
                                        return auth
                                return all_metadata_authors
                    return author
                # Если значение невалидное, не возвращаем, продолжаем к следующему источнику
            
            # ПОПЫТКА 3: Fallback на author_processor
            result = self.author_processor.extract_author_from_filename(filename)
            if result:
                author_name = result[0].value if hasattr(result[0], 'value') else str(result[0])
                if author_name and self._is_valid_author_candidate(author_name):
                    return author_name
        except Exception as e:
            pass
        
        return ''
    
    def _is_valid_author_candidate(self, value: str) -> bool:
        """
        Проверить, выглядит ли значение как имя автора.
        
        СТРОГАЯ ВАЛИДАЦИЯ:
        1. Черный список (том, часть, выпуск, сборник и т.д.)
        2. Разумная длина (2-100 символов)
        3. Буквы (не только цифры)
        4. Не в UPPERCASE целиком
        5. ГЛАВНОЕ: Должно содержать известные авторские слова ИЛИ выглядеть как структурное имя (2+ слова)
           но НЕ выглядеть как название серии/описания
        
        Args:
            value: Значение для проверки
            
        Returns:
            True если похоже на имя автора, False иначе
        """
        if not value:
            return False
        
        # Проверка 1: Черный список (название книг, томы и т.д.)
        if self._is_blacklisted_value(value):
            return False
        
        # Проверка 2: Должна быть хотя бы одна буква
        if not any(c.isalpha() for c in value):
            return False
        
        # Проверка 3: Не может быть всё в UPPERCASE (похоже на географическое название, аббревиатуру)
        if value.isupper() and len(value) > 1:
            return False
        
        # Проверка 4: Разумная длина
        if len(value) < 2 or len(value) > 100:
            return False
        
        # ГЛАВНАЯ ПРОВЕРКА 5: Должно содержать известные авторские слова ИЛИ выглядеть как имя
        # Но НЕ выглядеть как название серии
        has_known_author_words = self._contains_known_author_name(value)
        looks_like_series = self._looks_like_series_description(value)
        
        if has_known_author_words:
            # Есть известные авторские слова - это хороший знак
            return True
        
        if looks_like_series:
            # Выглядит как название серии/описания - отклонить
            return False
        
        # Проверить структурное сходство с именем (2+ слова, начинающихся с заглавной)
        words = re.split(r'\s+', value)
        if len(words) >= 2:
            # Проверить: есть ли капитализация
            capitalized_words = [w for w in words if w and w[0].isupper()]
            if len(capitalized_words) >= 2:
                # Выглядит структурно как имя (Иван Петров)
                return True
        
        return False
    
    def _contains_known_author_name(self, text: str) -> bool:
        """Check if text contains any known author names from config."""
        try:
            text_normalized = self._normalize_diacritics(text.lower())
            words = re.split(r'[^а-яa-z]+', text_normalized)
            
            for word in words:
                if word and len(word) >= 2:
                    if word in self.author_names:
                        return True
            return False
        except:
            return False
    
    def _looks_like_series_description(self, text: str) -> bool:
        """Check if text looks like series name, not author name."""
        try:
            # Blacklist слова, указывающие на серию/сборник (из конфига)
            blacklist = self.settings.get_list('filename_blacklist') + self.settings.get_list('collection_keywords')
            text_lower = text.lower()
            
            # Если содержит blacklist слова - это серия
            if any(word in text_lower for word in blacklist):
                return True
            
            # Проверить структурное сходство: выглядит ли это как имя автора?
            # "Иван Петров" (2 слова, оба капитализированы) - это имя, а не серия
            words = re.split(r'\s+', text)
            if len(words) >= 2:
                # Проверить: есть ли капитализация (имена обычно начинаются с заглавной)
                capitalized_words = [w for w in words if w and w[0].isupper()]
                if len(capitalized_words) >= 2:
                    # Выглядит структурно как имя (Иван Петров, Мороз Игорь)
                    # Это НЕ серия, это потенциальное имя автора
                    return False
            
            # Если нет известных авторских слов И текст короткий И не выглядит как имя - вероятно серия
            if not self._contains_known_author_name(text):
                if len(words) <= 3:
                    # Короткий текст без известных авторских слов и без цписанной структуры имени
                    return True
            
            return False
        except:
            return False
    
    def _select_best_pattern(self, filename: str, pattern_type: str = 'files') -> Optional[Dict[str, str]]:
        """
        Выбрать оптимальный паттерн извлечения автора на основе названия файла.
        
        Сначала пытается простые паттерны с дефисом, потом более сложные.
        
        Args:
            filename: Имя файла без расширения
            pattern_type: 'files' или 'folders'
        
        Returns:
            Dict с ключами: 'pattern', 'example', 'regex' (compiled pattern)
            или None если ничего не подходит
        """
        try:
            # Получить паттерны из конфигурации
            if pattern_type == 'files':
                config_patterns = self.settings.get_author_series_patterns_in_files()
            elif pattern_type == 'folders':
                config_patterns = self.settings.get_author_series_patterns_in_folders()
            else:
                return None
            
            if not config_patterns:
                return None
            
            # Порядок попытки (более точные первыми)
            # Приоритет: дефис-паттерны > точка-паттерны > скобки-паттерны
            pattern_priority = [
                "Author - Title (Series. service_words)",
                "Author. Title (Series. service_words)",
                "Author. Series. Title",
                "Author - Series.Title",
                "Author - Title",
                "Author. Title",
                "Title (Author)",
                "Title - (Author)",
                "(Author) - Title",
            ]
            
            for priority_pattern in pattern_priority:
                for config_pattern in config_patterns:
                    if config_pattern.get('pattern') == priority_pattern:
                        regex = self._compile_pattern_regex(config_pattern['pattern'])
                        if regex:
                            match = regex.search(filename)
                            if match:
                                # Вернуть найденный паттерн с compiled regex
                                result = dict(config_pattern)
                                result['regex'] = regex
                                result['match'] = match
                                return result
            
            # Если приоритетные не подошли, попробовать остальные
            for config_pattern in config_patterns:
                if config_pattern.get('pattern') not in pattern_priority:
                    regex = self._compile_pattern_regex(config_pattern['pattern'])
                    if regex:
                        match = regex.search(filename)
                        if match:
                            result = dict(config_pattern)
                            result['regex'] = regex
                            result['match'] = match
                            return result
            
            return None
        except Exception as e:
            return None
    
    def _compile_pattern_regex(self, pattern_str: str) -> Optional:
        r"""
        Скомпилировать паттерн в regex для извлечения авторов.
        
        Паттерны:
        - "Author - Title" → ^(.+?)\s+-\s+(.+)$
        - "Author. Title" → ^(.+?)\.\s+(.+)$
        - "Title (Author)" → (.+?)\s+\(([^)]+)\)$
        - "Author - Title (Series)" → ^(.+?)\s+-\s+(.+?)\s+\(([^)]+)\)$
        - и т.д.
        
        Args:
            pattern_str: Строка с описанием паттерна
        
        Returns:
            Скомпилированный regex или None
        """
        try:
            # Маппинг описаний паттернов на regex
            patterns_map = {
                "Author - Title (Series. service_words)": r"^(.+?)\s+-\s+(.+?)\s+\((.+)\)$",
                "Author. Title (Series. service_words)": r"^(.+?)\.\s+(.+?)\s+\((.+)\)$",
                "Author - Series.Title": r"^(.+?)\s+-\s+(.+)$",
                "Author. Series. Title": r"^(.+?)\.(.+?)\.(.+)$",
                "Author - Title": r"^(.+?)\s+-\s+(.+)$",
                "Author. Title": r"^(.+?)\.(.+)$",
                "Title (Author)": r"(.+?)\s+\(([^)]+)\)$",
                "Title - (Author)": r"(.+?)\s+-\s+\(([^)]+)\)$",
                "(Author) - Title": r"^\(([^)]+)\)\s+-\s+(.+)$",
            }
            
            if pattern_str in patterns_map:
                regex_str = patterns_map[pattern_str]
                return re.compile(regex_str, re.IGNORECASE | re.UNICODE)
            
            return None
        except Exception:
            return None
    
    def _extract_author_from_filename_with_pattern(self, filename: str, pattern_dict: Dict) -> str:
        """
        Извлечь автора из имени файла, используя найденный паттерн.
        
        Args:
            filename: Имя файла без расширения
            pattern_dict: Результат _select_best_pattern() с 'pattern', 'regex', 'match'
        
        Returns:
            Извлечённый автор или пустая строка
        """
        try:
            if not pattern_dict or 'match' not in pattern_dict:
                return ''
            
            match = pattern_dict['match']
            pattern_str = pattern_dict.get('pattern', '')
            
            # В зависимости от типа паттерна, автор находится в разной группе
            author_group_map = {
                "Author - Title (Series. service_words)": 1,
                "Author. Title (Series. service_words)": 1,
                "Author - Series.Title": 1,
                "Author. Series. Title": 1,
                "Author - Title": 1,
                "Author. Title": 1,
                "Title (Author)": 2,
                "Title - (Author)": 2,
                "(Author) - Title": 1,
            }
            
            author_group = author_group_map.get(pattern_str, 1)
            
            if match.groups():
                author = match.group(author_group) if author_group <= len(match.groups()) else ''
                author = author.strip() if author else ''
                
                # Проверить не в чёрном списке
                if author and not self._is_blacklisted_value(author):
                    return author
            
            return ''
        except Exception:
            return ''
    
    def _is_blacklisted_value(self, value: str) -> bool:
        """Проверить, есть ли значение в чёрном списке.
        
        Использует list filename_blacklist из config.json.
        Параметр защиты для PASS 1: если название папки находится в blacklist,
        пропускаем его и ищем дальше (или переходим на парсинг имени файла).
        
        Args:
            value: Значение для проверки (название папки)
            
        Returns:
            True если значение в blacklist, False иначе
        """
        blacklist = self.settings.get_filename_blacklist()
        value_lower = value.lower()
        
        # Проверить если какое-то слово из blacklist содержится в value
        for bl_word in blacklist:
            if bl_word.lower() in value_lower:
                return True
        
        return False
    
    def _has_explicit_author_in_parentheses(self, fb2_path: Path) -> bool:
        """
        Проверить, есть ли явно указанный автор в скобках в конце имени файла.
        
        Паттерн: "название (Автор).fb2"
        
        Args:
            fb2_path: Path к FB2 файлу
        
        Returns:
            True если автор явно указан в скобках в конце, False иначе
        """
        filename = fb2_path.stem
        pattern = r'\(([^()]+)\)\s*$'  # Скобки только в конце
        match = re.search(pattern, filename)
        if match:
            author_candidate = match.group(1).strip()
            # Проверить что это не чёрный список и похоже на имя автора
            if author_candidate and not self._is_blacklisted_value(author_candidate):
                # Простая проверка: содержит буквы и не выглядит как год/номер
                if any(c.isalpha() for c in author_candidate):
                    return True
        return False
    
    def _extract_all_metadata_at_once(self, fb2_path: Path) -> dict:
        """Извлечь title, authors, series, genre из FB2 за одно чтение файла.

        Читает файл один раз, находит <title-info> один раз —
        заменяет 4 отдельных вызова _extract_title/authors/series/genres в Pass 1.

        Returns:
            dict with keys: title (str), authors (str), series (str), genre (str)
        """
        result = {'title': '', 'authors': '', 'series': '', 'series_number': '', 'genre': ''}
        try:
            # 65 536 байт достаточно для любого <title-info> — он всегда в начале файла.
            # Это критично для компиляций (5–20 МБ), чтобы не читать лишние мегабайты.
            content = self._detect_correct_encoding(fb2_path, max_bytes=65536)
            if not content:
                return result

            title_info_match = re.search(
                r'<(?:fb:)?title-info>.*?</(?:fb:)?title-info>', content, re.DOTALL
            )
            if not title_info_match:
                return result

            title_info = title_info_match.group(0)

            # Title
            title_m = re.search(r'<book-title>(.*?)</book-title>', title_info, re.DOTALL)
            if title_m:
                result['title'] = title_m.group(1).strip()

            # All authors
            authors = []
            for author_m in re.finditer(
                r'<(?:fb:)?author>(.*?)</(?:fb:)?author>', title_info, re.DOTALL
            ):
                author_text = author_m.group(0)
                first_m = re.search(
                    r'<(?:fb:)?first-name>(.*?)</(?:fb:)?first-name>', author_text
                )
                last_m = re.search(
                    r'<(?:fb:)?last-name>(.*?)</(?:fb:)?last-name>', author_text
                )
                first = first_m.group(1) if first_m else ''
                last = last_m.group(1) if last_m else ''
                if first or last:
                    name = f"{first} {last}".strip()
                    if name and not self._is_blacklisted(name):
                        authors.append(name)
            result['authors'] = '; '.join(authors)

            # Series: собираем все sequence-теги, ищем диапазон номеров
            all_seqs = re.findall(
                r'<sequence\s+([^>]*/?)\s*>', title_info, re.IGNORECASE
            )
            # Парсим (name, number) из каждого тега
            seq_pairs = []
            for attrs_str in all_seqs:
                name_m = re.search(r'name=["\']([^"\']+)["\']', attrs_str, re.IGNORECASE)
                num_m  = re.search(r'number=["\'](\d+)["\']', attrs_str, re.IGNORECASE)
                if name_m:
                    seq_pairs.append((name_m.group(1).strip(), num_m.group(1) if num_m else ''))
            if seq_pairs:
                # Основная серия — первая с номером, иначе просто первая
                primary = next((n for n, _ in seq_pairs if n), '')
                result['series'] = primary
                nums = []
                for sname, snum in seq_pairs:
                    if sname == primary and snum:
                        try:
                            nums.append(int(snum))
                        except ValueError:
                            pass
                if len(nums) >= 2:
                    result['series_number'] = f'{min(nums)}-{max(nums)}'
                elif nums:
                    result['series_number'] = str(nums[0])
                else:
                    result['series_number'] = ''

            # Genre
            genres = re.findall(r'<genre[^>]*>(.*?)</genre>', title_info, re.DOTALL)
            if genres:
                result['genre'] = ', '.join(g.strip() for g in genres if g.strip())

            # Раскрываем HTML-сущности во всех строковых полях
            import html as _html
            for key in ('title', 'authors', 'series', 'genre'):
                if result[key]:
                    result[key] = _html.unescape(result[key])

        except Exception:
            pass
        return result

    def _detect_correct_encoding(self, fb2_path: Path, max_bytes: int = 0) -> str:
        """Автоматически определить правильную кодировку FB2 файла.

        Args:
            max_bytes: Если > 0, читать не более max_bytes байт (ускоряет
                       обработку компиляций — metadata всегда в начале файла).

        Стратегия:
        1. Читает BOM и объявление кодировки в XML-заголовке.
        2. Пробует UTF-8 (если успешно — сразу возвращает: UTF-8 однозначна).
        3. Если UTF-8 не подходит, пробует KOI8-R и CP1251 и выбирает ту,
           которая даёт «естественную» кириллицу (слова Николай, Бахрошин),
           а не «перевёрнутый» регистр (оЙЛПМБК) — артефакт KOI8-R, прочитанной как CP1251.

        Returns:
            Содержимое файла с правильной кодировкой, или '' если не удалось прочитать
        """
        import re as _re

        # Шаг 1: читаем начало файла в бинарном режиме
        declared_encoding = None
        try:
            with open(fb2_path, 'rb') as f:
                raw_start = f.read(256)
            if raw_start.startswith(b'\xef\xbb\xbf'):
                declared_encoding = 'utf-8-sig'
            elif raw_start.startswith((b'\xff\xfe', b'\xfe\xff')):
                declared_encoding = 'utf-16'
            else:
                header = raw_start.decode('ascii', errors='replace')
                m = _re.search(r'encoding\s*=\s*["\']([^"\']+)["\']', header, _re.IGNORECASE)
                if m:
                    declared_encoding = m.group(1)
        except Exception:
            pass

        def _read_limited(encoding, errors='strict'):
            """Читать файл целиком или до max_bytes (если задан)."""
            try:
                with open(fb2_path, 'r', encoding=encoding, errors=errors) as f:
                    return f.read(max_bytes) if max_bytes > 0 else f.read()
            except Exception:
                return None

        def _score_naturalness(text: str) -> int:
            """Оценивает «естественность» кириллицы.

            Начало слова с заглавной + строчная = норма (+1).
            Начало слова со строчной + заглавная = артефакт KOI8-R в CP1251 (-2).
            """
            words = _re.findall(r'[а-яёА-ЯЁ]+', text[:4000])
            score = 0
            for w in words:
                if len(w) < 2:
                    continue
                if w[0].isupper() and w[1].islower():
                    score += 1
                elif w[0].islower() and w[1].isupper():
                    score -= 2
            return score

        # Шаг 2: сначала пробуем UTF-8 (не нуждается в эвристике — либо OK, либо нет)
        content = _read_limited('utf-8', errors='strict')
        if content:
            return content

        # UTF-8 не подошла: файл в однобайтной кодировке.
        # Если объявленная кодировка известна и отличается от utf-8 — пробуем её первой.
        declared_lower = (declared_encoding or '').lower()

        # Если файл объявил UTF-8 но строгое чтение упало (редкие битые байты в теле),
        # пробуем с errors='replace' — метаданные в начале файла останутся корректными.
        if declared_lower in ('utf-8', 'utf8', 'utf-8-sig'):
            content = _read_limited('utf-8', errors='replace')
            if content and _score_naturalness(content) > 0:
                return content

        candidates = []
        priority = []
        if declared_encoding and declared_lower not in ('utf-8', 'utf8'):
            priority.append(declared_encoding)
        for enc in ['koi8-r', 'cp1251', 'cp866']:
            if enc not in [e.lower() for e in priority]:
                priority.append(enc)

        for encoding in priority:
            content = _read_limited(encoding, errors='strict')
            if content:
                candidates.append((_score_naturalness(content), content))

        if candidates:
            return max(candidates, key=lambda x: x[0])[1]

        # Финальный fallback с заменой символов
        content = _read_limited('utf-8', errors='replace')
        return content or ''
    
    
    
    def _extract_author_from_metadata(self, fb2_path: Path) -> str:
        """
        Извлечь автора из метаданных FB2 файла.
        
        Значение извлекается ТОЛЬКО из тега <title-info>,
        а не из других разделов (ignoring document-info и т.д.).
        Возвращает ТОЛЬКО ПЕРВОГО АВТОРА для проверки и верификации.
        """
        try:
            import re
            
            # Использовать функцию автоматического определения кодировки
            content = self._detect_correct_encoding(fb2_path)
            
            if not content:
                return ''
            
            # Найти весь <title-info>...</title-info> блок
            title_info_match = re.search(r'<(?:fb:)?title-info>.*?</(?:fb:)?title-info>', content, re.DOTALL)
            
            if not title_info_match:
                return ''
            
            # Работаем только с содержимым title-info
            title_info_content = title_info_match.group(0)
            
            # Найти первого автора ТОЛЬКО в title-info
            author_pattern = r'<(?:fb:)?author>.*?</(?:fb:)?author>'
            match = re.search(author_pattern, title_info_content, re.DOTALL)
            
            if match:
                author_text = match.group(0)
                
                # Извлечь компоненты имени
                first_name_match = re.search(r'<(?:fb:)?first-name>(.*?)</(?:fb:)?first-name>', author_text)
                last_name_match = re.search(r'<(?:fb:)?last-name>(.*?)</(?:fb:)?last-name>', author_text)
                middle_name_match = re.search(r'<(?:fb:)?middle-name>(.*?)</(?:fb:)?middle-name>', author_text)
                
                first_name = first_name_match.group(1) if first_name_match else ''
                last_name = last_name_match.group(1) if last_name_match else ''
                middle_name = middle_name_match.group(1) if middle_name_match else ''
                
                # Составить имя - используем только first-name и last-name
                # nickname игнорируется полностью
                if first_name or last_name:
                    author = f"{first_name} {last_name}".strip()
                else:
                    return ''
                
                if not author:
                    return ''
                
                # Проверить черный список
                if not self._is_blacklisted(author):
                    return author
        except Exception:
            pass
        
        return ''
    
    def _extract_all_authors_from_metadata(self, fb2_path: Path) -> str:
        """
        Извлечь ВСЕХ авторов из метаданных FB2 файла.
        
        Значение извлекается ТОЛЬКО из тега <title-info>,
        а не из других разделов.
        Возвращает строку со всеми авторами разделённых '; '
        """
        try:
            # Использовать функцию автоматического определения кодировки
            content = self._detect_correct_encoding(fb2_path)
            
            if not content:
                return ''
            
            # Найти весь <title-info>...</title-info> блок
            title_info_match = re.search(r'<(?:fb:)?title-info>.*?</(?:fb:)?title-info>', content, re.DOTALL)
            
            if not title_info_match:
                return ''
            
            # Работаем только с содержимым title-info
            title_info_content = title_info_match.group(0)
            
            # Найти всех авторов в title-info
            author_pattern = r'<(?:fb:)?author>.*?</(?:fb:)?author>'
            matches = re.finditer(author_pattern, title_info_content, re.DOTALL)
            
            authors = []
            for match in matches:
                author_text = match.group(0)
                
                # Извлечь компоненты имени
                first_name_match = re.search(r'<(?:fb:)?first-name>(.*?)</(?:fb:)?first-name>', author_text)
                last_name_match = re.search(r'<(?:fb:)?last-name>(.*?)</(?:fb:)?last-name>', author_text)
                
                first_name = first_name_match.group(1) if first_name_match else ''
                last_name = last_name_match.group(1) if last_name_match else ''
                
                # Составить имя
                if first_name or last_name:
                    author = f"{first_name} {last_name}".strip()
                    if author and not self._is_blacklisted(author):
                        authors.append(author)
            
            if authors:
                return "; ".join(authors)
        except Exception:
            pass
        
        return ''
    
    def _is_blacklisted(self, value: str) -> bool:
        """
        Проверить, находится ли значение в черном списке.
        Проверяет ЦЕЛЫЕ СЛОВА, а не подстроки.
        
        Например:
        - "СИ" в черном списке НЕ должен блокировать "Сергей Анисимов"
        - Но "СИ" должен блокировать стоящее отдельно слово "СИ"
        """
        try:
            blacklist = self.settings.get_filename_blacklist()
            value_lower = value.lower()
            value_words = value_lower.split()
            
            for item in blacklist:
                item_lower = item.lower()
                
                # ТОЧНОЕ совпадение со всей строкой
                if value_lower == item_lower:
                    return True
                
                # Проверка совпадения со СЛОВАМИ (разделённые пробелами)
                if item_lower in value_words:
                    return True
        except Exception:
            pass
        
        return False
    
    def _extract_title_from_fb2(self, fb2_path: Path) -> Optional[str]:
        """Извлечь название книги из FB2 файла.
        
        Ищет тег <book-title> в <title-info>.
        
        Args:
            fb2_path: Path к FB2 файлу
            
        Returns:
            Название книги или None
        """
        try:
            # Использовать функцию автоматического определения кодировки
            content = self._detect_correct_encoding(fb2_path)
            
            if not content:
                return None
            
            # Найти <title-info> блок
            title_info_match = re.search(r'<(?:fb:)?title-info>.*?</(?:fb:)?title-info>', content, re.DOTALL)
            if not title_info_match:
                return None
            
            title_info_content = title_info_match.group(0)
            
            # Найти <book-title>
            title_match = re.search(r'<book-title>(.*?)</book-title>', title_info_content, re.DOTALL)
            if title_match:
                title = title_match.group(1).strip()
                return title if title else None
            
            return None
            
        except Exception:
            return None
    
    def _extract_genres_from_fb2(self, fb2_path: Path) -> str:
        """Извлечь жанры из FB2 файла.
        
        Ищет теги <genre> в <title-info> и объединяет их через запятую.
        
        Args:
            fb2_path: Path к FB2 файлу
            
        Returns:
            Жанры через запятую или пустая строка
        """
        try:
            # Использовать функцию автоматического определения кодировки
            content = self._detect_correct_encoding(fb2_path)
            
            if not content:
                return ""
            
            # Найти <title-info> блок
            title_info_match = re.search(r'<(?:fb:)?title-info>.*?</(?:fb:)?title-info>', content, re.DOTALL)
            if not title_info_match:
                return ""
            
            title_info_content = title_info_match.group(0)
            
            # Найти все <genre> теги
            genres = re.findall(r'<genre[^>]*>(.*?)</genre>', title_info_content, re.DOTALL)
            
            if genres:
                # Очистить и объединить жанры
                genres = [g.strip() for g in genres if g.strip()]
                return ", ".join(genres)
            
            return ""
            
        except Exception:
            return ""
    
    def _expand_surnames_from_metadata(self, surname_string: str, metadata_author: str) -> str:
        """
        Расширить фамилии (например "Харников, Дынин") полными именами из метаданных.
        
        Алгоритм:
        1. Разбить surname_string на отдельные фамилии
        2. Для каждой фамилии найти соответствие в metadata_author
        3. Если найдено - подставить полное имя
        4. Вернуть расширенный список в формате "Фамилия Имя"
        
        Args:
            surname_string: "Харников, Дынин" или "Харников; Дынин"
            metadata_author: "Александр Харников; Максим Дынин" (полные имена)
        
        Returns:
            Расширенная строка типа "Харников Александр; Дынин Максим" или пустая строка
        """
        if not surname_string or not metadata_author:
            return ""
        
        try:
            # Разбить фамилии по разделителям
            import re
            surnames = re.split(r'[,;]', surname_string)
            surnames = [s.strip() for s in surnames if s.strip()]
            
            # Разбить метаданные по авторам
            metadata_authors = re.split(r'[;]', metadata_author)
            metadata_authors = [a.strip() for a in metadata_authors if a.strip()]
            
            expanded = []
            for surname in surnames:
                surname_lower = surname.lower()
                found = False
                
                # Поиск в метаданных: проверить каждого автора
                for meta_author in metadata_authors:
                    meta_lower = meta_author.lower()
                    
                    # Проверка 1: фамилия в конце "Имя Фамилия"
                    if meta_lower.endswith(surname_lower):
                        normalized = self._normalize_single_author(meta_author)
                        if normalized:
                            expanded.append(normalized)
                            found = True
                            break
                    
                    # Проверка 2: фамилия где-то в строке "Имя Фамилия"
                    if ' ' + surname_lower in meta_lower or meta_lower.startswith(surname_lower + ' '):
                        normalized = self._normalize_single_author(meta_author)
                        if normalized:
                            expanded.append(normalized)
                            found = True
                            break
                
                if not found:
                    # Фамилия не найдена в метаданных - хранить как есть
                    expanded.append(surname)
            
            if expanded:
                return "; ".join(expanded)
        except Exception:
            pass
        
        return ""
    
    def is_anthology(self, filename: str, author_count: int = 0) -> bool:
        """
        Определить, является ли файл сборником/антологией.
        
        Критерии (оба должны быть выполнены):
        1. Имя файла содержит маркер сборника (сборник, антология, коллекция и т.д.)
        2. И количество авторов > 2 (что указывает на множество авторов)
        
        ТОЛЬКО если оба условия выполнены - файл помечается как сборник.
        
        Args:
            filename: Имя файла без расширения
            author_count: Количество авторов из метаданных (обязательно для правильной работы)
        
        Returns:
            True если файл признан сборником (оба условия выполнены)
        """
        try:
            filename_lower = filename.lower()
            
            # Проверить маркеры сборников в имени файла
            has_marker = False
            for marker in self.anthology_markers:
                if marker in filename_lower:
                    has_marker = True
                    break
            
            # Сборник ТОЛЬКО если оба условия выполнены:
            # 1. Есть маркер сборника в имени
            # 2. Авторов > 2
            if has_marker and author_count > 2:
                return True
            
            return False
        except Exception:
            return False
    
    def _expand_abbreviation(self, abbreviated_name: str, metadata_authors_str: str) -> str:
        """
        Расширить сокращённое имя до полного.
        
        Обрабатывает два формата:
        1. "А. Живой" → ищет слово "Живой" (фамилия в конце)
        2. "Михеев М" или "Михеев М." → ищет слово "Михеев" (фамилия в начале)
        
        Алгоритм:
        1. Определить ключевое слово для поиска
           - Если "А. Фамилия" (буква + фамилия): искать фамилию
           - Если "Фамилия М/М." (фамилия + буква): искать фамилию
           - Если просто слово: искать это слово
        2. Поискать во всех metadata авторах полные имена, содержащие это слово
        3. ИСКЛЮЧИТЬ само сокращение из результатов
        4. Выбрать наиболее полное (больше букв, без точек)
        5. Вернуть в формате "Фамилия Имя"
        
        Args:
            abbreviated_name: Сокращённое имя типа "А. Живой" или "Михеев М"
            metadata_authors_str: Строка со всеми авторами из metadata
        
        Returns:
            Полное имя, или исходное если расширение не помогло
        """
        if not abbreviated_name or not metadata_authors_str:
            return abbreviated_name
        
        try:
            metadata_authors = [a.strip() for a in metadata_authors_str.split(';') if a.strip()]
            
            # Определить ключевое слово для поиска
            search_word = None
            parts = abbreviated_name.split()
            
            if len(parts) == 2:
                # Два слова: "А. Живой" или "Михеев М"
                word1 = parts[0]
                word2 = parts[1]
                
                # Проверить: является ли первое слово инициалом (одна буква)?
                if len(word1.replace('.', '')) == 1 and word1[0].isalpha():
                    # "А. Живой" - инициал в начале, фамилия в конце
                    search_word = word2.lower()
                # Проверить: является ли второе слово инициалом?
                elif len(word2.replace('.', '')) == 1 and word2[0].isalpha():
                    # "Михеев М" - фамилия в начале, инициал в конце
                    search_word = word1.lower()
                else:
                    # Оба слова полные - попробовать второе слово (может быть фамилия)
                    search_word = word2.lower()
            elif len(parts) == 1:
                # Одно слово
                if '.' in abbreviated_name:
                    # "А.Живой"
                    dot_pos = abbreviated_name.find('.')
                    if dot_pos >= 0 and dot_pos < len(abbreviated_name) - 1:
                        search_word = abbreviated_name[dot_pos+1:].lower()
                else:
                    # Просто "Живой"
                    search_word = abbreviated_name.lower()
            
            if not search_word:
                return abbreviated_name
            
            # Поискать авторов которые содержат это слово
            candidates = []
            abbrev_lower = abbreviated_name.lower()
            
            for author in metadata_authors:
                # ВАЖНО: исключить само сокращение - нам нужно расширение!
                if author.lower() == abbrev_lower:
                    continue
                
                if search_word in author.lower():
                    candidates.append(author)
            
            if candidates:
                # Выбрать самое полное (наибольшее число БУКВ, так как слова считают с точками неправильно)
                candidates.sort(key=lambda x: len(x.replace('.', '')), reverse=True)
                full_author = candidates[0]
                
                # Если нашли что-то полнее - вернуть
                expanded = self._normalize_author_format(full_author)
                if expanded:
                    return expanded
                return full_author
            
            return abbreviated_name
        
        except Exception:
            return abbreviated_name
    
    def _find_author_by_partial_name(self, partial_name: str, metadata_authors_str: str) -> str:
        """
        Найти полного автора в metadata по частичному имени.
        
        Алгоритм:
        1. Извлечь главное слово из partial_name (фамилия, исключая инициалы)
        2. Поискать во всех авторах metadata которые содержат это слово
        3. Выбрать "наиболее полный" вариант (с большим числом букв)
        4. Нормализовать в формат "Фамилия Имя"
        5. Если результат всё ещё выглядит сокращением, попытаться расширить его
        
        Args:
            partial_name: Авторское имя из filename (может быть "А. Живой", "Михеев М", или "Живой")
            metadata_authors_str: Строка со всеми авторами из metadata, разделённые "; "
        
        Returns:
            Полное имя автора в формате "Фамилия Имя", или пустая строка
        """
        if not partial_name or not metadata_authors_str:
            return ""
        
        try:
            # Разбить metadata авторов
            metadata_authors = [a.strip() for a in metadata_authors_str.split(';') if a.strip()]
            
            # Извлечь ключевое слово для поиска (исключая инициалы)
            search_words = []
            parts = partial_name.split()
            
            # Проверить: есть ли точки (признак инициала)?
            if '.' in partial_name:
                # Может быть формат "А. Живой" или "Живой М" или "М.Живой"
                if len(parts) == 2:
                    word1 = parts[0]
                    word2 = parts[1]
                    
                    # Проверить: какое слово инициал (одна буква)?
                    word1_len = len(word1.replace('.', ''))
                    word2_len = len(word2.replace('.', ''))
                    
                    if word1_len == 1 and word1[0].isalpha():
                        # Первое слово - инициал, второе - ключевое ("А. Живой")
                        search_words = [word2]
                    elif word2_len == 1 and word2[0].isalpha():
                        # Второе слово - инициал, первое - ключевое ("Живой М")
                        search_words = [word1]
                    else:
                        # Оба слова полные - попробовать второе
                        search_words = [word2]
                elif len(parts) == 1:
                    # Формат "А.Живой" - найти точку
                    dot_pos = partial_name.find('.')
                    if dot_pos >= 0 and dot_pos < len(partial_name) - 1:
                        word_after_dot = partial_name[dot_pos+1:].strip()
                        if word_after_dot:
                            search_words = [word_after_dot]
                    else:
                        # Точка в конце или на другом месте
                        search_words = [partial_name.replace('.', '')]
            else:
                # Нет точек - просто слово(а)
                if len(parts) >= 2:
                    # "Фамилия Имя" - ищем по первому слову (фамилия), затем по остальным
                    # Приоритет: первое слово (скорее всего фамилия) > остальные
                    search_words = [parts[0]]  # ВСЕГДА первое слово (фамилия)
                    # Добавить остальные слова как альтернативы (только если > 1 символа)
                    search_words.extend([p for p in parts[1:] if len(p) > 2])  # Остальные, но не инициалы
                else:
                    search_words = [partial_name]
            
            if not search_words:
                return ""
            
            # Поискать во всех авторах metadata
            # ВАЖНО: приоритизируем ПЕРВОЕ слово (фамилия) перед остальными
            matching_authors_primary = []  # Содержат первое слово
            matching_authors_secondary = []  # Содержат другие слова
            
            primary_word = search_words[0].lower()
            secondary_words = [w.lower() for w in search_words[1:]] if len(search_words) > 1 else []
            
            for meta_author in metadata_authors:
                meta_lower = meta_author.lower()
                meta_words = meta_lower.split()
                
                # Проверить первое слово (приоритет)
                # ВАЖНО: требуем точное совпадение ЦЕЛОГО слова, а не substring!
                # "Мах" должен совпадать с "Мах Макс" но НЕ с "Махров Алексей"
                if any(w.startswith(primary_word) and len(w) == len(primary_word) for w in meta_words):
                    # Точное совпадение целого слова
                    matching_authors_primary.append(meta_author)
                elif any(w.startswith(primary_word) for w in meta_words):
                    # Слово НАЧИНАЕТСЯ с искомого (например "Мах" совпадает с "Махров" - но это слабое совпадение)
                    # Добавляем как secondary, не primary
                    matching_authors_secondary.append(meta_author)
                # Иначе проверить остальные слова
                elif secondary_words:
                    for word in secondary_words:
                        if any(w.startswith(word) for w in meta_words):
                            matching_authors_secondary.append(meta_author)
                            break
            
            # Использовать primary если есть, иначе secondary
            if matching_authors_primary:
                matching_authors = matching_authors_primary
            elif matching_authors_secondary:
                matching_authors = matching_authors_secondary
            else:
                matching_authors = []
            
            if not matching_authors:
                return ""
            
            # Выбрать "наиболее полный" вариант (больше букв, без точек)
            matching_authors.sort(key=lambda x: len(x.replace('.', '')), reverse=True)
            full_author = matching_authors[0]  # Самый полный
            
            # Нормализовать в формат "Фамилия Имя"
            normalized = self._normalize_author_format(full_author)
            
            if not normalized:
                normalized = full_author
            
            # КЛЮЧЕВОЙ ШАГ: Если результат - это сокращение (А. Живой),
            # попытаться расширить его до полного имени (Живой Алексей)
            if '.' in normalized and normalized.count(' ') <= 1:
                # Это выглядит как сокращение
                expanded = self._expand_abbreviation(normalized, metadata_authors_str)
                if expanded != normalized:
                    # Успешно расширили
                    return expanded
            
            return normalized
        
        except Exception:
            return ""
    
    def expand_abbreviated_author(self, abbreviated_author: str, all_authors_map: Dict) -> str:
        """
        Раскрыть сокращённого автора (А.Фамилия) до полного имени.
        
        Стратегия:
        1. Парсить "А.Фамилия" - может быть как "А. Фамилия" так и "А.Фамилия"
        2. Извлечь букву инициала и фамилию
        3. Поискать в all_authors_map по фамилии как ключу
        4. all_authors_map[фамилия] = СПИСОК полных имён
        5. Найти первое имя, которое начинается с инициала
        6. Если найдено - вернуть полное имя
        7. Если нет - оставить как было
        
        Args:
            abbreviated_author: Сокращённое имя типа "А.Фамилия" или "А. Фамилия"
            all_authors_map: Словарь {фамилия.lower(): [полное_имя1, полное_имя2, ...]} или строка с авторами
        
        Returns:
            Полное имя если найдено, иначе исходное
        """
        if isinstance(all_authors_map, str):
            # Handle string input: ; separated full names
            all_authors = [a.strip() for a in all_authors_map.split(';') if a.strip()]
            if '.' in abbreviated_author:
                # Parse surname from abbreviated
                parts = abbreviated_author.split()
                if len(parts) >= 1:
                    surname = parts[-1].lower()
                    for full in all_authors:
                        if surname in full.lower() and '.' not in full and len(full.split()) >= 2:
                            return full
            else:
                # Abbreviated is surname, find full containing it
                surname = abbreviated_author.lower()
                for full in all_authors:
                    if surname in full.lower() and '.' not in full and len(full.split()) >= 2:
                        return full
            return abbreviated_author
        
        if not abbreviated_author or '.' not in abbreviated_author:
            return abbreviated_author
        
        try:
            # Парсить "А.Фамилия" или "А. Фамилия"
            # Сначала попробуем с пробелом (А. Фамилия)
            parts = abbreviated_author.split()
            if len(parts) == 2:
                # Формат "А. Фамилия"
                init_part = parts[0]
                surname = parts[1]
            elif len(parts) == 1:
                # Формат "А.Фамилия" (без пробела)
                # Нужно парсить вручную
                s = abbreviated_author
                
                # Найти позицию точки
                dot_pos = s.find('.')
                if dot_pos == -1:
                    return abbreviated_author
                
                init_part = s[:dot_pos+1]  # "А." или "Вишневский."
                surname = s[dot_pos+1:].lstrip()  # "Фамилия" или ""
                
                # Если surname пустая, значит вся строка - фамилия с точкой
                if not surname:
                    # Например "Вишневский." - это фамилия "Вишневский"
                    surname = init_part.rstrip('.')
                    init_part = ""
                elif not surname and len(init_part) > 2:
                    # Если init_part длиннее инициала, это фамилия
                    surname = init_part.rstrip('.')
                    init_part = ""
                
                if not surname:
                    return abbreviated_author
            else:
                return abbreviated_author
            
            # Если init_part пустой, значит это просто фамилия без инициала
            if not init_part:
                # Просто фамилия, например "Вишневский"
                surname_lower = surname.lower()
                if surname_lower in all_authors_map:
                    full_names = all_authors_map[surname_lower]
                    if isinstance(full_names, list) and full_names:
                        # Вернуть первое полное имя
                        return full_names[0]
                    elif isinstance(full_names, str):
                        return full_names
                return abbreviated_author
            
            # Проверить что первая часть заканчивается точкой
            if not init_part.endswith('.'):
                return abbreviated_author
            
            # Получить первую букву инициала
            first_letter = init_part[0].upper()
            
            # Поискать в словаре по фамилии как ключу
            surname_lower = surname.lower()
            
            # Попытка 1: найти точное совпадение фамилии в словаре
            if surname_lower in all_authors_map:
                full_names = all_authors_map[surname_lower]
                
                # all_authors_map[фамилия] теперь СПИСОК полных имён
                if isinstance(full_names, list):
                    # Искать первое имя, которое начинается с нужной буквы
                    for full_name in full_names:
                        # Парсить полное имя "Фамилия Имя"
                        full_parts = full_name.split()
                        if len(full_parts) >= 2:
                            first_name = full_parts[1]  # Второе слово - имя
                            
                            # Проверить совпадение первой буквы имени с инициалом
                            if first_name and first_name[0].upper() == first_letter:
                                return full_name
                else:
                    # На случай если это ещё старый формат (строка вместо списка)
                    full_name = full_names
                    full_parts = full_name.split()
                    if len(full_parts) >= 2:
                        first_name = full_parts[1]
                        if first_name and first_name[0].upper() == first_letter:
                            return full_name
            
            # Если не найдено - вернуть как было
            return abbreviated_author
        
        except Exception as e:
            return abbreviated_author
    
    def expand_surname_to_fullname(self, surname: str, metadata_authors: str) -> str:
        """
        Попытаться расширить фамилию (без точек) до полного имени из метаданных.
        
        Примеры:
        - "Каменские" + "Юрий Каменский; Вера Каменская" → "Юрий Каменский; Вера Каменская"
        - "Зеленский" + "Борис Зеленский; Святослав Логинов" → "Борис Зеленский; Святослав Логинов"
        - "Логинов СССР" → извлечение "Логинов" (пропуск "СССР" из abbreviations_preserve_case)
        
        Args:
            surname: Фамилия или сокращение (без точек)
            metadata_authors: Строка с авторами из метаданных (разделены ;)
        
        Returns:
            Полные имена если совпадение найдено, иначе исходная фамилия
        """
        if not surname or not metadata_authors:
            return surname
        
        # Извлечь первое слово из фамилии, пропуская известные аббревиатуры
        words = surname.split()
        first_word = None
        for word in words:
            if word not in self.abbreviations_preserve_case:
                first_word = word
                break
        
        if not first_word:
            # Если все слова - аббревиатуры, использовать исходную фамилию
            first_word = surname
        
        # Нормализовать фамилию для сравнения
        surname_lower = first_word.lower().strip()
        
        # Парсить metadata_authors
        authors_list = [a.strip() for a in metadata_authors.split(';') if a.strip()]
        
        matching_authors = []
        for author in authors_list:
            author_lower = author.lower()
            
            # Попытаться найти совпадение
            # Случай 1: "Каменские" совпадает с "Каменск..." (начало фамилии)
            if author_lower.startswith(surname_lower):
                matching_authors.append(author)
            # Случай 2: Fuzzy match (более гибкий поиск) - для форм слова (Каменские vs Каменский)
            elif self._fuzzy_match_surname(first_word, author) > 0.75:
                matching_authors.append(author)
        
        # Если найдены совпадения, нормализовать и вернуть их
        if matching_authors:
            # Нормализовать каждого автора из ИФ в ФИ (если 2 слова)
            normalized_authors = []
            for author in matching_authors:
                normalized = self._normalize_author_if_needed(author)
                normalized_authors.append(normalized)
            return ", ".join(normalized_authors)
        
        return surname
    
    def _fuzzy_match_surname(self, surname: str, fullname: str) -> float:
        """
        Fuzzy match для сравнения фамилии и полного имени.
        
        Извлекает фамилию из полного имени и сравнивает с исходной фамилией.
        
        Args:
            surname: Фамилия (может быть во множественном числе: "Каменские")
            fullname: Полное имя (Имя Фамилия): "Юрий Каменский"
        
        Returns:
            Процент сходства (0-1)
        """
        try:
            from difflib import SequenceMatcher
            
            surname_lower = surname.lower().strip()
            fullname_lower = fullname.lower().strip()
            
            # Если фамилия есть в полном имени
            if surname_lower in fullname_lower:
                return 1.0
            
            # Извлечь последнее слово из fullname (обычно фамилия)
            parts = fullname_lower.split()
            if parts:
                fullname_surname = parts[-1]
                
                # Сравнить основы фамилий
                # "Каменские" vs "Каменский" -> "Камен" vs "Камен" (первые 80%)
                ratio = SequenceMatcher(None, surname_lower, fullname_surname).ratio()
                return ratio
            
            # Вычислить степень сходства по всему имени
            ratio = SequenceMatcher(None, surname_lower, fullname_lower).ratio()
            return ratio
        except:
            return 0.0
    
    def _normalize_author_if_needed(self, author: str) -> str:
        """
        Нормализовать автора из ИФ (Имя Фамилия) в ФИ (Фамилия Имя).
        Для одноименных авторов (1 слово) вернуть как есть.
        
        Важно: Аббревиатуры типа "А. Живой" НЕ упрощаются! Они остаются как есть
        для раскрытия на этапе expand_abbreviated_authors.
        
        Args:
            author: Имя автора в любом формате
        
        Returns:
            Нормализованное имя ФИ или исходное если нельзя определить
        """
        if not author:
            return author
        
        parts = author.strip().split()
        
        # Если одно слово - одноименный автор, вернуть как есть
        if len(parts) == 1:
            return author
        
        # Если два слова - проверить и нормализовать
        if len(parts) == 2:
            first_part = parts[0].lower()
            second_part = parts[1].lower()
            
            # Случай 1: "Юрий Каменский" → "Каменский Юрий" (полное имя + фамилия)
            # Если первое слово - имя (есть в списках), а второе - не имя, то это ИФ формат
            all_names = self.male_names | self.female_names
            if first_part in all_names and second_part not in all_names:
                # Поменять на ФИ
                return f"{parts[1]} {parts[0]}"
            
            # Случай 2: "А. Живой" → ОСТАВИТЬ КАК ЕСТЬ
            # Аббревиатуры будут раскрыты на этапе expand_abbreviated_authors
            # через поиск по словарю авторов
            if "." in first_part and len(first_part) <= 2:
                # Это инициал, оставить как есть для раскрытия
                return author
            
            # Если уже в ФИ формате (первое не имя, второе имя) - вернуть как есть
            # Если оба - имена или оба - неизвестны - оставить как есть
            return author
        
        # Более 2 слов - оставить как есть (сложные имена)
        return author
    
    def reload_config(self):
        """Перезагрузить конфигурацию и паттерны."""
        self.settings.load()
        self.author_processor.reload_patterns()

    def _extract_series_from_metadata(self, fb2_path: Path) -> str:
        """
        Извлечь серию из метаданных FB2 файла.
        
        Ищет элемент <sequence> в блоке <title-info>.
        Если несколько series, берёт первую.
        
        Пример FB2 структуры:
            <sequence name="Война в Космосе" number="1"/>
        
        Args:
            fb2_path: Путь к FB2 файлу
        
        Returns:
            Название серии из атрибута 'name', или пустая строка
        """
        try:
            # Использовать функцию автоматического определения кодировки
            content = self._detect_correct_encoding(fb2_path)
            
            if not content:
                return ''
            
            # Найти весь <title-info>...</title-info> блок
            title_info_match = re.search(r'<(?:fb:)?title-info>.*?</(?:fb:)?title-info>', content, re.DOTALL)
            
            if not title_info_match:
                return ''
            
            # Работаем только с содержимым title-info
            title_info_content = title_info_match.group(0)
            
            # Найти первый элемент <sequence> с атрибутом name
            # Паттерн: <sequence name="Название" .../>
            sequence_pattern = r'<sequence\s+[^>]*name=["\']([^"\']+)["\'][^>]*/?>'
            match = re.search(sequence_pattern, title_info_content, re.IGNORECASE)
            
            if match:
                series_name = match.group(1).strip()
                if series_name:
                    return series_name
        except Exception:
            pass
        
        return ''


if __name__ == '__main__':
    # Простой тест
    extractor = FB2AuthorExtractor()
    print("FB2AuthorExtractor инициализирован")
    print(f"AuthorProcessor: {extractor.author_processor}")
    print(f"Приоритеты извлечения:")
    for priority in AuthorExtractionPriority.ORDER:
        print(f"  {priority}: {AuthorExtractionPriority.get_name(priority)}")
