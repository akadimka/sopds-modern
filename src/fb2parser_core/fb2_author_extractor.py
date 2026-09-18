"""
Модуль для парсинга FB2 файлов и извлечения информации об авторах.

Баг №105: раньше файл реализовывал многоуровневую приоритетную стратегию
извлечения авторов (папка/имя файла/метаданные + слияние по приоритету) —
~1900 из 2254 строк. Ни один внешний вызывающий (`pass1_read_files.py`,
`genre_scan_service.py`, `regen_csv.py`, `fb2parser_web/genre_conflict_check.py`)
никогда не обращался к этой стратегии — извлечение автора по факту
выполняют `passes/pass2_filename.py`/`pass2_series_filename.py` и
consensus-пассы, а не этот класс. Убрано целиком (подтверждено
grep-ом по всему репозиторию перед удалением — см.
docs/quality-roadmap.md, баг №105). Остались только 4 метода, которые
реально вызываются извне.
"""

import re
from pathlib import Path

try:
    from settings_manager import SettingsManager
except ImportError:
    from .settings_manager import SettingsManager


class FB2AuthorExtractor:
    """Извлечение метаданных (заголовок/авторы/серия/жанр) из FB2 файлов.

    Единая точка входа — `_extract_all_metadata_at_once()` (используется
    `passes/pass1_read_files.py`) — и отдельно `_extract_genres_from_fb2()`
    (используется `genre_scan_service.py`,
    `fb2parser_web/genre_conflict_check.py`).
    """

    def __init__(self, config_path: str = 'config.json'):
        """
        Инициализация экстрактора авторов FB2.

        Args:
            config_path: Путь к файлу конфигурации
        """
        self.settings = SettingsManager(config_path)

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
    
    def _extract_genres_from_fb2(self, fb2_path: Path) -> str:
        """Извлечь жанры из FB2 файла.

        Ищет теги <genre> в <title-info> и объединяет их через запятую.

        Args:
            fb2_path: Path к FB2 файлу

        Returns:
            Жанры через запятую или пустая строка
        """
        try:
            # 65 536 байт достаточно для любого <title-info> — он всегда в
            # начале файла (см. _extract_all_metadata_at_once). Без этого
            # лимита сканер жанров читал и декодировал файл целиком ради
            # тега в первых байтах — на компиляциях (5-20+ МБ, встроенные
            # обложки/иллюстрации) это и есть основная причина медленного
            # сканирования жанров по всей библиотеке.
            content = self._detect_correct_encoding(fb2_path, max_bytes=65536)
            
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
    
