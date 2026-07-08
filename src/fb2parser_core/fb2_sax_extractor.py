import xml.sax
import xml.sax.handler
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import re

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


class FB2SAXHandler(xml.sax.handler.ContentHandler):
    """
    SAX handler для парсинга FB2 файлов.
    Извлекает авторов и серию из title-info блока.
    """

    def __init__(self):
        self.in_title_info = False
        self.in_author = False
        self.in_first_name = False
        self.in_middle_name = False
        self.in_last_name = False
        self.in_sequence = False
        self.in_book_title = False
        self.in_genre = False

        self.authors = []
        self.current_author = {}
        self.series_name = ""
        self.series_number = ""
        self.book_title = ""
        self.genres = []
        # Накапливаем все sequence-теги: list of (name, number_str)
        self._all_sequences: list = []

        self.current_element = ""
        self.element_stack = []

    def startElement(self, name, attrs):
        self.element_stack.append(name)

        # Убираем namespace префикс fb: если есть
        local_name = name.split(':', 1)[-1] if ':' in name else name

        if local_name == 'title-info':
            self.in_title_info = True
        elif self.in_title_info and local_name == 'author':
            self.in_author = True
            self.current_author = {}
        elif self.in_author and local_name == 'first-name':
            self.in_first_name = True
        elif self.in_author and local_name == 'middle-name':
            self.in_middle_name = True
        elif self.in_author and local_name == 'last-name':
            self.in_last_name = True
        elif self.in_title_info and local_name == 'sequence':
            self.in_sequence = True
            # Извлекаем атрибуты серии; series_name/series_number — последний тег (обратная совместимость)
            seq_name = attrs.get('name', '')
            seq_num  = attrs.get('number', '')
            if seq_name:
                self.series_name = seq_name
            if seq_num:
                self.series_number = seq_num
            self._all_sequences.append((seq_name, seq_num))
        elif self.in_title_info and local_name == 'book-title':
            self.in_book_title = True
        elif self.in_title_info and local_name == 'genre':
            self.in_genre = True

    def endElement(self, name):
        local_name = name.split(':', 1)[-1] if ':' in name else name

        if local_name == 'title-info':
            self.in_title_info = False
        elif local_name == 'author':
            self.in_author = False
            # Сохраняем автора
            if self.current_author:
                self.authors.append(self.current_author.copy())
        elif local_name == 'first-name':
            self.in_first_name = False
        elif local_name == 'middle-name':
            self.in_middle_name = False
        elif local_name == 'last-name':
            self.in_last_name = False
        elif local_name == 'sequence':
            self.in_sequence = False
        elif local_name == 'book-title':
            self.in_book_title = False
        elif local_name == 'genre':
            self.in_genre = False

        if self.element_stack:
            self.element_stack.pop()

    def characters(self, content):
        if not self.in_title_info:
            return

        if self.in_first_name:
            self.current_author['first_name'] = self.current_author.get('first_name', '') + content
        elif self.in_middle_name:
            self.current_author['middle_name'] = self.current_author.get('middle_name', '') + content
        elif self.in_last_name:
            self.current_author['last_name'] = self.current_author.get('last_name', '') + content
        elif self.in_book_title:
            self.book_title += content
        elif self.in_genre:
            g = content.strip()
            if g and g not in self.genres:
                self.genres.append(g)


class FB2SAXExtractor:
    """
    SAX-based extractor для FB2 файлов.
    Более эффективен по памяти чем ElementTree для больших файлов.
    """

    def __init__(self, config_path: str = 'config.json'):
        """
        Инициализация SAX экстрактора авторов FB2.

        Args:
            config_path: Путь к файлу конфигурации
        """
        self.settings = SettingsManager(config_path)
        self.author_processor = AuthorProcessor(config_path)

        # Загрузить списки имен для определения порядка слов
        self.male_names = set(name.lower() for name in self.settings.get_male_names())
        self.female_names = set(name.lower() for name in self.settings.get_female_names())

        # Маркеры сборников/антологий из конфига
        self.anthology_markers = self.settings.get_list('collection_keywords')

    def _extract_metadata_with_sax(self, fb2_path: Path) -> Tuple[List[str], str]:
        """
        Извлечь авторов и серию из FB2 файла используя SAX парсер.

        Args:
            fb2_path: Путь к FB2 файлу

        Returns:
            (authors_list, series_name) где authors_list - список полных имен авторов
        """
        try:
            handler = FB2SAXHandler()

            # Попытка автоматического определения кодировки
            encoding = self._detect_encoding(fb2_path)
            if not encoding:
                return [], ""

            # Парсинг с SAX
            parser = xml.sax.make_parser()
            parser.setContentHandler(handler)

            with open(fb2_path, 'r', encoding=encoding, errors='ignore') as f:
                parser.parse(f)

            # Формируем список авторов (с дедупликацией)
            authors_list = []
            seen_lower: set = set()
            for author in handler.authors:
                parts = []
                if author.get('first_name', '').strip():
                    parts.append(author['first_name'].strip())
                if author.get('middle_name', '').strip():
                    parts.append(author['middle_name'].strip())
                if author.get('last_name', '').strip():
                    parts.append(author['last_name'].strip())

                if parts:
                    name = ' '.join(parts)
                    if name.lower() not in seen_lower:
                        authors_list.append(name)
                        seen_lower.add(name.lower())

            return authors_list, handler.series_name

        except Exception as e:
            # В случае ошибки возвращаем пустые результаты
            return [], ""

    def _detect_encoding(self, fb2_path: Path) -> Optional[str]:
        """
        Определить кодировку FB2 файла.
        """
        try:
            # Читаем первые 1024 байта для определения кодировки
            with open(fb2_path, 'rb') as f:
                raw = f.read(1024)

            # Ищем XML декларацию
            content = raw.decode('utf-8', errors='ignore')
            encoding_match = re.search(r'<\?xml[^>]*encoding=["\']([^"\']+)["\']', content, re.IGNORECASE)

            if encoding_match:
                encoding = encoding_match.group(1).lower()
                # Нормализуем распространенные варианты
                if encoding in ['windows-1251', 'cp1251']:
                    return 'cp1251'
                elif encoding in ['utf-8', 'utf8']:
                    return 'utf-8'
                else:
                    # Объявлена нестандартная кодировка (latin-1 и т.п.).
                    # Проверяем: если содержимое является валидным UTF-8 —
                    # используем UTF-8 (объявление ошибочное, файл реально в UTF-8).
                    try:
                        raw.decode('utf-8', errors='strict')
                        return 'utf-8'
                    except (UnicodeDecodeError, ValueError):
                        return encoding

            # По умолчанию пробуем utf-8
            return 'utf-8'

        except Exception:
            return 'utf-8'

    def _extract_author_from_metadata(self, fb2_path: Path) -> str:
        """
        Извлечь автора из метаданных FB2 файла используя SAX.
        Возвращает строку с авторами, разделенными '; '
        """
        authors_list, _ = self._extract_metadata_with_sax(fb2_path)
        return '; '.join(authors_list)

    def _extract_all_authors_from_metadata(self, fb2_path: Path) -> str:
        """
        Извлечь всех авторов из метаданных FB2 файла.
        Возвращает строку с авторами, разделенными '; '
        """
        authors_list, _ = self._extract_metadata_with_sax(fb2_path)
        return '; '.join(authors_list)

    def _extract_series_from_metadata(self, fb2_path: Path) -> str:
        """
        Извлечь серию из метаданных FB2 файла используя SAX.
        """
        _, series_name = self._extract_metadata_with_sax(fb2_path)
        return series_name

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
        - Дефисы допускаются в составных именах/фамилиях
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

            # Проверить: если это аббревиатура типа "А.Живой", оставить как есть
            if '.' in author_name:
                # Это может быть аббревиатура - проверить
                parts = author_name.split()
                if len(parts) == 2 and parts[0].endswith('.') and len(parts[0]) <= 3:
                    # Формат "А.Живой" или "А.B.Живой"
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
                        break  # Остановить при первом некорректном символе

                if not clean_word:
                    return ""  # Слово не содержит букв - отбросить

                # Проверить что начинается с большой буквы
                if not clean_word[0].isupper():
                    return ""

                cleaned_words.append(clean_word)

            if len(cleaned_words) != 2:
                return ""

            # Определить порядок слов на основе списков имен
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

    def resolve_author_by_priority(
        self,
        fb2_filepath: str,
        folder_parse_limit: int = 3,
        all_series_authors_str: str = ""
    ) -> Tuple[str, str]:
        """
        Простой метод для получения автора по приоритетам источников.

        Приоритеты извлечения в зависимости от folder_parse_limit:
        - folder_parse_limit > 0: папка → файл → метаданные (структурированное хранилище)
        - folder_parse_limit == 0: файл → метаданные (неструктурированные, разные авторы в папке)

        Для источников 1 и 2 используется fuzzy matching для верификации:
        - Кандидат сравнивается с авторами из метаданных
        - Если похожесть > 70%, принимается
        - Если не похоже ни на кого в метаданных, отклоняется

        Правило множественных авторов:
        - Если авторов <= 2: брать имена
        - Если авторов > 2: вернуть "Соавторство"

        Args:
            fb2_filepath: Полный путь к FB2 файлу
            folder_parse_limit: Глубина парсинга папок (int):
                - 0: не парсим папки вообще (приоритет: файл → метаданные)
                - N>0: парсим максимум N уровней вверх (приоритет: папка → файл → метаданные)
            all_series_authors_str: Строка со всеми авторами из всех файлов серии/папки (для расширения)

        Returns:
            (author_name, source) где source in ['folder_dataset', 'folder', 'filename', 'metadata', '']
            Если ничего не найдено, вернуть ('', '')
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
                            # (Расширение аббревиатур произойдет позже в RegenCSVService._expand_abbreviated_authors)
                            author = self._normalize_author_count(author)
                            author = self._normalize_author_format(author) if author else ""
                            if not author:
                                # Если нормализация не сработала, довериться исходному списку
                                author = " ".join(self._extract_author_from_folder_structure(fb2_path, folder_parse_limit).split())
                            if author:
                                return author, 'folder_dataset'  # ИСПРАВЛЕНО: folder_dataset вместо folder
                        elif self._verify_author_against_metadata(author, metadata_author):
                            # Один автор - проверить против метаданных
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

                    # КЛЮЧЕВАЯ ПРОВЕРКА: Есть ли несколько авторов (разделены запятой или точкой с запятой)
                    # обрабатывать каждого сразу (как для явного паттерна в скобках, так и для авторов в имени файла)
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
                                # Полное имя - нормализовать
                                normalized = self._normalize_author_format(single_author)
                                if normalized:
                                    expanded_authors.append(normalized)
                                else:
                                    expanded_authors.append(single_author)
                            else:
                                # Сокращение или одно слово - попытаться расширить
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

                                # Попытка 4: расширение фамилий в метаданных
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
                        # Автор уже полный - нормализовать и вернуть как есть
                        normalized = self._normalize_author_format(author)
                        if normalized:
                            return normalized, 'filename'
                        else:
                            # Нормализация не сработала - вернуть исходный
                            return author, 'filename'

                    # Если это сокращение/одно слово, попытаться расширить из метаданных
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

                    # Попытка 4: старый метод расширения фамилий в metadata файла
                    if not expanded_author and all_metadata_authors:
                        expanded_author = self._expand_surnames_from_metadata(author, all_metadata_authors)

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

    def _extract_author_from_folder_structure(self, fb2_path: Path, max_depth: int) -> str:
        """
        Извлечь автора из структуры папок.

        Args:
            fb2_path: Путь к FB2 файлу
            max_depth: Максимальная глубина поиска вверх

        Returns:
            Автор из папки или пустая строка
        """
        try:
            current = fb2_path.parent
            depth = 0

            while depth < max_depth and current != current.parent:
                folder_name = current.name

                # Пропустить корневые папки типа "D:", "C:" и т.п.
                if len(folder_name) <= 3 and ':' in folder_name:
                    current = current.parent
                    depth += 1
                    continue

                # Попытаться извлечь автора из имени папки
                author = self._extract_author_from_folder_name(folder_name)
                if author:
                    return author

                current = current.parent
                depth += 1

            return ""
        except Exception:
            return ""

    def _extract_author_from_folder_name(self, folder_name: str) -> str:
        """
        Извлечь автора из имени папки.
        """
        try:
            # Убрать лишние пробелы
            folder_name = folder_name.strip()

            # Пропустить если это год или число
            if folder_name.isdigit() or (len(folder_name) == 4 and folder_name.isdigit()):
                return ""

            # Пропустить если содержит маркеры сборников
            folder_lower = folder_name.lower()
            if any(marker.lower() in folder_lower for marker in self.anthology_markers):
                return ""

            # Попытаться распарсить как "Автор - Серия"
            if ' - ' in folder_name:
                author_part = folder_name.split(' - ')[0].strip()
                if author_part and not author_part.isdigit():
                    return author_part

            # Попытаться распарсить как "Автор_Серия"
            if '_' in folder_name:
                author_part = folder_name.split('_')[0].strip()
                if author_part and not author_part.isdigit():
                    return author_part

            # Если папка содержит пробелы и выглядит как имя автора
            if ' ' in folder_name:
                words = folder_name.split()
                if len(words) >= 2:
                    # Проверить что это может быть ФИ
                    if all(word and word[0].isupper() for word in words[:2]):
                        return folder_name

            # Если папка выглядит как фамилия (одно слово с большой буквы)
            if len(folder_name.split()) == 1 and folder_name[0].isupper():
                return folder_name

            return ""
        except Exception:
            return ""

    def _extract_author_from_filename(self, fb2_path: Path) -> str:
        """
        Извлечь автора из имени файла.
        """
        try:
            filename = fb2_path.stem  # Без расширения

            # Убрать лишние пробелы
            filename = filename.strip()

            # Попытаться найти паттерн "Автор - Название"
            if ' - ' in filename:
                author_part = filename.split(' - ')[0].strip()
                if author_part:
                    return author_part

            # Попытаться найти паттерн "Автор_Название"
            if '_' in filename:
                author_part = filename.split('_')[0].strip()
                if author_part:
                    return author_part

            # Попытаться найти автора в скобках в конце файла
            # Паттерн: "Название (Автор)"
            import re
            match = re.search(r'\(([^)]+)\)$', filename)
            if match:
                author_in_brackets = match.group(1).strip()
                if author_in_brackets:
                    return author_in_brackets

            return ""
        except Exception:
            return ""

    def _normalize_author_count(self, author_string: str) -> str:
        """
        Нормализовать количество авторов и формат.

        Правила:
        - Если авторов <= 2: нормализуем формат и возвращаем
        - Если авторов > 2: возвращаем "Соавторство"

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

            # Если разделителей нет - один автор
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
                        # оставить как есть
                        normalized = author.strip()
                    if normalized:
                        normalized_authors.append(normalized)

                if normalized_authors:
                    return ", ".join(normalized_authors)
                else:
                    # Если ничего не нормализовалось, оставить исходное
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
        2. Проверка что хоть одно слово из metadata есть в candidate
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

            # 2. Проверить что хоть одно слово из metadata есть в candidate
            # Это помогает при разном порядке слов: "Иван Петров" vs "Петров Иван"
            meta_words = set(meta_lower.split())
            cand_words = set(cand_lower.replace(',', ' ').split())  # Убрать запятые (для списков авторов)

            # Если найдено хоть 50% слов из метаданных в кандидате, это хороший знак
            if meta_words and cand_words:
                overlap = len(meta_words & cand_words) / len(meta_words)
                if overlap >= 0.5:  # Хоть половина слов совпадает
                    return True

            # 3. Fuzzy matching для последнего шанса
            similarity = SequenceMatcher(None, cand_lower, meta_lower).ratio()
            if similarity >= 0.70:
                return True

            return False
        except Exception:
            return False

    def _find_author_by_partial_name(self, partial_name: str, metadata_authors_str: str) -> str:
        """
        Найти полного автора в metadata по частичному имени.

        Алгоритм:
        1. Извлечь ключевое слово из partial_name (фамилию, исключая инициалы)
        2. Поискать в каждом авторе metadata который содержит это слово
        3. Выбрать "наиболее полный" вариант (больше букв, без точек)
        4. Нормализовать в формат "Фамилия Имя"
        5. Если результат всё ещё сокращение, попытаться расширить его

        Args:
            partial_name: Авторское имя из filename ("А. Живой", "Михеев М", или "Живой")
            metadata_authors_str: Строка со всеми авторами из metadata, разделёнными "; "

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

            # Проверить: есть ли точки (признак инициалов)?
            if '.' in partial_name:
                # Формат "А. Живой" или "Живой М"
                if len(parts) == 2:
                    word1 = parts[0]
                    word2 = parts[1]

                    # Определить какое слово ключевое (инициал или фамилия)
                    word1_len = len(word1.replace('.', ''))
                    word2_len = len(word2.replace('.', ''))

                    if word1_len == 1 and word1[0].isalpha():
                        # Первое слово - инициал, второе - ключевое (фамилия)
                        search_words = [word2]
                    elif word2_len == 1 and word2[0].isalpha():
                        # Второе слово - инициал, первое - ключевое (фамилия)
                        search_words = [word1]
                    else:
                        # Оба слова полные - попробовать второе (обычно фамилия)
                        search_words = [word2]
                elif len(parts) == 1:
                    # Формат "А.Живой" (без пробела)
                    # Найти точку
                    dot_pos = partial_name.find('.')
                    if dot_pos >= 0 and dot_pos < len(partial_name) - 1:
                        word_after_dot = partial_name[dot_pos+1:].strip()
                        if word_after_dot:
                            search_words = [word_after_dot]
                    else:
                        # Точка в конце или в начале - пропустить
                        search_words = [partial_name.replace('.', '')]
            else:
                # Нет точек - простое слово(а)
                if len(parts) >= 2:
                    # "Фамилия Имя" - искать по первому слову (фамилии), затем по остальным
                    # Приоритет: первое слово (скорее всего фамилия) > остальные
                    search_words = [parts[0]]  # ВСЕГДА первое слово (фамилия)
                    # Добавить остальные, но только если > 2 символов (не инициалы)
                    search_words.extend([p for p in parts[1:] if len(p) > 2])  # Остальные, но не инициалы
                else:
                    search_words = [partial_name]

            if not search_words:
                return ""

            # ВАЖНО: приоритизировать ПЕРВОЕ слово (фамилию) перед остальными
            primary_word = search_words[0].lower()
            secondary_words = [w.lower() for w in search_words[1:]] if len(search_words) > 1 else []

            # Поискать в каждом авторе metadata
            # ВАЖНО: приоритезировать ПЕРВОЕ слово (фамилию) перед остальными
            matching_authors_primary = []  # Содержат первое слово
            matching_authors_secondary = []  # Содержат другие слова

            for meta_author in metadata_authors:
                meta_lower = meta_author.lower()
                meta_words = meta_lower.split()

                # Проверить первое слово (приоритет)
                # ВАЖНО: требуем точное совпадение ЦЕЛОГО слова, а не substring!
                # "Мах" должен совпадать с "Мах Махович" но НЕ с "Махров Алексей"
                if any(w.startswith(primary_word) and len(w) == len(primary_word) for w in meta_words):
                    # Точное совпадение целого слова
                    matching_authors_primary.append(meta_author)
                elif any(w.startswith(primary_word) for w in meta_words):
                    # Слово НАЧИНАЕТСЯ с искомого (слабое совпадение)
                    # Например "Мах" совпадает с "Махров" - но это слабое совпадение
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

            # КЛЮЧЕВОЙ ШАГ: Если результат всё ещё выглядит как сокращение (А. Живой),
            # попытаться расширить его до полного имени (Живой Алексей)
            if '.' in normalized and normalized.count(' ') <= 1:
                # Выглядит как сокращение
                expanded = self._expand_abbreviation(normalized, metadata_authors_str)
                if expanded != normalized:
                    # Успешно расширили
                    return expanded

            return normalized
        except Exception:
            return ""

    def _expand_abbreviation(self, abbreviated_name: str, metadata_authors_str: str) -> str:
        """
        Расширить сокращённое имя до полного.

        Обрабатывает два формата:
        1. "А. Живой" → искать слово "Живой" (фамилия в конце)
        2. "Михеев М" или "Михеев М." → искать слово "Михеев" (фамилия в начале)

        Алгоритм:
        1. Определить ключевое слово для поиска
           - "А. Живой" (инициал в начале, фамилия в конце): искать "Живой"
           - "Михеев М" (фамилия в начале, инициал в конце): искать "Михеев"
           - "А.Живой" (без пробела): искать "Живой"
        2. Поискать в metadata авторов которые содержат это слово
        3. ИСКЛЮЧИТЬ само сокращение из результатов
        4. Выбрать наиболее полное (больше букв, без точек)
        5. Вернуть в формате "Фамилия Имя"

        Args:
            abbreviated_name: Сокращённое имя типа "А. Живой" или "Михеев М"
            metadata_authors_str: Строка со всеми авторами из metadata

        Returns:
            Полное имя, или исходное если расширение не удалось
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

                # Проверить: является ли первое слово инициалом (одна буква + точка)?
                if len(word1.replace('.', '')) == 1 and word1[0].isalpha():
                    # "А. Живой" - инициал в начале, фамилия в конце
                    search_word = word2.lower()
                # Проверить: является ли второе слово инициалом?
                elif len(word2.replace('.', '')) == 1 and word2[0].isalpha():
                    # "Михеев М" - фамилия в начале, инициал в конце
                    search_word = word1.lower()
                else:
                    # Оба слова полные - попробовать второе (обычно фамилия)
                    search_word = word2.lower()
            elif len(parts) == 1:
                # Одно слово: "А.Живой"
                s = abbreviated_name
                # Найти позицию точки
                dot_pos = s.find('.')
                if dot_pos >= 0 and dot_pos < len(s) - 1:
                    search_word = s[dot_pos+1:].lower()
                else:
                    # Точка в конце или в начале - использовать всё слово
                    search_word = s.replace('.', '').lower()

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
                # Выбрать самый полный (больше букв, без точек)
                candidates.sort(key=lambda x: len(x.replace('.', '')), reverse=True)
                full_author = candidates[0]

                # Нормализовать в формат "Фамилия Имя"
                expanded = self._normalize_author_format(full_author)
                if expanded:
                    return expanded
                return full_author

            return abbreviated_name
        except Exception:
            return abbreviated_name

    def _expand_surname_to_fullname(self, surname: str, metadata_authors: str) -> str:
        """
        Попытаться расширить фамилию (без точек) до полного имени из metadata.

        Примеры:
        - "Каменские" + "Юрий Каменский; Вера Каменская" → "Юрий Каменский; Вера Каменская"
        - "Зеленский" + "Борис Зеленский; Святослав Логинов" → "Борис Зеленский; Святослав Логинов"
        - "Логинов ССР" → извлечь "Логинов" (пропустить "ССР" из abbreviations_preserve_case)

        Args:
            surname: Фамилия или сокращение (без точек)
            metadata_authors: Строка с авторами из metadata (разделены ;)

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
            # Все слова - аббревиатуры, использовать исходное
            first_word = surname

        # Нормализовать фамилию для сравнения
        surname_lower = first_word.lower().strip()

        # Разбить metadata авторов
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
            fullname: Полное имя ("Иванов Иван"): "Юрий Каменский"

        Returns:
            Процент схожести (0-1)
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
                # "Каменские" vs "Каменский" -> "Каменск" vs "Каменск" (первые 80%)
                ratio = SequenceMatcher(None, surname_lower, fullname_surname).ratio()
                return ratio

            # Вычислить степень схожести по всему имени
            ratio = SequenceMatcher(None, surname_lower, fullname_lower).ratio()
            return ratio
        except:
            return 0.0

    def _normalize_author_if_needed(self, author: str) -> str:
        """
        Нормализовать автора из ИФ (Имя Фамилия) в ФИ (Фамилия Имя).
        Для одноимённых авторов (1 слово) вернуть как есть.

        Важно: Аббревиатуры типа "А. Живой" НЕ упрощать! Они обрабатываются
        на этапе expand_abbreviated_authors через поиск по словарю авторов.

        Args:
            author: Имя автора в любом формате

        Returns:
            Нормализованное имя ФИ или исходное если не удалось определить
        """
        if not author:
            return author

        parts = author.strip().split()

        # Если одно слово - одноимённый автор, вернуть как есть
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
                # Переставить на ФИ
                return f"{parts[1]} {parts[0]}"

            # Случай 2: "А. Живой" → ОСТАВИТЬ КАК ЕСТЬ
            # Аббревиатуры обрабатываются отдельно через expand_abbreviated_authors
            if "." in first_part and len(first_part) <= 2:
                # Это инициал, оставить как есть для дальнейшего расширения
                return author

            # Случай 3: Уже в ФИ формате ("Каменский Юрий") или оба - имена/не имена - оставить как есть
            # Если оба в списке имён или оба не в списке - оставить как есть
            return author

        # Больше 2 слов - сложное имя, оставить как есть (Мария-Антуанетта и т.п.)
        return author

    def _extract_all_metadata_at_once(self, fb2_path: Path) -> dict:
        """Извлечь все метаданные FB2 за один проход SAX парсера.

        Returns:
            dict с ключами: title, authors, series, series_number, genre
        """
        try:
            handler = FB2SAXHandler()
            encoding = self._detect_encoding(fb2_path)
            if not encoding:
                encoding = 'utf-8'

            raw_bytes = fb2_path.read_bytes()

            # Если объявленная кодировка — не UTF-8, но байты валидны как UTF-8,
            # патчим XML-декларацию чтобы SAX-парсер не переключился на latin-1/etc.
            if encoding == 'utf-8':
                raw_bytes = re.sub(
                    rb'(<\?xml[^>]*encoding\s*=\s*["\'])([^"\']+)(["\'])',
                    rb'\1utf-8\3',
                    raw_bytes[:256],
                ) + raw_bytes[256:]

            parser = xml.sax.make_parser()
            parser.setContentHandler(handler)
            try:
                xml.sax.parseString(raw_bytes, handler)
            except xml.sax.SAXParseException:
                # Файл может содержать частичные UTF-8 последовательности в теле,
                # но метаданные в начале файла уже успели извлечься — используем их.
                pass

            # Формируем строку авторов (с дедупликацией)
            authors_parts = []
            seen_lower: set = set()
            for author in handler.authors:
                parts = [
                    author.get('first_name', '').strip(),
                    author.get('middle_name', '').strip(),
                    author.get('last_name', '').strip(),
                ]
                name = ' '.join(p for p in parts if p)
                if name and name.lower() not in seen_lower:
                    authors_parts.append(name)
                    seen_lower.add(name.lower())
            authors_str = '; '.join(authors_parts)

            # Если одна серия встречается несколько раз с разными номерами — это компиляция.
            # Собираем номера по имени серии → если их ≥ 2, формируем диапазон "min-max".
            primary_series = handler.series_name.strip()
            series_number = handler.series_number.strip()
            if primary_series:
                nums = []
                for sname, snum in handler._all_sequences:
                    if sname.strip() == primary_series and snum.strip():
                        try:
                            nums.append(int(snum.strip()))
                        except ValueError:
                            pass
                if len(nums) >= 2:
                    lo, hi = min(nums), max(nums)
                    series_number = f'{lo}-{hi}'
                elif nums:
                    series_number = str(nums[0])

            return {
                'title': handler.book_title.strip() or '',
                'authors': authors_str,
                'series': primary_series,
                'series_number': series_number,
                'genre': ', '.join(handler.genres),
            }
        except Exception:
            return {'title': '', 'authors': '', 'series': '', 'series_number': '', 'genre': ''}

    def reload_config(self):
        """
        Перезагрузить конфигурацию и паттерны.
        """
        self.settings.load()
        self.author_processor.reload_patterns()


if __name__ == '__main__':
    # Простой тест
    extractor = FB2SAXExtractor()
    print("FB2SAXExtractor инициализирован")
    print(f"AuthorProcessor: {extractor.author_processor}")
    print("Приоритеты извлечения:")
    for priority in AuthorExtractionPriority.ORDER:
        print(f"  {priority}: {AuthorExtractionPriority.get_name(priority)}")