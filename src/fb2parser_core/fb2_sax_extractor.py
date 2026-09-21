import xml.sax
import xml.sax.handler
from pathlib import Path
from typing import List, Optional, Tuple
import re

try:
    from settings_manager import SettingsManager
except ImportError:
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
            seq_name = attrs.get('name', '')
            seq_num  = attrs.get('number', '')
            # Баг №81 (docs/quality-roadmap.md): реальный случай — файл несёт
            # несколько <sequence> тегов, например настоящую пронумерованную
            # серию первой ("Траун. Доминация" number="2") и родовую
            # группировку/вселенную второй ("Звёздные Войны", без номера) — а
            # у соседнего тома того же цикла порядок тегов в файле обратный.
            # Старое правило "последний тег побеждает" (чистая случайность
            # порядка в исходном файле) давало РАЗНУЮ metadata_series для
            # соседних томов одной и той же серии. Тег С НОМЕРОМ — это и есть
            # настоящая пронумерованная серия (а не родовая группировка без
            # номера) и потому предпочитается независимо от порядка в файле;
            # первый уже найденный номерной тег не переопределяется более
            # поздним ненумерованным. Диапазон series_number по нескольким
            # томам того же имени пересчитывается отдельно ниже, из
            # `_all_sequences`, поэтому ранняя фиксация здесь этому не мешает.
            if seq_name:
                if seq_num:
                    if not self.series_number:
                        self.series_name = seq_name
                        self.series_number = seq_num
                    elif self.series_number == '0' and seq_num != '0':
                        # Баг №110: первый пронумерованный тег был number="0" —
                        # почти всегда организационный корень/вселенная автора
                        # (см. коммент про баг №81 выше), а не настоящая
                        # позиция в серии. Раз у соседнего тега номер НЕ
                        # нулевой — это и есть настоящая пронумерованная
                        # подсерия, ей и доверяем больше независимо от
                        # порядка тегов в файле. Если оба нулевые — сравнивать
                        # нечего, оставляем прежнее поведение (первый победил).
                        self.series_name = seq_name
                        self.series_number = seq_num
                elif not self.series_name:
                    self.series_name = seq_name
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

    Баг №105: раньше класс реализовывал ту же многоуровневую приоритетную
    стратегию извлечения авторов, что и `fb2_author_extractor.py`
    (`resolve_author_by_priority` + ~15 поддерживающих методов) — ни
    один внешний вызывающий к ней не обращался. Убрано целиком (см.
    docs/quality-roadmap.md, баг №105). `_extract_metadata_with_sax()`
    сохранён — у него нет внешних вызывающих в продакшен-коде, но есть
    прямое тестовое покрытие (`test_sax_extractor_initials.py`).
    """

    def __init__(self, config_path: str = 'config.json'):
        """
        Инициализация SAX экстрактора авторов FB2.

        Args:
            config_path: Путь к файлу конфигурации
        """
        self.settings = SettingsManager(config_path)

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
                # Раздвигаем слитные инициалы ("С.А." → "С. А.") — ТОЛЬКО в
                # middle_name, не во всём объединённом имени: см. пояснение
                # и реальный случай ("N.B." псевдоним без фамилии) в
                # _extract_all_metadata_at_once().
                middle_name = author.get('middle_name', '').strip()
                if middle_name:
                    middle_name = re.sub(r'([А-ЯЁA-Z])\.(?=[А-ЯЁA-Z]\.)', r'\1. ', middle_name)
                    parts.append(middle_name)
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
                # Составные инициалы без пробела внутри тега <middle-name> —
                # "С.А." вместо "С. А." — встречаются в реальных FB2 (пример:
                # серия "Пространство"/"The Expanse", автор "Джеймс С.А.
                # Кори"). Без пробела это выглядит как ОДИН непрерывный
                # токен — дальнейшие проходы (нормализация порядка слов,
                # консенсус по серии, author_surname_conversions) ожидают
                # отдельные "И." "О." токены и не распознают такую склейку,
                # из-за чего файл с ней не проходит те же преобразования,
                # что и соседи по серии с обычным написанием ("Джеймс
                # Кори"/"Джеймс С. А. Кори") — и в итоге получает другого
                # "финального" автора при полной сборке библиотеки.
                # Раздвигаем такие инициалы пробелом здесь же, у источника —
                # ТОЛЬКО в middle_name, не в объединённой строке целиком:
                # иначе цепляет и самостоятельные псевдонимы-инициалы без
                # фамилии вовсе (напр. "N.B." — <first-name>N.B.</first-name>
                # с пустым last-name, реальный случай "N.B. ОЯШ 1-2.fb2") —
                # такие двухбуквенные "имена" не разделяются и не переставляются
                # порядком слов, это цельный псевдоним, а не имя+фамилия.
                middle_name = re.sub(
                    r'([А-ЯЁA-Z])\.(?=[А-ЯЁA-Z]\.)', r'\1. ',
                    author.get('middle_name', '').strip(),
                )
                parts = [
                    author.get('first_name', '').strip(),
                    middle_name,
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

