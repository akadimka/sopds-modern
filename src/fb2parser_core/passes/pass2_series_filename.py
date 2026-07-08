"""
PASS 2 для СЕРИЙ: Извлечение серий из имён файлов.
Аналог pass2_filename.py (для авторов) но специализирован на СЕРИИ.

Обновление: Добавлена УНИФИКАЦИЯ series_source
================================================================
Для файлов одного автора с одинаковой серией но разными sources
(например: File 1-2 с source="metadata", File 3 с source="filename"):
1. Все такие файлы группируются в _apply_cross_file_consensus()
2. Source унифицируется с приоритетом: "filename" > "metadata" > "consensus"
3. Результат: ВСЕ файлы одного автора с одной серией имеют ОДИНАКОВЫЙ source

Пример решения (Бродяга - Аскеров):
  БЫЛО:
    File 1: series_source="metadata"
    File 2: series_source="metadata"  
    File 3: series_source="filename"
  
  СТАЛО:
    File 1: series_source="filename" ✅
    File 2: series_source="filename" ✅
    File 3: series_source="filename" ✅
"""

import re
import sys
import unicodedata
from pathlib import Path
from typing import Dict, List

try:
    from extraction_constants import FILE_EXTENSION_FOLDER_NAMES, is_no_series_folder
except ImportError:
    from ..extraction_constants import FILE_EXTENSION_FOLDER_NAMES, is_no_series_folder

try:
    from series_normalizer import _nfc_lower_yo
except ImportError:
    def _nfc_lower_yo(s: str) -> str:  # type: ignore[misc]
        return unicodedata.normalize('NFC', s).lower().replace('ё', 'е')


def _norm_s(s: str) -> str:
    """Нормализация строки для сравнения: NFC + lower + ё→е + схлопывание пробелов."""
    return re.sub(r'\s+', ' ', _nfc_lower_yo(s)).strip()


# Паттерн «том/книга/часть/... N» — компилируется один раз для всего модуля.
# Полный набор слов (свиток, выпуск, арка — СИ-специфика).
_TOM_WORD_RE = re.compile(
    r'\b(?:свиток|том|книга|часть|выпуск|арка|book|vol\.?|part)\s+(\d{1,4})\b',
    re.IGNORECASE | re.UNICODE,
)


def _author_matches_folder(proposed_author: str, folder_part: str) -> bool:
    """Проверить, является ли folder_part папкой автора proposed_author.

    Обрабатывает:
    - Вхождение строки (быстрый путь)
    - Любое значимое слово автора как подстрока папки (для "Питер Ф. Гамильтон" в "...Гамильтон)")
    - Форму множественного числа фамилии: "Живовы" ↔ "Живов" (startswith)
    - Несколько авторов с союзом "и": "Живовы Георгий и Геннадий" ↔
      "Живов Геннадий, Живов Георгий" (все уникальные фамилии есть в папке)
    """
    if not proposed_author or not folder_part:
        return False

    proposed_lower = proposed_author.lower().replace('ё', 'е')
    folder_lower = folder_part.lower().replace('ё', 'е')

    # Быстрый путь: вхождение строки
    if proposed_lower in folder_lower or folder_lower in proposed_lower:
        return True

    # Дополнительный быстрый путь: любое значимое слово автора (≥4 букв)
    # присутствует как подстрока в имени папки.
    # Это покрывает случай когда автор ещё не нормализован (формат "Имя Фамилия"),
    # а папка содержит "(Питер Гамильтон)" — "гамильтон" найдётся как подстрока.
    for word in proposed_lower.split():
        word = word.strip('.').strip(',')
        if len(word) >= 4 and word in folder_lower:
            return True

    # Извлечь уникальные фамилии (первое слово каждого автора после split по , ;)
    surnames = []
    for author in re.split(r'[,;]', proposed_author):
        words = author.strip().replace('ё', 'е').split()
        if words:
            surnames.append(words[0].lower())
    unique_surnames = list(dict.fromkeys(surnames))
    if not unique_surnames:
        return False

    folder_words = [w for w in re.split(r'[\s,;\-\(\)]+', folder_lower) if w]

    # Каждая уникальная фамилия должна совпадать с хотя бы одним словом папки
    # (startswith для формы мн. числа: живов → живовы)
    for surname in unique_surnames:
        if not any(fw == surname or fw.startswith(surname) for fw in folder_words):
            return False

    return True

try:
    from BookRecord import BookRecord
except ImportError:
    # Если прямой импорт не работает, попробовать относительный
    from dataclasses import dataclass
    @dataclass
    class BookRecord:
        file_path: str = ""
        metadata_authors: str = ""
        proposed_author: str = ""
        author_source: str = ""
        metadata_series: str = ""
        proposed_series: str = ""
        series_source: str = ""
        file_title: str = ""

try:
    from logger import Logger
except ImportError:
    from ..logger import Logger

try:
    from settings_manager import SettingsManager
except ImportError:
    from ..settings_manager import SettingsManager

try:
    from name_normalizer import AuthorName
except ImportError:
    from ..name_normalizer import AuthorName

try:
    from pattern_converter import compile_patterns
except ImportError:
    from ..pattern_converter import compile_patterns

try:
    from block_level_pattern_matcher import BlockLevelPatternMatcher
except ImportError:
    from ..block_level_pattern_matcher import BlockLevelPatternMatcher


class BlockLevelPatternSelector:
    """Выбирает паттерн на основе анализа структурных блоков файла"""
    
    @staticmethod
    def analyze_filename_blocks(filename: str) -> dict:
        """Разбирает файл на структурные блоки"""
        
        # Извлекаем содержимое скобок
        bracket_match = re.search(r'\(([^)]+)\)\s*$', filename)
        
        parts = {
            'filename': filename,
            'has_brackets': bool(bracket_match),
            'content_in_brackets': bracket_match.group(1).strip() if bracket_match else None,
            'before_brackets': filename[:bracket_match.start()].strip() if bracket_match else filename,
        }
        
        # Анализируем "до скобок"
        before = parts['before_brackets']
        parts['before_bracket_parts'] = {
            'has_comma': ',' in before,
            'comma_count': before.count(','),
            'has_dot': '.' in before,
            'has_dash': ' - ' in before,
        }
        
        # Анализируем содержимое скобок - считаем иерархию (точки внутри)
        if parts['has_brackets'] and parts['content_in_brackets']:
            bracket_content = parts['content_in_brackets']
            # Количество точек + 1 = количество уровней
            # "Сид 1. Принцип талиона 1. Геката 1" → 2 точки = 3 уровня
            parts['bracket_levels'] = bracket_content.count('. ') + 1
        else:
            parts['bracket_levels'] = 0
        
        return parts
    
    @staticmethod
    def analyze_pattern_blocks(pattern: str) -> dict:
        """Разбирает что требует паттерн"""
        
        bracket_section = None
        before_brackets = pattern  # По умолчанию весь паттерн перед скобками
        
        if '(' in pattern and ')' in pattern:
            bracket_start = pattern.find('(')
            bracket_section = pattern[bracket_start:]
            before_brackets = pattern[:bracket_start].strip()
        
        # Определяем требуемое количество уровней в скобках
        # "Series" → 1 уровень
        # "Series. service_words" → 2 уровня
        # "Series. Title. service_words" → 3 уровня
        bracket_levels = 0
        if bracket_section:
            # Считаем точки внутри скобок: "Series. Title. service_words" → 2 точки = 3 уровня
            bracket_levels = bracket_section.count('. ') + 1
        
        reqs = {
            'pattern': pattern,
            'requires_comma': ',' in before_brackets,  # Проверяем только ДО скобок
            'requires_dot': '. ' in before_brackets,   # Проверяем только ДО скобок
            'requires_dash': ' - ' in before_brackets,  # Проверяем только ДО скобок
            'requires_brackets': '(' in pattern,
            'bracket_requires_service_words': 'service_words' in (bracket_section or ''),
            'bracket_complexity': (bracket_section or '').count('.') + 1 if bracket_section else 0,
            'bracket_levels': bracket_levels,  # Количество уровней иерархии требуемое паттерном
        }
        
        return reqs
    
    @staticmethod
    def score_blocks(file_blocks: dict, pattern_reqs: dict) -> int:
        """Оценивает соответствие структур файла и паттерна"""
        
        score = 0
        
        # ════ ПРОВЕРКА ОСНОВНОЙ СТРУКТУРЫ ════
        
        # Скобки
        if pattern_reqs['requires_brackets']:
            if not file_blocks['has_brackets']:
                return -999
            score += 15
        else:
            if not file_blocks['has_brackets']:
                score += 10
        
        # Запятая
        before = file_blocks['before_bracket_parts']
        if pattern_reqs['requires_comma']:
            if not before['has_comma']:
                return -999
            score += 10
        else:
            if before['has_comma']:
                score -= 5
            else:
                score += 10
        
        # Точка
        if pattern_reqs['requires_dot']:
            if not before['has_dot']:
                return -999
            score += 10
        else:
            if before['has_dot']:
                score -= 3
            else:
                score += 8
        
        # Тире
        if pattern_reqs['requires_dash']:
            if not before['has_dash']:
                return -999
            score += 10
        else:
            if not before['has_dash']:
                score += 10
        
        # ════ ПРОВЕРКА СОДЕРЖИМОГО СКОБОК ════
        
        if file_blocks['has_brackets'] and pattern_reqs['requires_brackets']:
            # ════ ПРОВЕРКА СОВПАДЕНИЯ ИЕРАРХИИ ════
            # Количество уровней в файле должно совпадать с требуемым паттерном
            # Но это не hard disqualifier - просто штраф в score
            file_levels = file_blocks.get('bracket_levels', 0)
            pattern_levels = pattern_reqs['bracket_levels']
            
            # НАКАЗЫВАЕМ за несовпадение иерархии:
            # файл с 3 уровнями не должен совпадать с паттерном на 1 уровень
            levels_diff = abs(file_levels - pattern_levels)
            if levels_diff > 0:
                # Штраф -5 за каждый уровень разницы
                score -= (5 * levels_diff)
            else:
                # Бонус за совпадение иерархии
                score += 10
            
            bracket_content = file_blocks['content_in_brackets'] or ''
            
            # Проверяем наличие служебных слов (Дилогия, Тетралогия и т.д.), но НЕ числовых диапазонов!
            has_service_word = False
            # Служебные слова: полные слова, не часть другого слова
            service_word_patterns = r'\b(Дилогия|Трилогия|Тетралогия|Пенталогия|Цикл|Серия)\b'
            has_service_word = bool(re.search(service_word_patterns, bracket_content, re.IGNORECASE))
            
            if pattern_reqs['bracket_requires_service_words']:
                if has_service_word:
                    score += 5
                else:
                    # Паттерн требует service_words, но их нет
                    score -= 5
            else:
                # Паттерн НЕ требует service_words
                if has_service_word:
                    # В файле есть, но паттерн не ожидает
                    score -= 3
                else:
                    # Паттерн не ожидает, и их нет
                    score += 5
            
            # Сложность: паттерн требует определённое кол-во уровней точками
            file_complexity = bracket_content.count('.') + 1
            pattern_complexity = pattern_reqs['bracket_complexity']
            
            if file_complexity != pattern_complexity:
                # Штраф за несоответствие сложности
                score -= abs(file_complexity - pattern_complexity) * 3
        
        return score


class Pass2SeriesFilename:
    """Извлечение серий из имён файлов."""

    # Источники серий, которые считаются «папочными» (приоритет над filename/metadata)
    _FOLDER_SOURCES = frozenset({
        'folder_dataset', 'folder_hierarchy', 'folder_meta_consensus',
        'folder_metadata_confirmed', 'no_series_folder',
    })

    # Папочные источники для arc-нумерации (без no_series_folder — там серии нет)
    _FOLDER_SOURCES_ARC = frozenset({
        'folder_dataset', 'folder_hierarchy', 'folder_meta_consensus',
        'folder_metadata_confirmed',
    })

    def __init__(self, logger: Logger = None, male_names: set = None, female_names: set = None, config_path: str = None):
        self.logger = logger or Logger()
        if config_path is None:
            config_path = str(Path(__file__).parent.parent / 'config.json')
        self.settings = SettingsManager(config_path)
        self.block_selector = BlockLevelPatternSelector()
        self.male_names = male_names or set()
        self.female_names = female_names or set()
        # Create block matcher with known author names
        self.block_matcher = BlockLevelPatternMatcher(
            service_words=list(self.settings.get_list('service_words')),
            male_names=self.male_names,
            female_names=self.female_names
        )  # NEW: для точного извлечения серий
        # Получить списки из config.json
        self.collection_keywords = self.settings.get_list('collection_keywords')
        self.variant_folder_keywords = [kw.lower() for kw in (self.settings.get_list('variant_folder_keywords') or [])]
        self.service_words = self.settings.get_list('service_words')
        self.filename_blacklist = self.settings.get_list('filename_blacklist')
        # Пользовательский список папок «без серии» (дополняет встроенный NO_SERIES_FOLDER_NAMES)
        self.no_series_names = self.settings.get_no_series_folder_names()
        
        # Получить паттерны из конфига
        self.file_patterns = self.settings.get_list('author_series_patterns_in_files') or []
        self.metadata_patterns = self.settings.get_list('series_patterns_in_metadata') or []
        self.folder_patterns_raw = self.settings.get_author_series_patterns_in_folders() or []

        # Скомпилировать паттерны в regex (один раз при инициализации)
        # Включает как file_patterns, так и metadata_patterns
        self.compiled_file_patterns = compile_patterns(self.file_patterns)
        self.compiled_metadata_patterns = compile_patterns(self.metadata_patterns)
        self.compiled_folder_patterns = compile_patterns(self.folder_patterns_raw)
        
        # Флаг: последний вызов _extract_series_from_brackets вернул иерархическую серию
        # (MainSeries N из "MainSeries N. SubSeries M-K") — не убирать trailing number
        self._last_was_hierarchical = False
    
    @staticmethod
    def _is_strong_match(author: str, folder: str) -> bool:
        """Строгое совпадение автора с именем папки (подстрока или совпадение слов)."""
        a = author.lower().replace('ё', 'е')
        f = folder.lower().replace('ё', 'е')
        if a in f or f in a:
            return True
        f_words = set(re.sub(r'[^\w]', ' ', f).split())
        if f_words:
            for single_author in re.split(r'[;,]', a):
                sa_words = set(single_author.strip().split())
                if sa_words and f_words == sa_words:
                    return True
        return False

    def _extract_series_from_folder_name(self, folder_name: str, author_hint: str = '') -> str:
        """
        Извлечь название серии из имени папки.
        Убирает ведущие номера ("1. ", "2) " и т.д.)
        и всё перед скобками ("1941 (Иван Байбаков)" → "1941"),
        но только если содержимое скобок — многословное (похоже на имя автора)
        и совпадает с author_hint. Однословные скобки сохраняются ("1 (Первый)" → "1 (Первый)").

        Args:
            folder_name: Имя папки
            author_hint: proposed_author записи для проверки скобок-автора

        Returns:
            Очищенное название серии
        """
        # Убрать ведущие номера ("1. ", "2) " и т.д.)
        cleaned = re.sub(r'^\d+[\.\)\-]\s+', '', folder_name).strip()
        if cleaned and cleaned != folder_name:
            folder_name = cleaned

        # Скобки убираем только если содержимое многословное (похоже на "Имя Фамилия")
        # и либо совпадает с автором, либо author_hint не задан.
        # Однословные скобки ("1 (Первый)") сохраняем — это часть названия серии.
        match = re.match(r'^(.+?)\s*\(([^)]+)\)\s*$', folder_name)
        if match:
            before = match.group(1).strip()
            inside = match.group(2).strip()
            inside_words = inside.split()
            if len(inside_words) >= 2:
                # Многословное содержимое в скобках — обычно имя автора (дизамбигуатор).
                # НО: если скобки содержат цифры ("Хроники 7-8", "тт. 1-4") —
                # это контекстный суффикс нумерации, не имя автора. Оставляем.
                # Примеры убираем: "Русич (Посняков Андрей)" → "Русич",
                #                  "Орда (Посняков Андрей)" → "Орда".
                # Примеры сохраняем: "Возвращение в Тооредаан (Хроники 7-8)" — оставить как есть.
                if not any(c.isdigit() for c in inside):
                    folder_name = before
            # Однословное содержимое — оставляем скобки как есть ("Алхимик (завершён)")

        # По правилам русского языка после запятой всегда должен идти пробел
        folder_name = re.sub(r',(\S)', r', \1', folder_name)

        return folder_name.strip()
    
    def _prepass_folder_setup(self, records: List[BookRecord]) -> None:
        """PRE-PASS: подготовка авторов и серий из папок до основного цикла.

        1. Распространяет автора из папки-предка (_propagate_ancestor_folder_authors).
        2. Применяет паттерны «Серия (Автор)» из config к именам папок.
        3. Унифицирует автора внутри папок (_unify_series_folder_authors).
        """
        # Шаг 1: авторы из папок-предков — нужно до основного цикла, чтобы
        # сопоставление author_folder работало даже для "Соавторство"/"Сборник".
        self._propagate_ancestor_folder_authors(records)

        # Шаг 2: паттерны «Серия (Автор)» — исправляет случаи когда папка
        # классифицирована как авторская, но на самом деле является серийной.
        # Пример: "Князь Игорь (Аксеничев Олег)" → author="Аксеничев Олег", series="Князь Игорь"
        if self.compiled_folder_patterns:
            _count = 0
            _FOLDER_AUTHOR_SRC = {
                'folder_dataset', 'folder_hierarchy',
                'metadata_folder_confirmed', 'folder_multiauthor',
            }
            # folder_parse_limit — глубина поиска папок вверх от файла
            _fpl = int(self.settings.get('folder_parse_limit', 10)
                       if isinstance(self.settings, dict)
                       else getattr(self.settings, 'settings', {}).get('folder_parse_limit', 10))

            for record in records:
                if record.author_source not in _FOLDER_AUTHOR_SRC:
                    continue
                path_parts = Path(record.file_path).parts
                # Ограничиваем глубину поиска по folder_parse_limit
                relevant_parts = path_parts[max(0, len(path_parts) - 1 - _fpl):-1]
                for part in relevant_parts:
                    for _p_str, _p_re, _p_groups in self.compiled_folder_patterns:
                        if 'series' not in _p_groups or 'author' not in _p_groups:
                            continue
                        m = _p_re.match(part)
                        if not m:
                            continue
                        extracted_series = m.group('series').strip()
                        extracted_author = m.group('author').strip()
                        if not extracted_series or not extracted_author:
                            continue
                        if len(extracted_author.replace(' ', '')) < 3:
                            continue
                        cur_author_norm = record.proposed_author.strip().lower().replace('ё', 'е')
                        ext_series_norm = extracted_series.lower().replace('ё', 'е')
                        ext_author_norm = extracted_author.lower().replace('ё', 'е')

                        # Case A: папка взята как автор вместо серии — исправить автора.
                        # Требуем ≥2 слов в extracted_author: одно слово («Базилио») —
                        # псевдоним, папка «Риддер Аристарх (Базилио)» — авторская.
                        _ext_author_words = [w for w in re.sub(r'[^\w]', ' ', ext_author_norm).split() if w]
                        author_was_series = (
                            cur_author_norm == ext_series_norm
                            and len(_ext_author_words) >= 2
                        )

                        # Case B: автор уже верный — длинное слово из extracted_author
                        # присутствует в proposed_author.
                        _ext_words = [w for w in re.sub(r'[^\w]', ' ', ext_author_norm).split() if len(w) > 3]
                        author_matches = bool(_ext_words) and any(w in cur_author_norm for w in _ext_words)

                        if not author_was_series and not author_matches:
                            continue

                        if author_was_series:
                            try:
                                from name_normalizer import AuthorName as _AN
                            except ImportError:
                                from ..name_normalizer import AuthorName as _AN
                            _an = _AN(extracted_author)
                            canonical_author = _an.normalized if (_an.is_valid and _an.normalized) else extracted_author
                            record.proposed_author = canonical_author
                            record.author_source = 'folder_dataset'

                        if not record.proposed_series or record.series_source not in self._FOLDER_SOURCES:
                            record.proposed_series = extracted_series
                            record.series_source = 'folder_dataset'

                        # Если папка содержит несколько авторов "(Барчук, Прядеев)",
                        # добавляем нематченных соавторов из metadata_authors.
                        # НО: не расширяем если автор уже закреплён родительским folder_dataset —
                        # папка-датасет имеет наивысший приоритет, соавторы из подпапки не добавляются.
                        if (author_matches and ',' in extracted_author and record.metadata_authors
                                and not record.author_source.startswith('folder_dataset')):
                            _folder_surnames = [
                                re.sub(r'[^\w]', '', s).lower().replace('ё', 'е')
                                for s in re.split(r',\s*', extracted_author)
                            ]
                            _cur_auth_norm = record.proposed_author.lower().replace('ё', 'е')
                            for _fsur in _folder_surnames:
                                if len(_fsur) <= 3 or _fsur in _cur_auth_norm:
                                    continue
                                # Ищем полное имя в metadata_authors
                                for _meta_part in re.split(r'[;,]', record.metadata_authors):
                                    _mp = _meta_part.strip()
                                    if not _mp:
                                        continue
                                    _mp_norm = _mp.lower().replace('ё', 'е')
                                    if _fsur in _mp_norm and _mp_norm not in _cur_auth_norm:
                                        record.proposed_author = record.proposed_author + ', ' + _mp
                                        _cur_auth_norm = record.proposed_author.lower().replace('ё', 'е')
                                        break

                        _count += 1
                        break
            if _count:
                self.logger.log(f"[PASS 2] Applied folder patterns to {_count} records")
                print(f"[PASS 2] Applied folder Series(Author) patterns to {_count} records")

        # Шаг 3: унификация автора внутри папок
        self._unify_series_folder_authors(records)

    def execute(self, records: List[BookRecord]) -> None:
        """
        ПРОСТАЯ И ПРАВИЛЬНАЯ ЛОГИКА - независима от папок!
        ===================================================
        Логика:
        1. Если series_source == "folder_dataset" → skip (папка дала series)
        2. Если proposed_series не пусто → skip (уже выбрана)  
        3. ВСЕГДА пробовать паттерны (неважно file_depth!)
        4. Fallback на metadata только если паттерны не дали
        """
        self._prepass_folder_setup(records)

        # Кэш Path.parts: один и тот же file_path встречается в нескольких проходах
        _parts_cache: dict = {}
        for record in records:
            self._process_single_record(record, _parts_cache)
        # 🔑 Многоавторные папки — коллекции, не серии.
        # Если папка содержит книги РАЗНЫХ авторов → её имя не является серией.
        self._clear_multiauthor_folder_series(records)

        # 🔑 НОВОЕ: Папочный консенсус
        # Если папка содержит файлы с series_source = "folder_dataset",
        # то ВСЕ файлы в этой папке должны получить одинаковую серию из папки
        self._apply_folder_consensus(records)

        # 🔑 УНИФИКАЦИЯ АВТОРА внутри папки
        # Если в папке есть файлы с folder_dataset — их автор применяется ко всем
        # файлам в папке с source='metadata_folder_confirmed' (исправляет файлы
        # с испорченными метаданными, которые не смогли пройти валидацию propagate).
        self._unify_folder_author_source(records)

        # 🔑 УНИФИКАЦИЯ источника серии внутри папки
        # Если хотя бы один файл в папке получил folder_hierarchy — значит папка
        # является авторитетом для всей папки. Все metadata_folder_confirmed файлы
        # в той же папке должны получить folder_hierarchy с той же серией.
        self._unify_folder_series_source(records)
        self._split_umbrella_folder_series(records)

        self._postpass_metadata_fallback(records)

        self._postpass_arc_numbering(records)

        # Нормализуем рассогласование «Серия\Подсерия» vs «Серия» у одного автора
        self._resolve_hierarchical_flat_mismatch(records)
        # Разбиваем «Серия N. Заголовок. Том M» на подсерии «Серия N»
        self._split_numbered_subseries(records)
        # Обнаруживаем именованные дуги «Серия 0N. ArcTitle [ArcN]» → «Серия\ArcTitle»
        self._detect_named_arcs(records)

        # Коррекция series_number: числовой префикс имени файла перебивает metadata.
        # «01_Якудза...» → series_number=1 даже если metadata ошибочно говорит 3.
        self._correct_series_number_from_filename(records)

        # Пометить устаревшие дубликаты (старый/новый вариант одной книги)
        self._mark_duplicate_variants(records)

        # 🔑 ФИНАЛЬНЫЙ КОСТЫЛЬ: многоавторные папки "Серия (Фамилия и др)"
        # После всей обработки исправляем автора для ВСЕХ файлов под такими папками.
        # ВАЖНО: вызываем до _unify_folder_series_source повторно, потому что
        # при первом вызове авторы были разные → guard "len(authors)>1" пропустил папку.
        self._fix_multiauthor_folders(records)

    def _postpass_metadata_fallback(self, records: List[BookRecord]) -> None:
        """Последний шанс: назначить серию из metadata_series если proposed_series пусто.

        Применяет валидацию как в основном цикле. Также балансирует кавычки
        и убирает завершающий backslash из всех series.
        """
        for record in records:
            if record.proposed_series or not record.metadata_series:
                continue
            meta = record.metadata_series.strip()
            if record.proposed_author and meta.lower() == record.proposed_author.lower():
                continue
            meta_lower = meta.lower()
            _has_bl = False
            for bl in self.filename_blacklist:
                bl_lower = bl.lower().strip()
                if not bl_lower:
                    continue
                pat = r'(?<![а-яёa-z])' + re.escape(bl_lower) + r'(?![а-яёa-z])'
                if re.search(pat, meta_lower):
                    _has_bl = True
                    break
            if _has_bl:
                continue
            series = self._extract_series_from_metadata(meta)
            series = self._remove_blacklist_words(series)
            if not series:
                continue
            if not self._is_valid_series(series, extracted_author=record.proposed_author or None):
                continue
            record.proposed_series = self._fix_russian_grammar(series)
            record.series_source = "metadata"

        for record in records:
            if record.proposed_series:
                record.proposed_series = self._balance_quotes(record.proposed_series).rstrip('\\')

    def _postpass_arc_numbering(self, records: List[BookRecord]) -> None:
        """Дополняет proposed_series числом дуги/сезона из имени файла.

        Два пути:
        A) Автор имеет подсерии «Серия N\\...» — для плоских записей той же серии
           ищем «Серия N.» в стеме и добавляем N к proposed_series.
        B) metadata подтверждает серию — если у ВСЕХ файлов группы одинаковый N
           в стеме (не номер тома), добавляем N к proposed_series.
        """
        # --- Путь A: author_roots ---
        _author_roots: dict = {}
        for rec in records:
            if '\\' not in (rec.proposed_series or ''):
                continue
            root = rec.proposed_series.split('\\')[0].strip()
            root_base = re.sub(r'\s+\d+\s*$', '', root).strip()
            ak = _norm_s(rec.proposed_author or '')
            _author_roots.setdefault(ak, {})[_norm_s(root_base)] = root_base

        _ar_nums: dict = {}
        for _rec in records:
            if '\\' in (_rec.proposed_series or '') or not _rec.proposed_series:
                continue
            if _rec.series_source in self._FOLDER_SOURCES_ARC:
                continue
            _ak2 = _norm_s(_rec.proposed_author or '')
            _sn2 = _norm_s(_rec.proposed_series.strip())
            if _ak2 not in _author_roots or _sn2 not in _author_roots[_ak2]:
                continue
            _p2 = re.compile(re.escape(_sn2) + r'\s+(\d{1,4})\s*[.\-–—]', re.UNICODE)
            _m2 = _p2.search(_norm_s(Path(_rec.file_path).stem))
            if _m2:
                _n2 = int(_m2.group(1))
                _is_zero_padded2 = _m2.group(1).startswith('0') and len(_m2.group(1)) >= 2
                if _n2 < 1900 and not _is_zero_padded2:
                    _ar_nums.setdefault((_ak2, _sn2), set()).add(_n2)

        for record in records:
            if '\\' in (record.proposed_series or '') or not record.proposed_series:
                continue
            if record.series_source in self._FOLDER_SOURCES_ARC:
                continue
            ak = _norm_s(record.proposed_author or '')
            series_norm = _norm_s(record.proposed_series.strip())
            if ak not in _author_roots or series_norm not in _author_roots[ak]:
                continue
            _pat = re.compile(re.escape(series_norm) + r'\s+(\d{1,4})\s*[.\-–—]', re.UNICODE)
            _m = _pat.search(_norm_s(Path(record.file_path).stem))
            if _m:
                n_str = _m.group(1)
                n = int(n_str)
                if n < 1900:
                    if n_str.startswith('0') and len(n_str) >= 2:
                        if not record.series_number:
                            record.series_number = str(n)
                    else:
                        if len(_ar_nums.get((ak, series_norm), set())) >= 2:
                            continue
                        record.proposed_series = f'{record.proposed_series.strip()} {n}'

        # --- Путь B: metadata-confirmed arc ---
        _meta_arc_map: dict = {}
        _meta_arc_pat: dict = {}
        _meta_arc_recs: list = []
        for record in records:
            if '\\' in (record.proposed_series or ''):
                continue
            if not record.proposed_series or not record.metadata_series:
                continue
            if record.series_source in self._FOLDER_SOURCES_ARC:
                continue
            ms_norm = _norm_s(record.metadata_series.replace('…', '...').strip())
            ps_norm = _norm_s(record.proposed_series.strip())
            if ms_norm != ps_norm:
                continue
            ak = _norm_s(record.proposed_author or '')
            key = (ak, ps_norm)
            if key not in _meta_arc_pat:
                _meta_arc_pat[key] = re.compile(
                    re.escape(ps_norm) + r'\s+(\d{1,4})\s*[.\-–—](?!\d)', re.UNICODE
                )
                _meta_arc_map[key] = set()
            _m = _meta_arc_pat[key].search(_norm_s(Path(record.file_path).stem))
            if _m:
                n = int(_m.group(1))
                if n < 1900:
                    _meta_arc_map[key].add(n)
            _meta_arc_recs.append((record, key))

        for record, key in _meta_arc_recs:
            nums = _meta_arc_map.get(key, set())
            if len(nums) != 1:
                continue
            n = next(iter(nums))
            n_str = str(n)
            _m = _meta_arc_pat[key].search(_norm_s(Path(record.file_path).stem))
            if not _m:
                continue
            if n_str.startswith('0') and len(n_str) >= 2:
                if not record.series_number:
                    record.series_number = str(n_str.lstrip('0') or '0')
            else:
                record.proposed_series = f'{record.proposed_series.strip()} {n}'

        # --- Финал: series_number из второго числа в стеме ---
        for record in records:
            if '\\' in (record.proposed_series or '') or not record.proposed_series:
                continue
            if record.series_source in self._FOLDER_SOURCES_ARC:
                continue
            ps_norm = _norm_s(record.proposed_series.strip())
            if not re.search(r'\s+\d+$', ps_norm):
                continue
            _sn_pat = re.compile(
                re.escape(ps_norm) + r'\s*\.\s*[а-яёa-zA-ZЀ-ӿ][^\d.]*\s+(\d{1,3})\s*[.\-–—]',
                re.UNICODE,
            )
            _sm = _sn_pat.search(_norm_s(Path(record.file_path).stem))
            if _sm:
                n = int(_sm.group(1))
                if n < 1900 and str(n) != (record.series_number or ''):
                    record.series_number = str(n)

    def _apply_folder_series(self, record, parts_cache: dict) -> None:
        """Определить серию из структуры папок и записать в record."""
        # Приоритет из config.json: FOLDER_STRUCTURE=3 > FILENAME=2 > FB2_METADATA=1
        # Поиск по папкам применяется всегда, используя любой известный автор:
        # proposed_author (из папки или файла) или metadata_authors (из FB2).
        # Это гарантирует соблюдение приоритета независимо от author_source.

        # folder_parse_limit: ограничение глубины поиска папок вверх от файла
        _fpl = getattr(self, '_folder_parse_limit_cache', None)
        if _fpl is None:
            try:
                _fpl = int(self.settings.get('folder_parse_limit', 10)
                           if isinstance(self.settings, dict)
                           else getattr(self.settings, 'settings', {}).get('folder_parse_limit', 10))
            except Exception:
                _fpl = 10
            self._folder_parse_limit_cache = _fpl

        author_name = record.proposed_author or record.metadata_authors or None
        if author_name:
            path_parts = parts_cache.get(record.file_path)
            if path_parts is None:
                raw = Path(record.file_path).parts
                # Ограничиваем глубину до folder_parse_limit уровней от файла
                raw = raw[max(0, len(raw) - 1 - _fpl):]
                path_parts = tuple(
                    p for i, p in enumerate(raw)
                    if i == len(raw) - 1 or p.lower() not in FILE_EXTENSION_FOLDER_NAMES
                )
                parts_cache[record.file_path] = path_parts

            author_folder_idx = None
            for i, part in enumerate(path_parts[:-1]):
                if self._is_strong_match(author_name, part):
                    author_folder_idx = i
                    break
            if author_folder_idx is None:
                for i, part in enumerate(path_parts[:-1]):
                    if _author_matches_folder(author_name, part):
                        author_folder_idx = i
                        break

            # folder_dataset + голая фамилия (без пробелов) → раскрыть из метаданных.
            # Проверяем прямую родительскую папку файла (path_parts[-2]):
            # если она имеет паттерн "Серия (Фамилия)" с нашей фамилией — расширяем.
            # "Широков" + folder "Воин Грёзы (Широков)" + meta "Алексей Широков" → "Широков Алексей".
            if (record.author_source == "folder_dataset"
                    and record.proposed_author
                    and ' ' not in record.proposed_author
                    and record.metadata_authors
                    and record.metadata_authors not in ('[unknown]', '')
                    and len(path_parts) >= 2):
                _direct_parent = path_parts[-2]
                _surname_lc = record.proposed_author.lower().replace('ё', 'е')
                if ('(' in _direct_parent
                        and _surname_lc in _direct_parent.lower().replace('ё', 'е')):
                    try:
                        from name_normalizer import normalize_author_name as _norm_au
                    except ImportError:
                        from ..name_normalizer import normalize_author_name as _norm_au
                    _expanded = None
                    for _raw_au in record.metadata_authors.replace(';', ',').split(','):
                        _raw_au = _raw_au.strip()
                        if not _raw_au:
                            continue
                        _norm = _norm_au(_raw_au)
                        if _norm.split()[0].lower().replace('ё', 'е') == _surname_lc:
                            _expanded = _norm
                            break
                        if _surname_lc in [w.lower().replace('ё', 'е')
                                           for w in _raw_au.split()]:
                            _expanded = _norm
                            break
                    if _expanded:
                        record.proposed_author = _expanded
                        record.author_source = "metadata_folder_confirmed"

            if author_folder_idx is not None:
                i = author_folder_idx
                part = path_parts[i]
                # Папка подтвердила автора — если источник был только мета, обновляем
                if record.author_source == "metadata":
                    record.author_source = "metadata_folder_confirmed"

                # Если VARIANT B уже установил серию из папки — не перезаписываем.
                # Только обновление author_source выше допустимо.
                if record.series_source in self._FOLDER_SOURCES and record.proposed_series:
                    pass  # серия уже определена папочной структурой

                # Найдена папка автора на позиции i
                # Следующая папка (i+1) это серия (если это не файл)
                elif i + 1 < len(path_parts) - 1:  # -1 чтобы исключить сам файл
                    series_folder = path_parts[i + 1]
                    if not series_folder.endswith('.fb2'):
                        # Папка «Вне серий» / «Без серии» — явный признак отсутствия серии
                        if is_no_series_folder(series_folder, self.no_series_names):
                            record.proposed_series = ""
                            record.series_source = "no_series_folder"

                        # Если подпапка — это "вариант" / "альт. перевод" / "СИ" и т.п.,
                        # она НЕ является названием серии — серия берётся из папки автора.
                        elif self._is_variant_folder(series_folder):
                            series_name = self._extract_series_from_folder_name(part)
                            if series_name:
                                record.proposed_series = series_name
                                record.series_source = "folder_hierarchy"

                        else:
                            # Проверяем: папка-автор сама является циклом?
                            # Признак: "Серия (Автор)" — _extract_series_from_folder_name вернёт
                            # что-то отличное от исходного имени папки.
                            # НО: если скобки содержат псевдоним/псевдоним автора
                            # ("Гоблин (MeXXanik)") — это НЕ "Серия (Автор)", а папка автора.
                            author_folder_series = self._extract_series_from_folder_name(part)
                            _parens_match = re.search(r'\(([^)]+)\)\s*$', part)
                            _parens_content = _parens_match.group(1).strip() if _parens_match else ''

                            # Если скобки содержат "и др" / "et al" — это многоавторный хинт,
                            # а НЕ дизамбигуатор автора: папка является серийной, не авторской.
                            _ET_AL_RE = re.compile(
                                r'(?:и\s+др\.?|и\s+другие|et\s+al\.?|and\s+others)\s*$',
                                re.IGNORECASE
                            )
                            _is_multiauthor_hint = bool(_parens_content) and bool(_ET_AL_RE.search(_parens_content))

                            _parens_is_author = (
                                bool(_parens_content)
                                and not _is_multiauthor_hint
                                and _author_matches_folder(author_name, _parens_content)
                            )
                            has_parent_series = (
                                bool(author_folder_series) and
                                author_folder_series.strip().lower() != part.strip().lower() and
                                not _parens_is_author
                            )

                            if has_parent_series:
                                # Б) Иерархия: {цикл}\{Подсерия}
                                # Убираем суффикс "(Автор)", но сохраняем:
                                # - числовой префикс "N. " — порядковый номер подсерии
                                # - скобочный суффикс с цифрами "(Хроники 7-8)" — глобальный контекст
                                _par_m = re.search(r'\s*\(([^)]*)\)\s*$', series_folder)
                                if _par_m and any(c.isdigit() for c in _par_m.group(1)):
                                    # Содержит цифры — это контекст нумерации, не имя автора
                                    subfolder_display = series_folder.strip()
                                else:
                                    subfolder_display = re.sub(r'\s*\([^)]*\)\s*$', '', series_folder).strip()
                                record.proposed_series = f"{author_folder_series}\\{subfolder_display}"

                                # Костыль для многоавторных папок: "Серия (Фамилия и др)" →
                                # ищем полное имя в metadata и ставим "Фамилия Имя и другие".
                                if _is_multiauthor_hint:
                                    hint_surname = _ET_AL_RE.sub('', _parens_content).strip().rstrip(',').strip()
                                    hint_lower = hint_surname.lower().replace('ё', 'е')
                                    # Ищем полное нормализованное имя в proposed_author (уже "Фамилия Имя")
                                    full_name = None
                                    for pa_part in re.split(r'\s*,\s*', record.proposed_author or ''):
                                        if any(hint_lower in w.lower().replace('ё', 'е') for w in pa_part.split()):
                                            full_name = pa_part.strip()
                                            break
                                    # Fallback: поиск в metadata_authors
                                    if not full_name:
                                        for meta_a in re.split(r'[;,]', record.metadata_authors or ''):
                                            meta_a = meta_a.strip()
                                            if any(hint_lower in w.lower().replace('ё', 'е') for w in meta_a.split()):
                                                full_name = meta_a
                                                break
                                    if full_name:
                                        record.proposed_author = f"{full_name} и другие"
                                        record.author_source = 'folder_multiauthor'
                            else:
                                # Обычная папка автора → серия из папки i+1
                                # Если есть ещё папка i+2 (подсерия) — строим "Серия\Подсерия",
                                # сохраняя числовой префикс "N." в имени подсерии (порядок внутри серии).
                                if i + 2 < len(path_parts) - 1:
                                    parent_series_name = self._extract_series_from_folder_name(series_folder, record.proposed_author or '')
                                    subseries_folder = path_parts[i + 2]
                                    record.proposed_series = f"{parent_series_name or series_folder}\\{subseries_folder}"
                                else:
                                    subseries_name = self._extract_series_from_folder_name(series_folder, record.proposed_author or '')
                                    record.proposed_series = subseries_name or series_folder

                            record.series_source = "folder_hierarchy"
                elif not (record.series_source in self._FOLDER_SOURCES and record.proposed_series):
                    # Папка i содержит автора И является папкой серии одновременно
                    # (формат: "Сборник\Серия (Автор)\Файл.fb2" — нет подпапки серии)
                    # Папка имеет ВЫСШИЙ приоритет. Но если metadata_series — вариация
                    # того же названия (напр. "Барраярский цикл" и "Барраяр"), то
                    # сохраняем более точное название из FB2 тегов.

                    # ЗАЩИТА: "Издательская папка с фамилией" — папка вида
                    # "Fanzon. Наш выбор. Куанг" содержит фамилию автора как ПОСЛЕДНЕЕ слово.
                    # Такие папки — организационные, а не серийные.
                    # Серию ищем сначала по имени файла, мета только подтверждает.
                    # ИСКЛЮЧЕНИЕ: "Серия (Автор)" — фамилия в скобках является лишь дизамбигуатором,
                    # такая папка — это серия; проверяем только хвост БЕЗ скобок.
                    _part_no_parens = re.sub(r'\s*\([^)]*\)\s*$', '', part.strip()).strip()
                    _part_words = re.split(r'[\s.\-]+', _part_no_parens) if _part_no_parens else re.split(r'[\s.\-]+', part.strip())
                    _part_last_word = _part_words[-1].lower().replace('ё', 'е') if _part_words else ''
                    _author_name_clean = re.sub(r'\([^)]*\)', '', author_name).strip()
                    _author_words = set(w.lower().replace('ё', 'е') for w in _author_name_clean.split() if len(w) > 2)
                    # Check if ANY word in the folder (>2 chars) matches an author word.
                    # This covers "Таннер А" where the FIRST word "Таннер" is the surname,
                    # not just the last word (the old check only caught endings like "Куанг").
                    # Also handles inflected forms: "Киза" matches author word "Киз" via startswith.
                    _folder_contains_author = any(
                        any(
                            fw == aw or fw.startswith(aw) or aw.startswith(fw)
                            for aw in _author_words
                        )
                        for fw in (w.lower().replace('ё', 'е') for w in _part_words if len(w) > 2)
                    )
                    # Также проверяем скобочный суффикс папки: если автор совпадает
                    # с тем что в скобках — это папка "Серия (Автор)", не серия.
                    # Пример: папка "Орлов Алекс (Дарищев Вадим)", автор "Дарищев Вадим"
                    # → _part_words = ["Орлов", "Алекс"], author_words не совпадут,
                    # но в скобках "Дарищев Вадим" == author → тоже авторская папка.
                    if not _folder_contains_author:
                        _parens_in_part = re.search(r'\(([^)]+)\)', part)
                        if _parens_in_part:
                            _parens_words = set(
                                w.lower().replace('ё', 'е')
                                for w in _parens_in_part.group(1).split()
                                if len(w) > 2
                            )
                            if _parens_words & _author_words:
                                _folder_contains_author = True

                    if _folder_contains_author:
                        pass  # Не устанавливаем серию из папки → идём дальше к filename extraction
                    else:
                        series_name = self._extract_series_from_folder_name(part)
                        if series_name:
                            if record.metadata_series:
                                meta_l = record.metadata_series.lower().replace('ё', 'е')
                                folder_l = series_name.lower().replace('ё', 'е')
                                # Если одно является префиксом другого — это одна серия,
                                # просто разные формы названия → оставляем более точную.
                                if not (folder_l.startswith(meta_l) or meta_l.startswith(folder_l)):
                                    # Разные названия → папка имеет высший приоритет
                                    record.proposed_series = series_name
                                    record.series_source = "folder_hierarchy"
                                else:
                                    # Та же серия, разная форма.
                                    # Если meta_l начинается с folder_l → мета добавляет лишнее
                                    # (напр. "Ацтек (RedDetonator)" vs "Ацтек") → берём folder_name.
                                    # Если folder_l начинается с meta_l → папка добавляет описание
                                    # (напр. "Барраярский цикл" vs "Барраяр") → берём мету.
                                    if meta_l.startswith(folder_l) and len(meta_l) > len(folder_l):
                                        record.proposed_series = series_name
                                    else:
                                        record.proposed_series = record.metadata_series
                                    record.series_source = "folder_metadata_confirmed"
                            else:
                                record.proposed_series = series_name
                                record.series_source = "folder_hierarchy"
        

    def _process_single_record(self, record, parts_cache: dict) -> None:
        """Обработать одну запись: определить серию из папки, filename или metadata."""
        self._apply_folder_series(record, parts_cache)
        # Special case: depth==4 without series subfolder
        # Pass 1 wrongly sets folder_dataset for depth==4, allowing Pass 2 to override it
        file_depth = len(Path(record.file_path).parts)
        # Учитываём если в пути есть extension-папки (они прозрачны, не считаются как уровень)
        raw_parts = Path(record.file_path).parts
        file_depth = len(tuple(
            p for i, p in enumerate(raw_parts)
            if i == len(raw_parts) - 1 or p.lower() not in FILE_EXTENSION_FOLDER_NAMES
        ))
        is_depth4_without_real_series = (
            file_depth == 4 and 
            record.series_source == "folder_dataset"
        )
        
        # Общая проверка: если серия из папки (любого типа) попала в publisher-blacklist →
        # сбросить и дать шанс filename extraction, затем metadata как финальный fallback.
        # Используем word-boundary regex чтобы "СИ" не совпадало с "Русич" и т.п.
        # ИСКЛЮЧЕНИЕ: folder_dataset и folder_hierarchy — это имена реальных папок,
        # созданных пользователем; они авторитетны и blacklist к ним не применяем.
        if record.proposed_series and self.filename_blacklist and \
                record.series_source not in self._FOLDER_SOURCES:
            _fs_lower = record.proposed_series.lower().replace('ё', 'е')
            _folder_series_bl = False
            for _bl in self.filename_blacklist:
                _bl_l = _bl.lower().replace('ё', 'е').strip()
                if not _bl_l:
                    continue
                _pat = r'(?<![а-яёa-z\w])' + re.escape(_bl_l) + r'(?![а-яёa-z\w])'
                if re.search(_pat, _fs_lower):
                    _folder_series_bl = True
                    break
            if _folder_series_bl:
                record.proposed_series = ''
                record.series_source = ''
        if record.series_source == "folder_dataset" and not is_depth4_without_real_series:
            if record.proposed_series:
                return  # Папка дала series (кроме depth==4 ошибки)

        if record.series_source == "folder_hierarchy":
            return  # Иерархия папок определила серию - готово!

        if record.series_source == "no_series_folder":
            return  # Папка «Вне серий» — серии нет, дальше не ищем

        if record.proposed_series and not is_depth4_without_real_series:
            return  # Серия уже установлена (кроме depth==4 ошибки)
        
        # ОБЯЗАТЕЛЬНО пробуемы паттерны (глубина НЕ влияет!)
        # Если series уже установлена из папок → пропускаем extraction
        # Но если folder_dataset дал пустую серию — продолжаем extraction из filename
        if record.series_source == "folder_dataset" and record.proposed_series:
            return  # Folder extraction already set hierarchical series

        # Если папка НЕ дала series → пробуем extraction из filename
        series_candidate = self._extract_series_from_filename(
            record.file_path, validate=False, metadata_series=record.metadata_series,
            known_series=record.proposed_series or '',
            proposed_author=record.proposed_author or ''
        )

        if series_candidate:
            # Базовые фильтры (НЕ валидация) — ДО записи в extracted_series_candidate
            # Запятая-разделитель авторов стоит перед словом с заглавной буквы
            # ("Иванов, Петров"), грамматическая — перед строчной ("Игрок, забравшийся").
            if ',' in series_candidate:
                # ИСКЛЮЧЕНИЕ: если кандидат совпадает с metadata_series →
                # запятая является частью настоящего названия серии ("Мы, Мигель Мартинес")
                _meta_lc = record.metadata_series.strip().lower().replace('ё', 'е') if record.metadata_series else ''
                _cand_lc = series_candidate.lower().replace('ё', 'е')
                # Для иерархической серии «Корень\Подсерия» также проверяем
                # совпадение подсерии с metadata (Остен Ард 1\Память, Скорбь и Шип)
                _sub_lc = _cand_lc.split(chr(92), 1)[1] if chr(92) in _cand_lc else ''
                _meta_confirms_comma = bool(_meta_lc and (
                    _cand_lc == _meta_lc or _sub_lc == _meta_lc
                ))
                if not _meta_confirms_comma:
                    # Считаем это списком авторов только если после каждой запятой
                    # идёт слово с заглавной буквы (или инициал)
                    parts_after_comma = [p.strip() for p in series_candidate.split(',')[1:]]
                    all_capitalized = all(
                        p and (p[0].isupper() or (len(p) >= 2 and p[1] == '.'))
                        for p in parts_after_comma
                    )
                    if all_capitalized:
                        series_candidate = None  # Список авторов
            # ВАЖНО: проверки ниже — независимые (не elif), чтобы срабатывать
            # даже когда кандидат прошёл comma-check (например "о том, как")
            if series_candidate and self._is_author_surname(series_candidate, record.proposed_author):
                            series_candidate = None  # Фамилия или полное имя автора
            if series_candidate and record.file_title:
                # TITLE-AS-SERIES GUARD: если кандидат совпадает с названием книги,
                # это ложный матч (например "Книга" в service_words увела нас не туда).
                # Очищаем file_title от мусора [litres] и сравниваем.
                import re as _re
                _title_clean = _re.sub(r'\s*\[.*?\]\s*$', '', record.file_title.strip())
                # Также убрать (ЛП), (альт. перевод) и т.п. скобочные суффиксы
                _title_no_parens = _re.sub(r'\s*\([^)]*\)\s*$', '', _title_clean).strip()
                # Нормализуем кандидата: убираем ведущий пунктуационный мусор ("- Траун" → "Траун"),
                # чтобы title-collision guard правильно сравнивал с заголовком книги.
                _cand_for_guard = _re.sub(r'^[\-–—\s]+', '', series_candidate).strip()
                _cand_lower = _cand_for_guard.lower()
                _title_lower = _title_clean.lower()
                _title_np_lower = _title_no_parens.lower()
                # Прямое совпадение ИЛИ кандидат является началом названия книги
                # (ловит обрезанные кандидаты типа "Спасение (альт" от "Спасение (альт. перевод)")
                # ИЛИ кандидат начинается с базового названия (без скобок) — "спасение (альт" startswith "спасение"
                # ИСКЛЮЧЕНИЕ 1: если кандидат совпадает с metadata_series → это подтверждённая серия,
                # название книги просто совпадает (1-я книга серии называется так же, как серия)
                # ИСКЛЮЧЕНИЕ 2: если кандидат явно присутствует в скобках в имени файла —
                # "(Серый. Трилогия)" → серия "Серый" надёжна даже если title="Серый"
                # ИСКЛЮЧЕНИЕ 3: если в имени файла кандидат стоит перед номером тома
                # "Чисто шведские убийства 1. Отпуск в раю" → кандидат явно является серией,
                # даже если file_title тоже начинается с него (1-я книга = имя серии + подзаголовок)
                _meta_raw = (record.metadata_series or '').replace('\u2026', '...')
                _meta_lower = _meta_raw.lower().replace('ё', 'е') if _meta_raw else ''
                _cand_lower_norm = _cand_lower.replace('ё', 'е').replace('\u2026', '...')
                _is_confirmed_by_meta = bool(_meta_lower and _cand_lower_norm == _meta_lower)
                # ИСКЛЮЧЕНИЕ: кандидат является ПРЕФИКСОМ metadata_series
                # "Воронцов" → metadata "Воронцов. Перезагрузка" → кандидат реальная серия,
                # title просто начинается с первого слова серии.
                _is_meta_prefix = bool(
                    _meta_lower and not _is_confirmed_by_meta and
                    (_meta_lower.startswith(_cand_lower_norm + '.') or
                     _meta_lower.startswith(_cand_lower_norm + ' '))
                )
                # ИСКЛЮЧЕНИЕ: кандидат = metadata_series + суффикс из служебных слов
                # "Честное пионерское! Часть" → meta "Честное пионерское!" → кандидат начинается
                # с подтверждённой серии, хвост — только мусор/служебные слова.
                # Проверяем: candidates начинается с meta И хвост = только \W + цифры/SW-слова.
                _is_meta_with_service_suffix = bool(
                    _meta_lower and not _is_confirmed_by_meta and not _is_meta_prefix and
                    _cand_lower_norm.startswith(_meta_lower) and
                    _re.match(r'^[\W\s]*(|(\w+\s*)+)$',
                              _cand_lower_norm[len(_meta_lower):].strip())
                    and all(
                        w in self.service_words or w.isdigit()
                        for w in _cand_lower_norm[len(_meta_lower):].split()
                        if w.isalpha()
                    )
                )
                _fn_stem_lower = Path(record.file_path).stem.lower()
                _is_in_parens = bool(_re.search(r'\(\s*' + _re.escape(_cand_lower), _fn_stem_lower))
                # ИСКЛЮЧЕНИЕ 4: серия получена блок-матчером с score=1.0 И подтверждена metadata_series.
                # Только с metadata-подтверждением: title совпадает с серией у omnibus или 1-й книги.
                # Без metadata — блок-матчер мог дать score=1.0 из-за Author→Series coercion,
                # а настоящий title книги случайно совпадает с кандидатом — гарду надо сработать.
                # metadata_series считается подтверждением только если она НЕ в blacklist.
                # Если metadata — издательский ярлык (напр. «МИФ Проза»), он мог быть
                # очищен внутри block-matcher, но record.metadata_series всё ещё не пустая.
                # В таком случае confidence не оправдана — guard должен сработать.
                _meta_is_bl = False
                if record.metadata_series and self.filename_blacklist:
                    _ml = record.metadata_series.lower().replace('ё', 'е')
                    _meta_is_bl = any(
                        bl.lower().replace('ё', 'е') in _ml
                        for bl in self.filename_blacklist if bl
                    )
                _is_block_matcher_confident = (getattr(self, '_last_from_block_matcher', False)
                                               and bool(record.metadata_series)
                                               and not _meta_is_bl)
                # Кандидат + номер в имени файла: "... - Серия N." или "... - Серия N "
                # Также: "Серия. Том N" / "Серия. Книга N" / "Серия. Часть N"
                _cand_esc = _re.escape(_cand_lower.replace('ё', 'е'))
                _is_numbered_series = bool(_re.search(
                    _cand_esc + r'[\s.\-–—]+\d+(?:[\s.]|$)',
                    _fn_stem_lower.replace('ё', 'е')
                )) or bool(_re.search(
                    _cand_esc + r'[\s.\-–—]+(?:том|часть|книга|кн\.|vol\.?|book)\s+\d+',
                    _fn_stem_lower.replace('ё', 'е'),
                    _re.IGNORECASE,
                ))
                if not _is_confirmed_by_meta and not _is_meta_prefix and not _is_meta_with_service_suffix and not _is_in_parens and not _is_numbered_series and not _is_block_matcher_confident and (
                   (_title_lower and _cand_lower == _title_lower) or \
                   (_title_np_lower and _cand_lower == _title_np_lower) or \
                   (_title_lower and _title_lower.startswith(_cand_lower) and len(_cand_lower) >= 4) or \
                   # ИСКЛЮЧЕНИЕ: однословный кандидат без подтверждённой metadata_series,
                   # а заголовок начинается с этого слова → это первое слово заголовка, не серия.
                   # Пример: "Куонг Валери Тонг - Бей. Беги. Замри" → candidate="Бей", title="Бей. Беги. Замри"
                   (not record.metadata_series and
                    ' ' not in _cand_lower and
                    _title_lower and _title_lower.startswith(_cand_lower)) or \
                   (_title_np_lower and len(_title_np_lower) >= 4 and _cand_lower.startswith(_title_np_lower)) or \
                   # ИСКЛЮЧЕНИЕ guard: кандидат является хвостом заголовка (subtitle-суффикс).
                   # Пример: candidate="Правдивая история о том, как студентка исчезла у всех на виду"
                   # title="Пропавшая: Исчезновение Лорен Спирер. Правдивая история..."
                   # → title.endswith(candidate) → это подзаголовок, не серия.
                   (_title_lower and _title_lower.endswith(_cand_lower) and len(_cand_lower) >= 10) or \
                   # ИСКЛЮЧЕНИЕ guard: кандидат является подстрокой заголовка (фрагмент в середине).
                   # Пример: candidate="Рязань, год" (блок из "Время умирать. Рязань, год 1237")
                   # title="Время умирать. Рязань, год 1237" → candidate in title → не серия.
                   (_title_lower and _cand_lower in _title_lower and len(_cand_lower) >= 8)):
                                    series_candidate = None  # Название книги ≠ серия

            # Сохраняем только если прошёл фильтры (иначе Pass4 может распространить имя автора)
            if series_candidate:
                record.extracted_series_candidate = series_candidate

        if not self._apply_filename_candidate(record, series_candidate):
            self._apply_metadata_fallback_single(record, series_candidate)
        

    def _apply_filename_candidate(self, record, series_candidate) -> bool:
        """Валидировать и применить filename series_candidate к record.

        Возвращает True если серия успешно применена, False — нужен metadata fallback.
        """
        if not series_candidate:
            return False
        clean = self._clean_series_name(
            series_candidate,
            keep_trailing_number=self._last_was_hierarchical
        )
        clean = self._remove_blacklist_words(clean)

        # Guard: если _clean_series_name убрала служебное слово, но metadata подтверждает
        # полное название — восстанавливаем из metadata.
        if clean and record.metadata_series:
            _nyo = _nfc_lower_yo
            _meta = record.metadata_series.strip()
            if (_nyo(_meta).startswith(_nyo(clean) + ' ')
                    and _nyo(series_candidate).startswith(_nyo(_meta))):
                clean = _meta

        if not clean:
            return False

        if not self._is_valid_series(clean, extracted_author=record.proposed_author or None):
            return False

        clean = self._fix_russian_grammar(clean)
        record.proposed_series = clean
        record.series_source = "filename"
        if (record.metadata_series and
                record.metadata_series.strip().lower() == clean.lower()):
            record.series_source = "filename+meta_confirmed"

        # Если иерархический root содержит trailing number, а metadata совпадает с root
        # БЕЗ числа — число является позицией книги, не частью названия серии.
        if record.metadata_series and '\\' in (record.proposed_series or ''):
            _root_h, _sub_h = record.proposed_series.split('\\', 1)
            _root_h = _root_h.strip()
            _root_no_num = re.sub(r'\s+\d+\s*$', '', _root_h).strip()
            _meta_s = record.metadata_series.strip()
            if (_root_no_num and _root_no_num != _root_h and
                    _root_no_num.lower().replace('ё', 'е') ==
                    _meta_s.lower().replace('ё', 'е')):
                _sub_stripped = _sub_h.strip()
                if (bool(re.match(r'^\d+$', _sub_stripped)) or
                        _sub_stripped.lower().replace('ё', 'е') ==
                        _meta_s.lower().replace('ё', 'е')):
                    record.proposed_series = _meta_s
                    record.series_source = "filename+meta_confirmed"
        return True

    def _apply_metadata_fallback_single(self, record, series_candidate) -> None:
        """Metadata fallback: если filename не дал серию, ищем из metadata.
        Также применяет найденный series_candidate если он найден в этом методе.
        """
        # Fallback: metadata ТОЛЬКО если паттерны не дали
        if not series_candidate:
            file_name = Path(record.file_path).stem  # Имя без расширения
            
            # ✅ ВАЖНО: Удалить метатеги из конца чтобы fallback правила работали!
            # "(СИ)" - Самиздат/Интернет
            # "(ЛП)" - Лицензионное произведение
            file_name_for_fallback = re.sub(r'\s*\([СЛ]И\)\s*$', '', file_name).strip()
            
            # Перед fallback к metadata попробуем простое правило: Author. Series RomanNumeral
            # "Яманов Александр. Бесноватый Цесаревич I.fb2" → "Бесноватый Цесаревич"
            if '. ' in file_name_for_fallback:
                parts = file_name_for_fallback.split('. ', 1)
                if len(parts) == 2:
                    first_part = parts[0].strip()
                    second_part = parts[1].strip()
                    
                    # Проверяем что первая часть это автор (< 50 символов, без цифр)
                    looks_like_author = (
                        len(first_part) < 50 and
                        not any(digit in first_part for digit in '0123456789')
                    )
                    
                    if looks_like_author:
                        # Убрать аннотацию в скобках с конца перед матчингом диапазона:
                        # "Маршал 1-9 (без иллюстраций)" → "Маршал 1-9"
                        second_part_bare = re.sub(r'\s*\([^)]*\)\s*$', '', second_part).strip()
                        # Убрать год-суффикс (1900–2099) — не должен трактоваться как номер тома:
                        # "Том Ⅰ - 2022" → "Том Ⅰ"  /  "Серия 1 2023" → "Серия 1"
                        second_part_bare = re.sub(r'(?:\s*[-–—])?\s*(?:19|20)\d{2}\s*$', '', second_part_bare).strip()
                        # Диапазон N-M: "Совок 1-5", "Попаданец в Дракона 1-8"
                        match = re.search(r'^(.+?)\s+\d+[-\u2013\u2014]\d+\s*$', second_part_bare)
                        is_range_match = bool(match)
                        if not match:
                            # Одиночное арабское число 1–2 цифры: "Охотник 1", "Серия 12"
                            # 3+ цифры (888, 1234) — номер дела/произведения, не том серии.
                            match = re.search(r'^(.+?)\s+\d{1,2}\s*$', second_part_bare)
                        if not match:
                            # Римские цифры: "Бесноватый Цесаревич I"
                            match = re.search(r'^(.+?)\s+[IVX]+\s*$', second_part_bare)
                        if match:
                            simple_series = match.group(1).strip()
                            _ftitle = (record.file_title or '').lower()
                            # Диапазон N-M в имени файла — однозначный признак серии,
                            # даже если название совпадает с заголовком книги.
                            # Пример: "Хакер 1-2.fb2", file_title="Хакер" → серия "Хакер" корректна.
                            _in_title = (not is_range_match and
                                         bool(_ftitle and simple_series.lower() in _ftitle))
                            if not _in_title and self._is_valid_series(simple_series, extracted_author=record.proposed_author):
                                series_candidate = simple_series
            
            # ✅ НОВОЕ: Попробуем "Author - Series NUM или N-M" паттерн
            # "Шалашов Евгений - Господин следователь 2" → "Господин следователь"
            # Также: "Author - Series N. Title" (число не в конце, за ним ". Title")
            if not series_candidate and ' - ' in file_name_for_fallback:
                match = re.match(r'^(.+?)\s*-\s*(.+?)\s+(?:\d{1,2}[-\u2013\u2014]\d{1,2}|\d{1,2}|[IVX]+)\s*$', file_name_for_fallback)
                if not match:
                    # Попытка: "Author - Series N. Title"
                    match = re.match(r'^(.+?)\s*-\s*(.+?)\s+\d{1,2}\.\s+.+$', file_name_for_fallback)
                if match:
                    first_part = match.group(1).strip()
                    series_part = match.group(2).strip()
                    
                    # Проверяем что первая часть это автор/авторы
                    looks_like_author = (
                        len(first_part) < 50 and
                        not any(digit in first_part for digit in '0123456789')
                    )
                    
                    if looks_like_author:
                        _ftitle = (record.file_title or '').lower()
                        _in_title = bool(_ftitle and series_part.lower() in _ftitle)
                        # ИСКЛЮЧЕНИЕ: если кандидат стоит перед номером тома в имени файла
                        # ("Королевство Костей и Терний 1. Терновый Король") →
                        # это явная серия, даже если _in_title=False по другим причинам.
                        _fn_stem_fb = Path(record.file_path).stem.lower().replace('ё', 'е')
                        _sp_norm = series_part.lower().replace('ё', 'е')
                        _is_numbered_in_fn = bool(re.search(
                            re.escape(_sp_norm) + r'[\s.\-–—]+\d+(?:[\s.]|$)',
                            _fn_stem_fb
                        ))
                        if (_is_numbered_in_fn or not _in_title) and self._is_valid_series(series_part, extracted_author=record.proposed_author):
                            series_candidate = series_part
        
        if series_candidate:
            # Из filename extraction найдена серия
            record.extracted_series_candidate = series_candidate
            clean = self._clean_series_name(
                series_candidate, 
                keep_trailing_number=self._last_was_hierarchical
            )
            # ✅ НОВОЕ: Удалить слова из blacklist вместо полного отвергания
            clean = self._remove_blacklist_words(clean)
            
            if clean:  # Проверяем что что-то осталось после очистки
                author_for_validation = record.proposed_author or None
                
                if self._is_valid_series(clean, extracted_author=author_for_validation):
                    # Исправляем грамматику русского языка (добавляем запятую перед "что")
                    clean = self._fix_russian_grammar(clean)
                    record.proposed_series = clean
                    record.series_source = "filename"
                    if (record.metadata_series and
                            record.metadata_series.strip().lower() == clean.lower()):
                        record.series_source = "filename+meta_confirmed"
                    # Если иерархический root содержит trailing number, а metadata_series
                    # совпадает с root БЕЗ числа — число является позицией книги, не частью
                    # названия серии. Пример: «Север и Юг 01\Великая сага» + meta «Север и Юг»
                    # → proposed_series = «Север и Юг» (иначе каждая книга в отдельной группе).
                    if record.metadata_series and '\\' in (record.proposed_series or ''):
                        _root_h, _sub_h = record.proposed_series.split('\\', 1)
                        _root_h = _root_h.strip()
                        _root_no_num = re.sub(r'\s+\d+\s*$', '', _root_h).strip()
                        _meta_s = record.metadata_series.strip()
                        if (_root_no_num and _root_no_num != _root_h and
                                _root_no_num.lower().replace('ё', 'е') ==
                                _meta_s.lower().replace('ё', 'е')):
                            _sub_stripped = _sub_h.strip()
                            _sub_is_num_only = bool(re.match(r'^\d+$', _sub_stripped))
                            _sub_is_meta_dup = (_sub_stripped.lower().replace('ё', 'е') ==
                                                _meta_s.lower().replace('ё', 'е'))
                            if _sub_is_num_only or _sub_is_meta_dup:
                                # Подсерия — чисто цифровая или дублирует metadata:
                                # «Север и Юг 01\12» или «Серия 1\Серия» → стираем до metadata
                                record.proposed_series = _meta_s
                                record.series_source = "filename+meta_confirmed"
                            # else: подсерия — реальное название («Аспект-Император»);
                            # оставляем proposed_series без изменений (с числом в root)
        elif record.metadata_series:
            # ✅ ЗАЩИТА: Перед использованием metadata - проверяем наличие слов из blacklist
            # ТРЕБОВАНИЕ: "если мета содержит слово или слова из BL, полностью ее игнорируем в качестве значения"
            # Пример: "Шедевры фантастики (продолжатели)" содержит "фантастики" → отклоняем целиком
            # ВАЖНО: word-boundary matching, не substring — "попаданец" не должен блокировать
            # легитимное "Попаданец в Дракона" является реальной серией
            meta_lower = record.metadata_series.lower()
            has_blacklist_word = False
            for bl in self.filename_blacklist:
                bl_lower = bl.lower().strip()
                if not bl_lower:
                    continue
                # Для коротких слов (≤3 символа) — word-boundary; для длинных — word-boundary тоже
                pat = r'(?<![а-яёa-z])' + re.escape(bl_lower) + r'(?![а-яёa-z])'
                if re.search(pat, meta_lower):
                    has_blacklist_word = True
                    break
            
            if has_blacklist_word:
                # metadata содержит слова из blacklist → игнорируем целиком, не используем как series
                pass
            else:
                # ✅ ДОПОЛНИТЕЛЬНО: Проверяем целиком ли она в blacklist
                # Пример: "Современный фантастический боевик (АСТ)" → без "(АСТ)" = "Современный фантастический боевик"
                metadata_base = record.metadata_series.replace(' (АСТ)', '').replace('(АСТ)', '').strip()
                is_pure_blacklist = any(
                    metadata_base.lower() == bl.lower() 
                    for bl in self.filename_blacklist
                )
                
                if is_pure_blacklist:
                    # Весь metadata это blacklist → пропускаем (series остаётся пустой)
                    pass
                else:
                    # Fallback к metadata - только если из filename ничего не нашли
                    series = self._extract_series_from_metadata(record.metadata_series.strip())
                    # Очищаем от скобочных суффиксов (автор в скобках, номера томов и т.п.)
                    # Пример: "Путь (Михаил Игнатов)" → "Путь"
                    series = self._clean_series_name(series)

                    # ✅ Удалить слова из blacklist также из metadata серии
                    series = self._remove_blacklist_words(series)
                    
                    author_for_validation = record.proposed_author or None
                    if series and self._is_valid_series(series, extracted_author=author_for_validation):
                        # Исправляем грамматику русского языка (добавляем запятую перед "что")
                        series = self._fix_russian_grammar(series)
                        record.proposed_series = series
                        
                        # 🔑 Папка уже была проверена выше. Если мы здесь → это просто metadata series (не совпадает с папкой)
                        record.series_source = "metadata"

    def _detect_named_arcs(self, records: List[BookRecord]) -> None:
        """Обнаружить именованные дуги в серии и создать подсерии через '\\'.

        Паттерн: «Author - SeriesRoot 0N. ArcTitle [ArcOrdinal]»
        Если ArcTitle (без хвостового порядкового числа) встречается у 2+ томов
        одной серии — это именованная дуга, которая становится подсерией.

        Пример:
          «Флибер 04. Джони, о-е! Или назад в СССР»   → Флибер\\Джони, о-е! Или назад в СССР  sn=4
          «Флибер 05. Джони, о-е! Или назад в СССР 2» → Флибер\\Джони, о-е! Или назад в СССР  sn=5
          «Флибер 01. Изменить будущее»                → Флибер  sn=1  (title уникален — не дуга)
        """
        from collections import defaultdict

        # Zero-padded паттерн: «SeriesRoot 0N. ArcTitle»
        # Захватываем серию, номер тома (zero-padded) и arc candidate.
        # Допускаем многосоставный arc title с точками внутри: «Другая жизнь. Назад в СССР»
        _ARC_RE = re.compile(
            r'^(?:.+?\s*-\s*)?(.+?)\s+(0\d+)\.\s+(.+)$',
            re.UNICODE,
        )
        # Нуль-непаддированный паттерн для любых источников: «SeriesRoot N. ArcTitle[. Subtitle]»
        # Используется как запасной когда _ARC_RE не совпал, или как основной для non-filename.
        _ARC_RE_ANY = re.compile(
            r'^(?:.+?\s*-\s*)?(.+?)\s+(\d{1,2})\.\s+(.+)$',
            re.UNICODE,
        )
        # Хвостовой порядковый номер/год дуги:
        # «Джони, о-е! 2» → «Джони, о-е!»
        # «Назад в СССР. 1985» → «Назад в СССР»  (год 19xx/20xx после точки)
        _TRAIL_NUM = re.compile(
            r'(?:\s*[.\-–—]\s*(?:19|20)\d{2}|\s+(?:\d{1,2}|(?:19|20)\d{2}))\s*$'
        )

        # 1. Для каждой записи с плоской серией пробуем извлечь arc.
        #    Ключ: (author_norm, series_norm), значение: список (record, vol_num, arc_norm, arc_display)
        #    Обрабатываем и filename-источники (через _ARC_RE / _ARC_RE_ANY), и другие источники
        #    (metadata и т.п.) через _ARC_RE_ANY — берём только первую секцию до '. '.
        groups: dict = defaultdict(list)
        for rec in records:
            if not rec.proposed_series or '\\' in rec.proposed_series:
                continue
            if (rec.series_source or '') == 'filename_named_arc':
                continue
            stem = Path(rec.file_path).stem
            _is_fn_src = 'filename' in (rec.series_source or '')
            m = _ARC_RE.match(stem) if _is_fn_src else None
            if not m:
                m = _ARC_RE_ANY.match(stem)
            if not m:
                continue
            series_in_stem = _norm_s(m.group(1))
            series_rec = _norm_s(rec.proposed_series)
            # Проверяем что корень в стеме совпадает с proposed_series.
            # Если нет — возможно в стеме есть авторский префикс «Автор. Серия N. Арк».
            # Ищем series_rec в нормализованном стеме; принимаем только если он
            # стоит сразу после '. ' или '- ' (авторский разделитель, не внутри слова).
            if series_in_stem != series_rec and not series_rec.startswith(series_in_stem):
                _stem_n = _norm_s(stem)
                _idx = _stem_n.find(series_rec)
                if _idx <= 0:
                    continue
                _prefix = _stem_n[:_idx]
                if not (_prefix.endswith('. ') or _prefix.endswith('- ')):
                    continue  # series_rec внутри другого слова, не авторский префикс
                m2 = _ARC_RE_ANY.match(stem[_idx:])
                if not m2:
                    continue
                _sin2 = _norm_s(m2.group(1))
                if _sin2 != series_rec and not series_rec.startswith(_sin2):
                    continue
                m = m2
            vol_num = int(m.group(2))
            arc_raw = m.group(3).strip()
            # Убираем хвостовой порядковый номер дуги
            arc_full = _TRAIL_NUM.sub('', arc_raw).strip()
            # Берём первую секцию до '. ' — общий arc-prefix без подзаголовка.
            # «Пилот ракетоносца. Выбор курса» → «Пилот ракетоносца»
            # Сохраняем оба варианта: полное имя и префикс — в шаге 2 предпочтём полное
            # если все вхождения дуги имеют одинаковое полное имя.
            arc_prefix = re.split(r'\.\s+', arc_full)[0].strip()
            # Ключ группировки — по полному имени; если оно уникально, попробуем префикс
            arc_norm_full = _norm_s(arc_full)
            arc_norm_prefix = _norm_s(arc_prefix)
            arc_norm = arc_norm_full  # используем полное имя для группировки
            arc_display = arc_full
            if not arc_norm or len(arc_norm) < 4:
                continue
            key = (_norm_s(rec.proposed_author or ''), series_rec)
            groups[key].append((rec, vol_num, arc_norm, arc_display, arc_norm_prefix, arc_prefix))

        # 2. По каждой группе: arc titles с 2+ вхождениями → подсерия
        for (_author_k, _series_k), entries in groups.items():
            arc_counts: dict = defaultdict(list)
            for rec, vol_num, arc_norm, arc_display, arc_norm_prefix, arc_prefix in entries:
                arc_counts[arc_norm].append((rec, vol_num, arc_display))
            # Также группируем по префиксу (для случаев с разными подзаголовками)
            arc_counts_prefix: dict = defaultdict(list)
            for rec, vol_num, arc_norm, arc_display, arc_norm_prefix, arc_prefix in entries:
                if arc_norm_prefix != arc_norm:  # только если отличается от полного
                    arc_counts_prefix[arc_norm_prefix].append((rec, vol_num, arc_prefix))

            # Объединяем: полное имя приоритетнее префикса
            all_arc_counts = {**arc_counts_prefix}
            for k, v in arc_counts.items():
                all_arc_counts[k] = v  # полное имя перезаписывает префикс

            for arc_norm, arc_entries in all_arc_counts.items():
                if len(arc_entries) < 2:
                    continue  # уникальный title — не дуга
                # Если arc совпадает с названием самой серии — это не подсерия
                # (пример: «ПТУшник N. ПТУшник» → arc="ПТУшник" = series "ПТУшник")
                if arc_norm == _series_k:
                    continue
                # Если arc = «серия + порядковый номер» — это том, не дуга.
                # «Ученик теней 3» при серии «Ученик теней» — book 3, не arc.
                _arc_no_trail = re.sub(r'\s+\d+\s*$', '', arc_norm).strip()
                if _arc_no_trail == _series_k:
                    continue
                # Берём наиболее длинный arc_display как каноническое название дуги
                arc_canonical = max((arc_display for _, _, arc_display in arc_entries), key=len)
                vols = sorted(vol_num for _, vol_num, _ in arc_entries)
                lo, hi = vols[0], vols[-1]
                # Если все вхождения арка имеют ОДИНАКОВЫЙ vol_num — это дубли одного
                # файла в разных папках, а не реальная подсерия. Пропускаем.
                if lo == hi:
                    continue
                # Диапазон в корне серии нужен только когда дуга — подмножество:
                # «Флибер 4-7\Джони» — часть серии, есть другие тома вне дуги.
                # Если же ВСЕ тома серии принадлежат этой дуге — диапазон лишний:
                # «Вторая жизнь\Нагнуть Европу», а не «Вторая жизнь 1-2\Нагнуть Европу».
                # Диапазон арка: если есть пробел — пропускаем (разрывный арк не создаём).
                _arc_is_dense = (hi - lo + 1) == len(arc_entries)
                if not _arc_is_dense:
                    continue
                # Диапазон НЕ добавляем в корень серии — позиция уже в sn.
                # «Мент\Одесса-мама» вместо «Мент 10-11\Одесса-мама»:
                # range в корне ломает bucket-ключи и вызывает неверную группировку.
                _ps0 = entries[0][0].proposed_series
                series_root = re.sub(r'\s*\[[^\]]*\]\s*$', '', _ps0.split('\\')[0]).strip()
                # Убираем авторский префикс из корня серии, если он туда попал при парсинге.
                # «Аберкромби. Земной круг 1» → «Земной круг 1» когда автор = «Аберкромби Джо».
                _auth = (entries[0][0].proposed_author or '').strip()
                if _auth:
                    _auth_words_n = {_norm_s(w) for w in _auth.split() if w}
                    _root_first_word = _norm_s(series_root.split('.')[0].split()[0]) if series_root else ''
                    if _root_first_word and _root_first_word in _auth_words_n:
                        _dot_idx = series_root.find('. ')
                        if _dot_idx > 0:
                            _candidate = series_root[_dot_idx + 2:].strip()
                            if _candidate:
                                series_root = _candidate
                new_series = f'{series_root}\\{arc_canonical}'
                for rec, vol_num, _ in arc_entries:
                    rec.proposed_series = new_series
                    rec.series_number = str(vol_num)
                    rec.series_source = 'filename_named_arc'

        # --- Второй проход: выравниваем существующие Series\Arc из filename-источников ---
        # Тома которые уже имеют '\' (созданы старым двухточечным механизмом) должны
        # получить тот же формат с диапазоном в корне, что и дуги из первого прохода.
        # Используем расширенный паттерн — принимаем и zero-padded (08, 09) и двузначные
        # (10, 11, 12), так как записи с '\' уже прошли фильтрацию по другому критерию.
        _ARC_RE2 = re.compile(
            r'^(?:.+?\s*-\s*)?(.+?)\s+(\d{1,2})\.\s+(.+)$',
            re.UNICODE,
        )
        _FILENAME_SRCS = {'filename', 'filename+meta_confirmed', 'filename+meta_expanded'}
        existing_arcs: dict = defaultdict(list)
        for rec in records:
            s = rec.proposed_series or ''
            if '\\' not in s:
                continue
            if (rec.series_source or '') not in _FILENAME_SRCS:
                continue
            stem = Path(rec.file_path).stem
            m = _ARC_RE2.match(stem)
            if not m:
                continue
            vol_num = int(m.group(2))
            root, arc = s.split('\\', 1)
            root_base = re.sub(r'\s+\d+[-–—]\d+\s*$', '', root).strip()
            root_base = re.sub(r'\s+\d+\s*$', '', root_base).strip()
            # Проверяем что дуга из proposed_series встречается в стеме.
            # Том 13 («аворг. Назад в СССР») не должен попасть в группу «Другая жизнь»
            # потому что «другая жизнь» не встречается в его стеме.
            # Для Чинцова («Нагнуть Европу 1. Сокровища тамплиеров») «нагнуть европу»
            # присутствует в стеме → правильно включается.
            arc_norm_ps = _norm_s(arc.strip())
            if arc_norm_ps and arc_norm_ps not in _norm_s(stem):
                continue
            # Пропускаем если арк = корень серии (нормализованно без точек/многоточий).
            # «Пункт назначения..\Пункт назначения» — это ложный арк, не реальная подсерия.
            # Также пропускаем если корень содержится в имени арка:
            # «Хоттабыч\Позывной Хоттабыч» — «хоттабыч» ⊂ «позывной хоттабыч» → ложный арк.
            _root_base_stripped = _norm_s(re.sub(r'[.…]+$', '', root_base.strip()))
            if arc_norm_ps and (arc_norm_ps == _root_base_stripped
                                or (_root_base_stripped and _root_base_stripped in arc_norm_ps)):
                continue
            key = (_norm_s(rec.proposed_author or ''), _norm_s(root_base), _norm_s(arc))
            existing_arcs[key].append((rec, vol_num, root_base, arc))

        for (_ak, _rk, _srk), arc_entries in existing_arcs.items():
            if len(arc_entries) < 2:
                continue
            vols = sorted(vol_num for _, vol_num, _, _ in arc_entries)
            lo, hi = vols[0], vols[-1]

            # Когда ВСЕ книги арка имеют ОДИНАКОВЫЙ vol_num (lo == hi) — это
            # «временная подсерия» (пример: «Такер Уэйн» входит в том 8 «Отряда Сигма»).
            # Вместо иерархии «Серия 8\Такер Уэйн» присваиваем дробные sn:
            # 1-я книга арка → sn='8.1', 2-я → '8.2', ... Flat серия сохраняется.
            if lo == hi:
                _arc_vol = lo
                _arc_display_local = arc_entries[0][3]
                _arc_norm_local = _norm_s(_arc_display_local)
                _root_base_local = arc_entries[0][2]

                _TOM_SORT_PAT = _TOM_WORD_RE

                def _arc_internal_pos(entry, _anl=_arc_norm_local):
                    _rec, _vn, _rb, _arc = entry
                    _stem_n = _norm_s(Path(_rec.file_path).stem)
                    _m = re.search(re.escape(_anl) + r'[\s.]+(\d{1,3})', _stem_n)
                    _primary = int(_m.group(1)) if _m else 9999
                    # Вторичный ключ — «Том/Книга N» в стеме надёжнее metadata sn
                    _tm = _TOM_SORT_PAT.search(_stem_n)
                    if _tm:
                        _secondary = int(_tm.group(1))
                    else:
                        _sn = (_rec.series_number or '').strip()
                        try:
                            _secondary = int(_sn) if _sn.isdigit() else 9999
                        except Exception:
                            _secondary = 9999
                    return (_primary, _secondary)

                # Сохраняем иерархическую форму «Корень N\Арк» — подсерия видна в CSV.
                # Compilation корректно сгруппирует через arc-root stripping.
                _arc_vol_series = f'{_root_base_local} {_arc_vol}\\{_arc_display_local}'
                _sorted_arc = sorted(arc_entries, key=_arc_internal_pos)
                for _i, (_rec, _vn, _, _) in enumerate(_sorted_arc, 1):
                    _rec.proposed_series = _arc_vol_series
                    _rec.series_number = f'{_arc_vol}.{_i}'
                    _rec.series_source = 'filename_named_arc'
                continue

            # Диапазон НЕ добавляем в корень — позиция уже в sn.
            # «Мент\Одесса-мама» вместо «Мент 10-11\Одесса-мама».
            root_base = arc_entries[0][2]
            arc_display = arc_entries[0][3]
            new_series = f'{root_base}\\{arc_display}'
            for rec, vol_num, _, _ in arc_entries:
                rec.proposed_series = new_series
                rec.series_number = str(vol_num)
                rec.series_source = 'filename_named_arc'

        # --- Третий проход: обратное применение арка к плоским томам ---
        # Если (автор, корень, арк) уже подтверждён (2+ тома с «ArcName. Subtitle»),
        # плоские тома у которых stem = «Серия N. ArcName» (без подзаголовка)
        # тоже включаются в тот же арк.
        # Пример: «Фортуна Эрика Минца 1. Пилот ракетоносца» + известный арк
        # «Пилот ракетоносца» из томов 2-3 → все три тома в арке.
        _arc_registry: dict = defaultdict(list)
        for rec in records:
            _s_r = rec.proposed_series or ''
            if '\\' not in _s_r or (rec.series_source or '') != 'filename_named_arc':
                continue
            _sn_r = (rec.series_number or '').strip()
            if not _sn_r.isdigit():
                continue  # дробный sn (8.1) — временная подсерия, не трогаем
            _root_r, _arc_r = _s_r.split('\\', 1)
            _root_base_r = re.sub(r'\s+\d+[-–—]\d+\s*$', '', _root_r).strip()
            _root_base_r = re.sub(r'\s+\d+\s*$', '', _root_base_r).strip()
            _key_r = (_norm_s(rec.proposed_author or ''), _norm_s(_root_base_r), _norm_s(_arc_r.strip()))
            _arc_registry[_key_r].append((rec, int(_sn_r), _root_base_r, _arc_r.strip()))

        for rec in records:
            if not rec.proposed_series or '\\' in (rec.proposed_series or ''):
                continue
            if (rec.series_source or '') == 'filename_named_arc':
                continue
            if 'filename' not in (rec.series_source or ''):
                continue
            _stem_f = Path(rec.file_path).stem
            _m_f = _ARC_RE2.match(_stem_f)
            if not _m_f:
                continue
            _vol_f = int(_m_f.group(2))
            _arc_raw_f = _m_f.group(3).strip()
            # Первая секция до '. ' — кандидат в арки (без подзаголовка)
            _arc_cand_f = re.split(r'\.\s+', _arc_raw_f)[0].strip()
            _arc_norm_f = _norm_s(_arc_cand_f)
            if not _arc_norm_f or len(_arc_norm_f) < 4:
                continue
            _root_ps_f = rec.proposed_series
            # Пробуем ключ с зачисткой числового суффикса из proposed_series и без
            _root_norm_f = _norm_s(re.sub(r'\s+\d+[-–—]?\d*\s*$', '', _root_ps_f).strip())
            _key_f = (_norm_s(rec.proposed_author or ''), _root_norm_f, _arc_norm_f)
            if _key_f not in _arc_registry:
                _key_f = (_norm_s(rec.proposed_author or ''), _norm_s(_root_ps_f), _arc_norm_f)
            if _key_f not in _arc_registry:
                continue
            _arc_recs_f = _arc_registry[_key_f]
            _all_vols_f = sorted({_vol_f} | {v for _, v, _, _ in _arc_recs_f})
            _new_lo_f, _new_hi_f = _all_vols_f[0], _all_vols_f[-1]
            _root_base_f = _arc_recs_f[0][2]
            _arc_disp_f = _arc_recs_f[0][3]
            _is_partial_f = _new_lo_f > 1
            _rsuf_f = (f' {_new_lo_f}-{_new_hi_f}' if _new_lo_f != _new_hi_f else f' {_new_lo_f}') if _is_partial_f else ''
            _new_series_f = f'{_root_base_f}{_rsuf_f}\\{_arc_disp_f}'
            rec.proposed_series = _new_series_f
            rec.series_number = str(_vol_f)
            rec.series_source = 'filename_named_arc'
            # Обновляем уже назначенные тома арка — диапазон мог измениться
            for _arc_rec_f, _arc_vol_f, _, _ in _arc_recs_f:
                _arc_rec_f.proposed_series = _new_series_f
                _arc_rec_f.series_number = str(_arc_vol_f)

    def _correct_series_number_from_filename(self, records: List[BookRecord]) -> None:
        """Переопределяет series_number числовым префиксом имени файла.

        Правило: если имя файла начинается с «NN_» или «NN-» (1–3 цифры, не год),
        то это число используется как series_number — оно достовернее, чем метаданные
        FB2, которые автор/издатель нередко указывает ошибочно.

        Примеры:
          «01_Якудза из другого мира. Том I.fb2»  → series_number=1
          «04_Якудза из другого мира. Том IV.fb2» → series_number=4 (а не 3 из meta)
          «2024_SomeBook.fb2»                      → НЕ трогаем (год, не порядковый №)
        """
        _PREFIX_RE = re.compile(r'^(\d{1,3})[_\-\.]')
        # Правило 1.5: дробный префикс «N.M.» или «N.M_»
        _FRAC_PREFIX_RE = re.compile(r'^(\d{1,3}\.\d{1,4})[\._\s]', re.UNICODE)
        # Правило 3: диапазон томов в скобках имени файла
        # «Автор - Серия (т. 4-7).fb2» → series_number='4-7'
        # Перекрывает metadata-диапазон «1-4» (относительные номера глав внутри файла)
        _BRACKET_RANGE_RE = re.compile(
            r'\(\s*(?:тт?\.?|tt?\.?|vol\.?s?|книг[аи]?|кн\.?)\s*'
            r'(\d{1,3})\s*[-–—]\s*(\d{1,3})\s*\)',
            re.IGNORECASE | re.UNICODE,
        )
        # Правило 3.5: «(SeriesName N-M)» — название серии в скобках перед диапазоном.
        # Пример: «Красницкий - Отрок. Ближний круг (Отрок 4-6)» → sn='4-6'
        # Prefix в скобках должен совпадать с proposed_series (нормализовано).
        _BRACKET_SERIES_RANGE_RE = re.compile(
            r'\(\s*([^\d()]+?)\s+(\d{1,3})\s*[-–—]\s*(\d{1,3})\s*\)',
            re.UNICODE,
        )

        def _br_series_match(prefix_raw: str, proposed: str) -> bool:
            """Prefix ≈ proposed_series root (нечувствительно к знакам и регистру)."""
            _n = lambda s: re.sub(r'[\W_]+', ' ', _nfc_lower_yo(s)).strip()
            p = _n(prefix_raw)
            root = _n(proposed.split('\\')[0])
            root = re.sub(r'\s*\d+\s*$', '', root).strip()  # убираем хвостовые цифры
            return p == root or root.startswith(p + ' ') or root == p

        for record in records:
            if not record.file_path:
                continue
            stem = Path(record.file_path).stem

            # Правило 3 — проверяем первым: явный диапазон в скобках (с ключевым словом)
            m3 = _BRACKET_RANGE_RE.search(stem)
            if m3:
                lo3, hi3 = int(m3.group(1)), int(m3.group(2))
                if lo3 < hi3 and not (1900 <= lo3 <= 2099):
                    record.series_number = f'{lo3}-{hi3}'
                    continue

            # Правило 3.5 — диапазон с именем серии в скобках
            m35 = _BRACKET_SERIES_RANGE_RE.search(stem)
            if m35 and record.proposed_series:
                lo35, hi35 = int(m35.group(2)), int(m35.group(3))
                if lo35 < hi35 and not (1900 <= lo35 <= 2099):
                    if _br_series_match(m35.group(1), record.proposed_series):
                        record.series_number = f'{lo35}-{hi35}'
                        continue

            # Правило 1.5: дробный префикс «N.M.» или «N.M_»
            # «0.1. Двигатель (рассказ).fb2» → series_number='0.1'
            # Проверяем ДО Правила 1, иначе _PREFIX_RE съест только «0».
            mf = _FRAC_PREFIX_RE.match(stem)
            if mf:
                fn_frac = mf.group(1)
                fn_frac_lo = int(fn_frac.split('.')[0])
                if not (1900 <= fn_frac_lo <= 2099):
                    if not (record.series_number and re.match(r'^\d+\.\d+$', record.series_number)):
                        record.series_number = fn_frac
                    continue

            m = _PREFIX_RE.match(stem)
            if m:
                fn_num = int(m.group(1))
                if not (1900 <= fn_num <= 2099):
                    record.series_number = str(fn_num)
                continue

            # Правило 2: «SeriesRoot N. BookTitle» в середине стема.
            # Расширено: поддерживает диапазон «N-M» («Орёл 1-2. Римский орел»)
            # и нормализацию ё→е при сопоставлении.
            if not record.proposed_series:
                continue
            series_root = record.proposed_series.split('\\')[0].strip()
            if not series_root:
                continue
            _sr_n = series_root.replace('ё', 'е').replace('Ё', 'Е')
            _stem_n2 = stem.replace('ё', 'е').replace('Ё', 'Е')
            m2 = re.search(
                r'(?i)' + re.escape(_sr_n)
                + r'[\s\-]+(\d{1,3}(?:\s*[-–—]\s*\d{1,3})?)\s*(?:[\.\s]|$)',
                _stem_n2
            )
            if not m2:
                continue
            fn_val2 = re.sub(r'\s*[-–—]\s*', '-', m2.group(1).strip())
            fn_lo2 = int(fn_val2.split('-')[0])
            if 1900 <= fn_lo2 <= 2099:
                continue
            if record.series_number and record.series_number == fn_val2:
                continue  # уже верное значение
            # Не перезаписываем дробный sn вида «8.1» (временная подсерия):
            if record.series_number and re.match(r'^\d+\.\d+$', record.series_number):
                continue
            record.series_number = fn_val2
            # Если диапазон N-M и серия была иерархической «Корень\Арк» —
            # выпрямляем: «Арк» это подзаголовок компиляции, а не настоящая подсерия.
            if '-' in fn_val2 and '\\' in (record.proposed_series or ''):
                if (record.series_source or '') != 'filename_named_arc':
                    record.proposed_series = series_root

        # Правило 4: голый диапазон в конце стема без скобок
        # «Варяг 1-3.fb2» → series_number='1-3'
        _BARE_RANGE_RE = re.compile(r'\s+(\d{1,3})\s*[-–—]\s*(\d{1,3})\s*$')
        for record in records:
            if record.series_number:
                continue  # уже есть — не трогаем
            if not record.file_path:
                continue
            stem = Path(record.file_path).stem
            mb = _BARE_RANGE_RE.search(stem)
            if not mb:
                continue
            lo_b, hi_b = int(mb.group(1)), int(mb.group(2))
            if lo_b >= hi_b or 1900 <= lo_b <= 2099:
                continue
            record.series_number = f'{lo_b}-{hi_b}'

        # Правило 5: «Слово N» в имени файла — «Свиток 1», «Том 3», «Книга 4» и т.п.
        # Применяется только когда series_number ещё не задан (нет метаданных и нет префикса).
        _WORD_NUM_RE = _TOM_WORD_RE
        for record in records:
            if record.series_number:
                continue
            if not record.file_path:
                continue
            stem = Path(record.file_path).stem
            mw = _WORD_NUM_RE.search(stem)
            if not mw:
                continue
            fn_numW = int(mw.group(1))
            if 1900 <= fn_numW <= 2099:
                continue
            record.series_number = str(fn_numW)

        # Правило 6: «Пролог» без series_number → sn=0, если в серии нет тома 0.
        _norm6 = lambda s: _nfc_lower_yo(s).strip()
        _has_zero: set = set()
        for rec in records:
            if (_norm6(rec.series_number or '') == '0'
                    and rec.proposed_series and rec.proposed_author):
                _has_zero.add((_norm6(rec.proposed_author), _norm6(rec.proposed_series)))
        for rec in records:
            if rec.series_number or not rec.proposed_series:
                continue
            if _norm6(rec.file_title or '') != 'пролог':
                continue
            key = (_norm6(rec.proposed_author or ''), _norm6(rec.proposed_series))
            if key not in _has_zero:
                rec.series_number = '0'

    def _resolve_hierarchical_flat_mismatch(self, records: List[BookRecord]) -> None:
        """Нормализует рассогласование «A\\B» и «A» у одного автора.

        Когда одна книга извлеклась как «Траун\\Приквелы», а другие — как «Траун»,
        но в именах файлов этих других книг есть «Приквелы N.» — все переименовываются
        в «Траун Приквелы».

        Условие безопасности: плоские записи включаются только если их стем содержит
        имя подсерии непосредственно перед номером («Приквелы 2.»).
        """
        # 1. Собираем иерархические записи: (author_norm, root_base_norm) → [(root_display, sub_display, rec)]
        from collections import defaultdict
        hier_map: dict = defaultdict(list)
        for rec in records:
            if '\\' not in (rec.proposed_series or ''):
                continue
            root, sub = rec.proposed_series.split('\\', 1)
            root = root.strip()
            sub = sub.strip()
            if not sub:
                continue
            root_base = re.sub(r'\s+\d+\s*$', '', root).strip()
            key = (_norm_s(rec.proposed_author or ''), _norm_s(root_base))
            hier_map[key].append((root_base, sub, rec))

        if not hier_map:
            return

        # 2. Для каждого ключа: если ровно одна подсерия — ищем плоские записи того же автора
        for (author_k, root_k), entries in hier_map.items():
            # Проверяем, что все иерархические записи имеют одну и ту же подсерию
            subs = {_norm_s(e[1]) for e in entries}
            if len(subs) != 1:
                continue  # Несколько разных подсерий — не трогаем
            sub_norm = next(iter(subs))
            sub_display = entries[0][1]  # оригинальный регистр
            root_display = entries[0][0]
            flat_series_display = f'{root_display}\\{sub_display}'

            # Ищем плоские записи: тот же автор, proposed_series == root_base
            _sub_re = re.compile(
                re.escape(sub_norm) + r'\s+\d',
                re.UNICODE,
            )
            for rec in records:
                if _norm_s(rec.proposed_author or '') != author_k:
                    continue
                if '\\' in (rec.proposed_series or ''):
                    # Это иерархическая запись — нормализуем ниже
                    continue
                if _norm_s(rec.proposed_series or '') != root_k:
                    continue
                stem_norm = _norm_s(Path(rec.file_path).stem)
                if _sub_re.search(stem_norm):
                    rec.proposed_series = flat_series_display

            # 3. Нормализуем сами иерархические записи
            for root_b, sub_d, rec in entries:
                rec.proposed_series = flat_series_display

    def _split_numbered_subseries(self, records: List[BookRecord]) -> None:
        """Обнаружить и разбить группы «Серия N. Заголовок. Том/Книга M» на подсерии.

        Когда в группе (автор, proposed_series) несколько книг имеют одинаковый
        series_number (коллизия), и у ВСЕХ коллизионных книг в имени файла есть
        явный Том/Книга/Часть M — series_number N является идентификатором подсерии,
        а не номером тома в общей серии.

        Пример:
          «Война великого бога 1. Седьмая казнь»          → sn=1, нет Том-слова
          «Война великого бога 2. Внутренняя война. Том 1» → sn=2, есть «Том 1»
          «Война великого бога 2. Внутренняя война. Том 2» → sn=2, есть «Том 2»

        Коллизия: sn=2 у двух файлов, оба с Том-словом → sub-series pattern.

        Исправление: proposed_series += f' {sn}' для ВСЕХ файлов группы,
        чтобы компилятор создал отдельные серии «Война великого бога 1»
        и «Война великого бога 2».

        Условия безопасности:
        - Применяется только при наличии коллизии (sn встречается ≥2 раз)
        - ВСЕ файлы коллизионного sn должны иметь явный Том-keyword в stem
        - Только плоские серии (без '\\')
        - series_number должен быть целым числом
        """
        from collections import defaultdict

        _TOM_RE = _TOM_WORD_RE
        _norm = lambda s: re.sub(r'\s+', ' ', re.sub(r'[.,:;!?]+', ' ', unicodedata.normalize('NFC', s).lower().replace('ё', 'е'))).strip()

        # Regex для извлечения числа из стема файла когда series_number пуст.
        # Ищем паттерн «СЛОВО N.» или «СЛОВО N » в имени файла.
        _STEM_SN_RE = re.compile(
            r'[А-ЯЁа-яёA-Za-z]+\s+(\d{1,4})(?:\.|(?=\s))',
            re.UNICODE,
        )

        def _sn_from_stem(rec) -> str:
            """Вернуть series_number из метаданных или из стема файла."""
            sn = (rec.series_number or '').strip()
            if sn and re.match(r'^\d+$', sn):
                return sn
            # Пробуем извлечь из имени файла: последнее вхождение «СЛОВО N.»
            stem = Path(rec.file_path).stem
            matches = _STEM_SN_RE.findall(stem)
            return matches[-1] if matches else ''

        _FOLDER_SRC_SNS = {
            'folder_dataset', 'folder_hierarchy', 'folder_meta_consensus',
            'folder_metadata_confirmed',
        }
        # Группируем по (автор, proposed_series) — только плоские серии
        # Папочные источники авторитетны: их серии не дополняем номерами.
        groups: dict = defaultdict(list)
        for rec in records:
            if not rec.proposed_series or '\\' in rec.proposed_series:
                continue
            if rec.series_source in _FOLDER_SRC_SNS:
                continue
            if not _sn_from_stem(rec):
                continue
            key = (_norm_s(rec.proposed_author or ''), _norm_s(rec.proposed_series))
            groups[key].append(rec)

        for (_author_k, _series_k), group in groups.items():
            # Разбиваем по series_number (или числу из стема)
            sn_buckets: dict = defaultdict(list)
            for rec in group:
                sn_buckets[_sn_from_stem(rec)].append(rec)

            # Ищем коллизию: sn встречается ≥2 раз, И как минимум 2 из коллизионных
            # файлов имеют явный Том/Книга-keyword со ВСЕМИ РАЗЛИЧНЫМИ числами.
            #
            # Различность чисел — ключевое условие, отличающее два случая:
            #   «Война великого бога 2. Внутренняя война. Том 1» +
            #   «Война великого бога 2. Внутренняя война. Том 2»
            #   → Том-числа {1,2} — все различны → подсерия ✓
            #
            #   «Афганский рубеж. Книга 1» (sn=1) +
            #   «Сирийский рубеж. Книга 1» (sn=1)
            #   → Том-числа {1,1} — есть дубли → разные подсерии, пропускаем ✗
            #
            # Скомпилированные файлы без Том-слова (e.g. «Война Великого Бога 2
            # (Дилогия)») в коллизионном bucket не блокируют детекцию: их Том-числа
            # просто не собираются (нет match → не попадают в список).
            has_collision = False
            for sn, recs in sn_buckets.items():
                if len(recs) < 2:
                    continue
                tom_values = []
                for r in recs:
                    m = _TOM_RE.search(Path(r.file_path).stem)
                    if m:
                        tom_values.append(int(m.group(1)))
                # ≥2 файлов с Том-keyword И все их числа различны
                if len(tom_values) >= 2 and len(set(tom_values)) == len(tom_values):
                    has_collision = True
                    break

            # Второй путь: квалификатор-коллизия — разные слова перед номером тома.
            # Пример: «Траун. Доминация 2» vs «Траун. Приквелы 2» при sn=2.
            # Том/Книга-keyword в именах отсутствует, но слова-квалификаторы различны.
            # ВАЖНО: folder_hierarchy-записи не участвуют — их серия из папки, не из имени файла.
            _qualifier_collision = False
            if not has_collision:
                for sn_val, recs in sn_buckets.items():
                    # Берём только записи с filename-источником серии
                    fn_recs = [r for r in recs if 'filename' in r.series_source]
                    if len(fn_recs) < 2:
                        continue
                    _qual_re = re.compile(
                        r'([А-ЯЁа-яёA-Za-z]+)\s+' + re.escape(sn_val) + r'(?=\b|\.|\s|$)',
                        re.UNICODE,
                    )
                    _quals: set = set()
                    for r in fn_recs:
                        _qm = _qual_re.search(Path(r.file_path).stem)
                        if _qm:
                            _quals.add(_norm_s(_qm.group(1)))
                    if len(_quals) >= 2:
                        _qualifier_collision = True
                        break

            if not has_collision and not _qualifier_collision:
                continue

            if has_collision:
                # Дописываем series_number к proposed_series для всех книг группы.
                # Для коллизионных файлов (с Том-keyword) дополнительно обновляем
                # series_number ← значение из «Том M» в имени файла, чтобы компилятор
                # видел корректные позиции томов (1, 2, ...) внутри подсерии.
                for sn, recs in sn_buckets.items():
                    for rec in recs:
                        rec.proposed_series = f'{rec.proposed_series.strip()} {sn}'
                        # Для файлов с Том-keyword: series_number ← M из «Том M»
                        tm = _TOM_RE.search(Path(rec.file_path).stem)
                        if tm:
                            rec.series_number = tm.group(1)
            else:
                # Квалификатор-коллизия: извлекаем слово перед номером только у filename-записей.
                # folder_hierarchy-записи пропускаем — их серия уже корректна из папки.
                for sn_val, recs in sn_buckets.items():
                    _qual_re = re.compile(
                        r'([А-ЯЁа-яёA-Za-z]+)\s+' + re.escape(sn_val) + r'(?=\b|\.|\s|$)',
                        re.UNICODE,
                    )
                    for rec in recs:
                        if 'filename' not in rec.series_source:
                            continue
                        # Пропускаем если метаданные уже подтверждают серию без квалификатора
                        _ms_norm = _norm_s(rec.metadata_series or '')
                        _ps_norm = _norm_s(rec.proposed_series or '')
                        if _ms_norm and _ms_norm == _ps_norm:
                            continue
                        _qm = _qual_re.search(Path(rec.file_path).stem)
                        if _qm:
                            rec.proposed_series = f'{rec.proposed_series.strip()} {_qm.group(1)}'

    def _mark_duplicate_variants(self, records: List[BookRecord]) -> None:
        """Помечает устаревшие дубликаты: два файла с одинаковым автором+серия+номер.

        Когда одна и та же книга существует в старом и новом вариантах (например,
        «Возвращение в Тооредаан.fb2» и «Возвращение в Тооредаан (новый вариант) (СИ).fb2»
        оба имеют series_number=1), нужно оставить более новый и пометить старый.

        Маркеры «нового варианта» в имени файла: «новый вариант», «новая редакция»,
        «новая версия», «new version», «revised», «updated».
        Файл С таким маркером — новый, БЕЗ маркера — старый → delete_flag=True.
        """
        _NEW_MARKERS = re.compile(
            r'нов(?:ый|ая|ое)\s+(?:вариант|редакци|версия|издани)|'
            r'new\s+(?:version|edition|variant)|revised|updated',
            re.IGNORECASE | re.UNICODE,
        )



        from collections import defaultdict
        # Группируем по (автор, серия, номер_в_серии)
        groups: dict = defaultdict(list)
        for rec in records:
            if not rec.proposed_series or not rec.series_number:
                continue
            key = (_norm_s(rec.proposed_author or ''), _norm_s(rec.proposed_series), rec.series_number)
            groups[key].append(rec)

        marked = 0
        for key, grp in groups.items():
            if len(grp) < 2:
                continue
            # Разбиваем на «новые» и «старые» по маркеру в имени файла
            new_variants = [r for r in grp if _NEW_MARKERS.search(Path(r.file_path).stem)]
            old_variants = [r for r in grp if not _NEW_MARKERS.search(Path(r.file_path).stem)]
            if new_variants and old_variants:
                for r in old_variants:
                    r.delete_flag = True
                    marked += 1
        if marked:
            print(f"[PASS 2] Marked {marked} records as duplicate (superseded by newer variant)")

    def _fix_multiauthor_folders(self, records: List[BookRecord]) -> None:
        """Финальный костыль: папки "Серия (Фамилия и др)" → автор всех файлов = "Фамилия Имя и другие".

        После полной обработки Pass 2 ищем в путях все папки вида "X (Surname и др)".
        Для каждой такой папки:
        1. Определяем hint_surname из скобок.
        2. Среди ВСЕХ записей в этой папке ищем хотя бы одну с proposed_author,
           содержащим hint_surname → это canonical_name ("Красницкий Евгений").
        3. Устанавливаем proposed_author = "canonical_name и другие" для ВСЕХ записей
           в этой папке (включая Соавторство-записи).
        """
        _ET_AL_RE = re.compile(
            r'\s*(?:и\s+др\.?|и\s+другие|et\s+al\.?|and\s+others)\s*$',
            re.IGNORECASE
        )
        _PARENS_ET_AL_RE = re.compile(
            r'\(([^)]+?)\s*(?:и\s+др\.?|и\s+другие|et\s+al\.?|and\s+others)\s*\)\s*$',
            re.IGNORECASE
        )

        # 1. Найти все папки-хинты в путях записей
        # folder_prefix → hint_surname (lower)
        hint_map: Dict[str, str] = {}  # folder_prefix_str → hint_surname_lower
        for record in records:
            parts = Path(record.file_path).parts[:-1]
            for i, part in enumerate(parts):
                m = _PARENS_ET_AL_RE.search(part)
                if m:
                    surname_hint = m.group(1).strip().rstrip(',').strip()
                    # prefix = все папки вплоть до этой (включительно)
                    prefix = str(Path(*parts[:i + 1])) if i > 0 else parts[0]
                    hint_lower = surname_hint.lower().replace('ё', 'е')
                    if prefix not in hint_map:
                        hint_map[prefix] = hint_lower

        if not hint_map:
            return

        # 2. Для каждой папки-хинта найти canonical_name среди records
        canonical_map: Dict[str, str] = {}  # prefix → "Фамилия Имя"
        for prefix, hint_lower in hint_map.items():
            canonical = None
            # Ищем среди всех records что лежат под этим prefix
            for record in records:
                record_prefix = str(Path(*Path(record.file_path).parts[:-1]))
                if not (record_prefix == prefix or record_prefix.startswith(prefix + '\\')):
                    continue
                # Ищем hint в proposed_author (приоритет — уже нормализовано)
                for pa_part in re.split(r'\s*,\s*', record.proposed_author or ''):
                    pa_part = pa_part.strip()
                    pa_clean = _ET_AL_RE.sub('', pa_part).strip()
                    pa_words = pa_clean.split()
                    if any(hint_lower == w.lower().replace('ё', 'е') for w in pa_words):
                        if len(pa_words) == 2:
                            canonical = pa_clean  # Идеальный вариант: "Фамилия Имя"
                            break
                        elif len(pa_words) >= 2 and canonical is None:
                            canonical = pa_clean  # Принимаем пока нет лучшего
                if canonical and len(canonical.split()) == 2:
                    break
                # Fallback: ищем в metadata_authors (могут содержать полное имя)
                for meta_part in re.split(r'[;,]', record.metadata_authors or ''):
                    meta_part = meta_part.strip()
                    meta_clean = _ET_AL_RE.sub('', meta_part).strip()
                    meta_words = meta_clean.split()
                    if any(hint_lower == w.lower().replace('ё', 'е') for w in meta_words):
                        if len(meta_words) == 2:
                            canonical = meta_clean
                            break
                        elif len(meta_words) >= 2 and canonical is None:
                            canonical = meta_clean
                if canonical and len(canonical.split()) == 2:
                    break
            if canonical:
                canonical_map[prefix] = canonical

        if not canonical_map:
            self.logger.log(f"[PASS 2 Multiauthor] hint_map found {len(hint_map)} folders but no canonical names resolved")
            return

        self.logger.log(f"[PASS 2 Multiauthor] canonical_map: {canonical_map}")

        # Вычислить series_name для каждого prefix ("Отрок_Сотник (Красницкий и др)" → "Отрок_Сотник")
        prefix_series: Dict[str, str] = {}
        for prefix in canonical_map:
            folder_name = Path(prefix).name if Path(prefix).name else prefix
            series_name = self._extract_series_from_folder_name(folder_name)
            prefix_series[prefix] = series_name or folder_name

        # 3. Применить canonical_name и серию ко ВСЕМ records под этим prefix
        fixed_author = 0
        fixed_series = 0
        for record in records:
            record_parts = Path(record.file_path).parts
            record_prefix = str(Path(*record_parts[:-1]))
            for prefix, canonical in canonical_map.items():
                if not (record_prefix == prefix or record_prefix.startswith(prefix + '\\')):
                    continue

                # Автор — без суффикса "и другие", чтобы все файлы получили идентичную строку
                # и компилятор мог сгруппировать их в одну группу
                new_author = canonical
                if record.proposed_author != new_author:
                    record.proposed_author = new_author
                    fixed_author += 1
                record.author_source = 'folder_multiauthor'

                # Серия: определяем подпапку сразу под prefix
                root_series = prefix_series[prefix]
                prefix_parts = Path(prefix).parts
                # Индекс папки-хинта в record_parts
                hint_depth = len(prefix_parts)  # сколько частей составляет prefix
                # Следующий элемент после prefix — подпапка серии (если есть)
                if hint_depth < len(record_parts) - 1:
                    subfolder_raw = record_parts[hint_depth]
                    if not subfolder_raw.endswith('.fb2'):
                        # Сохраняем числовой префикс "1. " "2) " — он становится
                        # порядковым номером подсерии. Убираем только лишние пробелы.
                        subfolder_display = subfolder_raw.strip()
                        new_series = f"{root_series}\\{subfolder_display}"
                        # Устанавливаем серию для всех файлов в мультиавторной папке:
                        # физическое расположение в подпапке авторитетнее имени файла.
                        # Только folder_dataset (явный датасет) не трогаем.
                        if record.series_source not in ("folder_dataset",):
                            if record.proposed_series != new_series:
                                record.proposed_series = new_series
                                record.series_source = "folder_hierarchy"
                                fixed_series += 1
                elif not record.proposed_series:
                    # Файл прямо в корневой папке серии
                    if record.proposed_series != root_series:
                        record.proposed_series = root_series
                        record.series_source = "folder_hierarchy"
                        fixed_series += 1
                break

        if fixed_author or fixed_series:
            self.logger.log(f"[PASS 2 Series] Multiauthor fix: {fixed_author} authors, {fixed_series} series updated")

    def _is_variant_folder(self, folder_name: str) -> bool:
        """Вернуть True если имя подпапки указывает на альтернативную версию текста.

        Примеры: "Вариант с СИ (Ватный Василий)", "Альтернативный перевод",
                 "Черновик (автор)", "ЛП", "СИ" и т.п.
        В таких случаях серия наследуется от родительской папки.
        """
        folder_lower = folder_name.lower().replace('ё', 'е')
        for kw in self.variant_folder_keywords:
            kw_lower = kw.lower()
            # Для коротких ключевых слов (≤3 символа, напр. "ЛП", "СИ", "alt")
            # используем word-boundary, чтобы не срабатывать на подстроки
            # ("си" в "псионик" не должно давать True).
            # Для длинных — простое вхождение достаточно.
            if len(kw_lower) <= 3:
                if re.search(r'(?<![а-яёa-z])' + re.escape(kw_lower) + r'(?![а-яёa-z])',
                             folder_lower):
                    return True
            else:
                if kw_lower in folder_lower:
                    return True
        return False

    def _propagate_ancestor_folder_authors(self, records: List[BookRecord]) -> None:
        """
        Распространение автора из папки-предка на все файлы под ней.

        Принцип: папка — самый надёжный источник автора (100% датасет).
        Если имя любой папки-предка файла парсится как имя автора
        (через folder_author_parser), то все файлы под этой папкой
        получают этого автора с source='folder_dataset'.

        Правила:
        - Используем ВЫСШУЮ (ближе к корню) папку, которая парсится как автор.
        - Файлы с уже установленным author_source='folder_dataset' НЕ трогаем
          (они уже получили правильного автора из Pass 1 или более точного подпути).
        - "и др", "et al." и подобные суффиксы из имени автора убираются.
        - Работает для любых вложенных структур (Коллекция / СерияАвтор / Подсерия / Файл).
        """
        from passes.folder_author_parser import parse_author_from_folder_name

        # Список суффиксов-заменителей соавторов, которые нужно убирать
        _ET_AL_PATTERN = re.compile(
            r'\s*(и\s+др\.?|и\s+другие|et\s+al\.?|and\s+others)\s*$',
            re.IGNORECASE
        )

        def _parse_folder_author(folder_name: str) -> str:
            """Попытаться распознать автора из имени папки, вернуть '' если не удалось."""
            # Быстрая проверка через filename_blacklist — слова издателей/серий.
            # Используем word-boundary matching чтобы короткие записи ("СИ", "ЛП" и т.п.)
            # не давали ложных срабатываний внутри слов (напр. "СИ" в "макСИм").
            folder_lower = folder_name.lower()
            for bl in self.filename_blacklist:
                bl_lower = bl.lower()
                if re.search(r'(?<![а-яёa-z])' + re.escape(bl_lower) + r'(?![а-яёa-z])',
                             folder_lower):
                    return ''
            author = parse_author_from_folder_name(
                folder_name,
                male_names=self.male_names,
                female_names=self.female_names,
            )
            if not author:
                return ''
            # Валидация: убеждаемся что извлечённое имя содержит реальное имя человека.
            # Это отсеивает коллекционные папки вроде «Романы МИФ. Один момент - целая жизнь»,
            # которые parse_author_from_folder_name может неверно распознать как автора.
            # Используем ту же логику что и precache._contains_valid_name, с корректным
            # lookbehind чтобы «Ф» в «МИФ.» не считался инициалом.
            author_valid = False
            # 1. Проверка по спискам имён
            for word in author.split():
                word_clean = word.strip('.,;:!?').lower()
                if word_clean in self.male_names or word_clean in self.female_names:
                    author_valid = True
                    break
            # 2. Паттерн инициала: "А.Фамилия" — инициал не должен быть частью слова (МИ<Ф>)
            if not author_valid:
                if re.search(r'(?<![а-яёА-Я])[А-Я]\.*\s*[А-Я][а-яё]+', author):
                    author_valid = True
            if not author_valid:
                return ''
            # Убираем "и др", "et al." в конце
            author = _ET_AL_PATTERN.sub('', author).strip()
            return author

        _tfp_propagate = [
            p.lower() for p in
            (self.settings.get('translator_folder_prefixes', []) or [])
        ]

        propagated = 0
        for record in records:
            if record.author_source == "folder_dataset":
                continue  # Уже точно определён — не трогаем

            path_parts = Path(record.file_path).parts[:-1]  # все папки без самого файла
            # Прозрачно исключаем технические папки (fb2, epub и т.п.)
            path_parts = tuple(
                p for p in path_parts
                if p.lower() not in FILE_EXTENSION_FOLDER_NAMES
            )

            # Пропускаем файлы в папках-переводчиков (translator_folder_prefixes)
            if _tfp_propagate and any(
                p.lower().startswith(tuple(_tfp_propagate)) for p in path_parts
            ):
                continue

            # Идём от корня (самая высокая папка) к файлу, останавливаемся на первом совпадении
            for part in path_parts:
                parsed_author = _parse_folder_author(part)
                if parsed_author:
                    # ВАЛИДАЦИЯ ПРОТИВ МЕТАДАННЫХ: если у файла есть metadata_authors,
                    # проверяем что хотя бы одно слово из parsed_author присутствует в мете.
                    # Это отсекает ложные «авторы» вроде «Питер» (издательство в скобках),
                    # когда мета однозначно указывает на других людей.
                    # ВАЛИДАЦИЯ ПРОТИВ МЕТАДАННЫХ пропускается когда мета содержит только
                    # коллективный термин («Соавторство», «Сборник» и т.п.) — папка является
                    # единственным авторитетным источником в таких случаях.
                    _COLLECTIVE_TERMS = {"соавторство", "сборник", "[unknown]", "коллектив авторов"}
                    _meta_is_collective = (
                        record.metadata_authors and
                        record.metadata_authors.strip().lower().replace('ё', 'е') in _COLLECTIVE_TERMS
                    )
                    _proposed_is_collective = (
                        record.proposed_author and
                        record.proposed_author.strip().lower().replace('ё', 'е') in _COLLECTIVE_TERMS
                    )
                    if record.metadata_authors and not _meta_is_collective and not _proposed_is_collective:
                        author_words = set(parsed_author.lower().split())
                        meta_words = set(re.sub(r'[;,]', ' ', record.metadata_authors.lower()).split())
                        if author_words and meta_words and not (author_words & meta_words):
                            break  # Папка не подтверждена метой — не перезаписываем
                    if parsed_author != record.proposed_author or record.author_source != "folder_dataset":
                        record.proposed_author = parsed_author
                        record.author_source = "folder_dataset"
                        propagated += 1
                    break  # Высшая папка найдена — дальше не ищем

        if propagated:
            self.logger.log(f"[PASS 2 Series] Propagated ancestor folder author to {propagated} records")

    def _unify_folder_author_source(self, records: List[BookRecord]) -> None:
        """
        Унификация автора внутри одной папки по аналогии с _unify_folder_series_source.

        Правило: если хотя бы один файл в папке получил author_source='metadata_folder_confirmed',
        значит папка подтвердила автора. Все остальные файлы в той же папке, у которых
        author_source='metadata' (но ещё не подтверждён папкой), получают того же автора
        с source='metadata_folder_confirmed'.
        """
        from collections import defaultdict

        folder_groups = defaultdict(list)
        for record in records:
            folder_groups[str(Path(record.file_path).parent)].append(record)

        for folder, group in folder_groups.items():
            # Ищем файлы, подтверждённые папкой
            # Наивысший приоритет: folder_dataset — явно распознанная папка автора.
            # Если в папке есть хотя бы один такой файл, используем его автора как канонического.
            folder_dataset_records = [
                r for r in group
                if r.author_source == "folder_dataset" and r.proposed_author
            ]
            confirmed_records = [
                r for r in group
                if r.author_source == "metadata_folder_confirmed" and r.proposed_author
            ]
            if folder_dataset_records:
                # Большинство среди folder_dataset
                author_counts: dict = {}
                for r in folder_dataset_records:
                    author_counts[r.proposed_author] = author_counts.get(r.proposed_author, 0) + 1
                canonical_author = max(author_counts, key=author_counts.get)
            else:
                # Fallback: большинство среди metadata_folder_confirmed
                if not confirmed_records:
                    continue
                author_counts = {}
                for r in confirmed_records:
                    author_counts[r.proposed_author] = author_counts.get(r.proposed_author, 0) + 1
                canonical_author = max(author_counts, key=author_counts.get)

            # Применяем ко всем файлам в папке с source='metadata', 'metadata_folder_confirmed'
            # или 'filename' (если канонический автор из folder_dataset или из большинства
            # metadata_folder_confirmed — это тоже авторитетный источник).
            # folder_dataset не трогаем — они уже точно определены.
            # 'filename': автор мог быть ошибочно извлечён из имени файла (напр. из названия
            # серии в паттерне "Серия - Подсерия"), переопределяем авторитетным источником.
            _overrideable = {"metadata", "metadata_folder_confirmed"}
            if folder_dataset_records or confirmed_records:
                _overrideable.add("filename")
            for record in group:
                if record.author_source in _overrideable and record.proposed_author:
                    record.proposed_author = canonical_author
                    record.author_source = "metadata_folder_confirmed"

    def _unify_folder_series_source(self, records: List[BookRecord]) -> None:
        """
        Унификация series_source внутри одной папки.

        Правило: папка — единица доверия. Если хотя бы один файл в папке получил
        series_source='folder_hierarchy', значит именно папка является источником
        серии для ВСЕЙ папки. Все остальные файлы в папке получают ту же серию,
        если только у них нет собственного folder_hierarchy/folder_dataset источника
        с другой серией.

        ИСКЛЮЧЕНИЕ: если в папке файлы от НЕСКОЛЬКИХ авторов — это коллекция,
        унификация не применяется (иначе имя коллекции становится «серией»).
        """
        from collections import defaultdict

        folder_groups = defaultdict(list)
        for record in records:
            folder_groups[str(Path(record.file_path).parent)].append(record)

        for folder, group in folder_groups.items():
            # Если в папке файлы от нескольких авторов → коллекция, пропускаем
            authors_in_folder = {r.proposed_author.strip() for r in group if r.proposed_author}
            if len(authors_in_folder) > 1:
                continue

            # Ищем файлы, у которых папка переопределила мету (авторитетные источники).
            # folder_metadata_confirmed также считается авторитетным: папка и мета согласны.
            STRONG_SOURCES = {"folder_hierarchy", "folder_dataset", "folder_metadata_confirmed"}
            folder_hierarchy_records = [
                r for r in group
                if r.series_source in STRONG_SOURCES and r.proposed_series
            ]
            if not folder_hierarchy_records:
                continue

            # Каноническая серия — из авторитетных источников (мажоритарное голосование)
            series_counts: dict = {}
            for r in folder_hierarchy_records:
                series_counts[r.proposed_series] = series_counts.get(r.proposed_series, 0) + 1
            canonical_series = max(series_counts, key=series_counts.get)

            # Применяем ко ВСЕМ файлам в папке, у которых источник не является авторитетным.
            for record in group:
                if record.series_source not in STRONG_SOURCES:
                    record.proposed_series = canonical_series
                    record.series_source = "folder_hierarchy"

    # Паттерн для извлечения имени серии из стема "Автор. Серия-N. Заголовок"
    _UMBRELLA_SERIES_RE = re.compile(r'^.+?\.\s+(.+?)-\d+\b', re.UNICODE)

    def _split_umbrella_folder_series(self, records: List[BookRecord]) -> None:
        """
        Обнаруживает umbrella-папки: одна папка содержит 2+ самостоятельных подсерии.

        Пример: папка «Вторая дорога» содержит файлы «Вторая дорога-1,2,3» и
        «Друзья офицера-1,2,3» — это две отдельные трилогии, не одна серия.

        Условие срабатывания:
          • Все записи в папке имеют series_source='folder_hierarchy' и одинаковый
            proposed_series (имя папки).
          • Стемы файлов раскрываются в ≥2 различных названий серий, каждое
            встречается ≥2 раз.

        Действие: переопределяем proposed_series каждой записи на название серии
        из имени файла. series_source остаётся 'folder_hierarchy'.
        """
        from collections import defaultdict

        folder_groups: dict = defaultdict(list)
        for record in records:
            folder_groups[str(Path(record.file_path).parent)].append(record)

        for folder, group in folder_groups.items():
            # Только если все записи из одной папки получили folder_hierarchy
            if not all(r.series_source == "folder_hierarchy" for r in group):
                continue

            folder_series_values = {r.proposed_series for r in group if r.proposed_series}
            if len(folder_series_values) != 1:
                continue  # уже разные серии — не трогаем

            # Извлекаем серию из стема каждого файла
            stem_series: dict = {}  # id(record) → extracted series
            for record in group:
                stem = Path(record.file_path).stem
                m = self._UMBRELLA_SERIES_RE.match(stem)
                if m:
                    stem_series[id(record)] = m.group(1).strip()

            if not stem_series:
                continue

            # Группируем записи по извлечённому имени серии
            by_series: dict = defaultdict(list)
            for record in group:
                s = stem_series.get(id(record))
                if s:
                    by_series[s].append(record)

            # Срабатываем только если ≥2 различных серий, каждая с ≥2 книгами
            qualified = {s: recs for s, recs in by_series.items() if len(recs) >= 2}
            if len(qualified) < 2:
                continue

            # Переопределяем proposed_series из стема файла
            for record in group:
                extracted = stem_series.get(id(record))
                if extracted and extracted in qualified:
                    record.proposed_series = extracted

    def _clear_multiauthor_folder_series(self, records: List[BookRecord]) -> None:
        """Сбросить серию для папок с книгами РАЗНЫХ авторов (коллекций).

        Правило: папка серии ВСЕГДА находится внутри папки автора.
        Если папка содержит книги разных авторов — это тематическая коллекция,
        и её имя не может быть серией ни из какого источника.

        Для каждого файла в папке-коллекции: если proposed_series совпадает с именем
        папки (из любого источника) — сбрасываем.
        """
        from collections import defaultdict

        folder_files: dict = defaultdict(list)
        for record in records:
            folder_files[str(Path(record.file_path).parent)].append(record)

        cleared = 0
        for folder_path, files_in_folder in folder_files.items():
            # proposed_author нормализован; metadata_authors — запасной для folder_dataset записей.
            # Используем proposed_author как основной критерий.
            unique_proposed = {
                f.proposed_author.strip()
                for f in files_in_folder
                if f.proposed_author and f.proposed_author.strip() not in ('', 'Сборник')
            }
            if len(unique_proposed) <= 1:
                continue  # Один (или ноль) предлагаемых авторов — не коллекция

            # Имя папки-коллекции (нормализованное для сравнения)
            folder_name_norm = Path(folder_path).name.lower().replace('ё', 'е').strip()

            for record in files_in_folder:
                if not record.proposed_series:
                    continue
                # Папочный источник авторитетен — не сбрасываем
                if record.series_source in ('folder_dataset', 'folder_hierarchy',
                                            'folder_meta_consensus', 'folder_metadata_confirmed'):
                    continue
                ps_norm = record.proposed_series.lower().replace('ё', 'е').strip()
                # Совпадение: proposed == folder_name или одно является префиксом другого
                if ps_norm == folder_name_norm or folder_name_norm.startswith(ps_norm) or ps_norm.startswith(folder_name_norm) or (len(ps_norm) >= 5 and ps_norm in folder_name_norm):
                    record.proposed_series = ''
                    record.series_source = ''
                    cleared += 1

        if cleared:
            self.logger.log(f"[PASS 2] Cleared {cleared} series matching collection folder name")
            print(f"[PASS 2] Cleared {cleared} series from multi-author collection folders")

    def _unify_series_folder_authors(self, records: List[BookRecord]) -> None:
        """Унифицировать авторов в папках типа 'Серия (Автор)'.

        Когда папка распознана как 'Серия (Автор)' и разные книги содержат разные
        вариации имени одного автора (напр. 'Гвор Виктор' и 'Гвор Михаил'), собираем
        всех авторов с совпадающей фамилией из папки и назначаем объединённый список
        всем книгам в этой папке.

        Пример: папка 'Волхвы Скрытной Управы (Гвор)'
          Книга 1: 'Гвор Виктор, Рагимов Михаил' → фильтруем по 'гвор' → 'Гвор Виктор'
          Книга 2: 'Гвор Михаил'                 → фильтруем по 'гвор' → 'Гвор Михаил'
          Итог для обеих: 'Гвор Виктор, Гвор Михаил'
        """
        if not self.compiled_folder_patterns:
            return

        from collections import defaultdict
        folder_records: dict = defaultdict(list)
        for record in records:
            folder = str(Path(record.file_path).parent)
            folder_records[folder].append(record)

        unified = 0
        for folder_path, recs in folder_records.items():
            folder_name = Path(folder_path).name
            # Найти "Серия (Автор)" паттерн для этой папки
            extracted_surname = None
            for _p_str, _p_re, _p_groups in self.compiled_folder_patterns:
                if 'series' not in _p_groups or 'author' not in _p_groups:
                    continue
                m = _p_re.match(folder_name)
                if not m:
                    continue
                extracted_surname = m.group('author').strip().lower().replace('ё', 'е')
                break
            if not extracted_surname or len(extracted_surname.replace(' ', '')) < 3:
                continue

            # Собрать уникальных авторов из папки, чьё имя содержит фамилию из паттерна
            # Для мультиавторных строк ("Гвор Виктор, Рагимов Михаил") — разбиваем и фильтруем
            matched_authors: list = []
            seen: set = set()
            for rec in recs:
                if not rec.proposed_author:
                    continue
                for part in re.split(r',\s*', rec.proposed_author):
                    part = part.strip()
                    if not part:
                        continue
                    part_norm = part.lower().replace('ё', 'е')
                    surname_words = re.sub(r'[^\w]', ' ', extracted_surname).split()
                    if any(sw in part_norm for sw in surname_words if len(sw) > 2):
                        key = part_norm
                        if key not in seen:
                            seen.add(key)
                            matched_authors.append(part)

            if len(matched_authors) <= 1:
                continue  # Нет смысла объединять одного автора

            combined = ', '.join(matched_authors)
            for rec in recs:
                if rec.proposed_author != combined:
                    rec.proposed_author = combined
                    rec.author_source = 'folder_dataset'
                    unified += 1

        if unified:
            self.logger.log(f"[PASS 2] Unified authors in Series(Author) folders: {unified} records")
            print(f"[PASS 2] Unified {unified} records with Series(Author) folder authors")

    def _apply_folder_consensus(self, records: List[BookRecord]) -> None:
        """
        Папочный консенсус: если папка содержит файлы с series_source = "folder_dataset",
        то ВСЕ файлы в этой папке должны получить одинаковую серию.
        
        ВАЖНО: Применяется ТОЛЬКО к папкам ОДНОГО автора!
        Если папка содержит файлы РАЗНЫХ авторов → это коллекция, consensusне применяется.
        
        Логика:
        1. Группируем файлы по папке (parent directory)
        2. Проверяем: все ли файлы от ОДНОГО автора? Если нет → skip (это коллекция)
        3. Ищем файлы с series_source = "folder_dataset"
        4. Берем серию из первого такого файла (обычно это название папки)
        5. Применяем эту серию ко ВСЕМ остальным файлам в папке
        
        Пример коллекции (skip consensus):
        Папка: "Боевая фантастика. Циклы"
        - Авраменко. Цикл «Солдат удачи» (АВТОР: Авраменко) 
        - Анисимов. Цикл «Вариант «Бис» (АВТОР: Анисимов) ← РАЗНЫЕ АВТОРЫ!
        → Consensus NOT applied (это коллекция)
        
        Пример папки-серии (apply consensus):
        Папка: "Авраменко Александр/Солдат удачи"
        - 1. Солдат удачи (АВТОР: Авраменко)
        - 2. Князь Терранский (АВТОР: Авраменко) ← ОДИН АВТОР!
        - 3. Взор Тьмы (АВТОР: Авраменко)
        → Consensus applied
        """
        from collections import defaultdict
        
        # Группируем файлы по папке
        folder_files = defaultdict(list)
        for record in records:
            folder_path = str(Path(record.file_path).parent)
            folder_files[folder_path].append(record)
        
        # Для каждой папки применяем консенсус
        for folder_path, files_in_folder in folder_files.items():
            # ПРОВЕРКА 1: все ли файлы в папке от ОДНОГО автора?
            # Извлекаем уникальные авторов в этой папке
            authors_in_folder = set()
            for f in files_in_folder:
                if f.proposed_author:
                    # Нормализуем для сравнения (без разрывов строк и пробелов)
                    author = f.proposed_author.strip()
                    if author:
                        authors_in_folder.add(author)
            
            # Если авторов больше одного → это коллекция, skip consensus
            if len(authors_in_folder) > 1:
                continue
            
            # Ищем файлы с series_source = "folder_dataset"
            folder_dataset_files = [
                f for f in files_in_folder 
                if f.series_source == "folder_dataset" and f.proposed_series
            ]
            
            if not folder_dataset_files:
                continue  # В этой папке нет файлов с folder_dataset
            
            # Берем серию из первого файла (они должны быть одинаковые)
            canonical_series = folder_dataset_files[0].proposed_series
            
            # Применяем эту серию ко ВСЕМ файлам в папке
            for record in files_in_folder:
                if record.series_source != "folder_dataset":
                    # Переопределяем серию на основе папочного консенсуса
                    record.proposed_series = canonical_series
                    record.series_source = "folder_dataset"

        # ДОПОЛНИТЕЛЬНЫЙ КОНСЕНСУС: папки с metadata-серией.
        # Если большинство файлов одного автора в папке имеют одинаковую серию
        # из любого источника — применяем её к аутсайдерам (файлам с другой серией).
        # Это исправляет случаи когда часть файлов имеет правильную серию из metadata,
        # а остальные — издательскую мета-серию («Военная фантастика (АСТ)»).
        for folder_path, files_in_folder in folder_files.items():
            if len(files_in_folder) < 2:
                continue
            authors_in_folder = {f.proposed_author.strip() for f in files_in_folder if f.proposed_author}
            if len(authors_in_folder) > 1:
                continue  # Разные proposed_author — не трогаем
            # Главное правило: если в папке книги РАЗНЫХ реальных авторов —
            # это жанровая коллекция или издательская серия, а не авторская серия.
            # Имя папки (например, «МИФ Проза», «Клуб убийств») = ярлык, не серия.
            # Проверяем metadata_authors, т.к. folder_dataset назначает имя папки
            # как proposed_author для всех файлов, маскируя реальное разнообразие.
            real_meta_authors = {
                f.metadata_authors.strip()
                for f in files_in_folder
                if f.metadata_authors and f.metadata_authors.strip() not in ('[unknown]', '')
            }
            if len(real_meta_authors) > 1:
                continue  # Многоавторная коллекция — не трогаем
            # Считаем голоса за каждую серию (источниками выше metadata)
            from collections import Counter
            series_votes = Counter(
                f.proposed_series
                for f in files_in_folder
                if f.proposed_series and f.series_source != "folder_dataset"
            )
            if not series_votes:
                continue
            top_series, top_count = series_votes.most_common(1)[0]
            if top_count <= 1:
                continue
            # Нормализованная база top_series для сравнения
            import re as _re_ac
            def _norm_base(s: str) -> str:
                s = _re_ac.sub(r'\s*\([^)]*\)\s*$', '', s).strip()
                s = _re_ac.sub(r'\s*\[[^\]]*\]\s*$', '', s).strip()
                s = _re_ac.sub(r'\s+\d+[\s\.\:].*$', '', s).strip()
                s = _re_ac.sub(r'\s+\d+\s*$', '', s).strip()
                return s.lower().replace('ё', 'е')
            top_base = _norm_base(top_series)
            # Применяем только к файлам без серии или с низкоприоритетным источником.
            # Не трогаем то, что уже надёжно определено из имени файла.
            for record in files_in_folder:
                if record.proposed_series != top_series:
                    if record.series_source not in ('filename', 'filename+meta_confirmed'):
                        # Не навязываем серию файлу, у которого нет metadata_series —
                        # он скорее всего не принадлежит этой серии (просто тот же автор).
                        if not record.metadata_series:
                            continue
                        # Не навязываем серию если metadata_series указывает на ДРУГУЮ серию.
                        if _norm_base(record.metadata_series) != top_base:
                            continue
                        record.proposed_series = top_series
                        record.series_source = "author-consensus"
                    else:
                        # Файл уже имеет серию из имени файла.
                        # Исправляем если его серия является суффиксом/частью top_series —
                        # это признак того, что парсер обрезал префикс через " - ".
                        # Пример: "Миха" ⊂ "Я - Миха" → исправить до "Я - Миха".
                        rec_series = (record.proposed_series or '').lower().replace('ё', 'е')
                        top_lower = top_series.lower().replace('ё', 'е')
                        if (rec_series and rec_series != top_lower
                                and top_lower.endswith(rec_series)
                                and not record.metadata_series):
                            record.proposed_series = top_series
                            record.series_source = "author-consensus"

    def _extract_series_from_filename(self, file_path: str, validate: bool = True, metadata_series: str = "", known_series: str = "", proposed_author: str = "") -> str:
        """
        Извлечь серию из имени файла, используя паттерны из конфига.
        
        ОБНОВЛЕНО: Теперь использует BlockLevelPatternMatcher для точного извлечения!
        + ДОБАВЛЕНО: Подтверждение результата с помощью metadata_series
        
        Применяет (в порядке приоритета):
        1. BlockLevelPatternMatcher (структурный анализ блоков) + подтверждение metadata
        2. Паттерны из конфига (author_series_patterns_in_files)
        3. [Серия] - квадратные скобки в начале
        4. Серия (лат. буквы/цифры) - скобки в конце с сервис-словами
        5. Серия. Название - точка как разделитель в начале
        
        Args:
            file_path: Путь к файлу
            validate: Если True - проверять валидность; если False - возвращать raw candidate
            metadata_series: Метаинформация о серии из FB2 (для подтверждения результата BlockLevelPatternMatcher)
        """
        filename = Path(file_path).name
        name_without_ext = filename.rsplit('.', 1)[0]

        # ВАЖНО: Удалить метатеги из конца filename ПЕРЕД парсингом
        # "(СИ)" - Самиздат/Интернет
        # "(ЛП)" - Лицензионное произведение
        # "(др. изд.)" / "(другое издание)" - ссылки на другое издание
        # Эти метатеги не должны влиять на извлечение series
        name_for_parsing = re.sub(r'\s*\([СЛ]И\)\s*$', '', name_without_ext).strip()
        name_for_parsing = re.sub(r'\s*\([^)]*(?:издание|изд\.)[^)]*\)\s*$', '', name_for_parsing, flags=re.IGNORECASE).strip()
        # Убрать год-суффикс (1900–2099) в конце — год не является номером тома.
        # "Тысяча и одна ночь. Том Ⅰ - 2022" → "Тысяча и одна ночь. Том Ⅰ"
        # "Серия. Название - 2019" → "Серия. Название"
        name_for_parsing = re.sub(r'(?:\s*[-–—])?\s*(?:19|20)\d{2}\s*$', '', name_for_parsing).strip()


        
        # 🔑 Флаг: найден паттерн БЕЗ Series информации
        pattern_found_without_series = False
        # 🔑 Флаг: блок-матчер нашёл серию с высокой уверенностью (score=1.0)
        # При таком score title-as-series guard не должен отбрасывать результат
        block_matcher_high_confidence = False
        self._last_from_block_matcher = False

        # ══════════════════════════════════════════════════════════════════
        # ШАГ 0: Двухуровневая иерархия «Серия N. Подсерия M. Название»
        # Паттерн: после автора идут ДВА именованных уровня с номерами,
        # затем заголовок. Пример:
        #   «Земной круг 1. Первый закон 2. Прежде чем их повесят»
        #   → proposed_series = «Земной круг 1\Первый закон»
        # Подтверждение: metadata_series совпадает с root или subseries.
        # ══════════════════════════════════════════════════════════════════
        # Вариант А: «Серия N. Подсерия M. Название»
        _TWO_LEVEL_RE = re.compile(
            r'^(.+?)\s+(\d{1,4})\.\s+([А-ЯЁA-Z][^.]{2,}?)\s+(\d{1,4})\.\s+(.+)$',
            re.UNICODE,
        )
        # Вариант Б: «Серия N. Подсерия. Том/Книга M» — подсерия без своего номера
        _TWO_LEVEL_TOM_RE = re.compile(
            r'^(.+?)\s+(\d{1,4})\.\s+([А-ЯЁA-Z][^.]{2,}?)\.\s+(?:Том|Книга|Часть|кн\.|Book|Vol\.?)\s+\d',
            re.IGNORECASE | re.UNICODE,
        )
        # Применяем только к части после " - " чтобы не захватывать автора
        _name_after_dash = name_for_parsing
        if ' - ' in name_for_parsing:
            _name_after_dash = name_for_parsing.split(' - ', 1)[1]
        _meta_norm = _nfc_lower_yo
        def _word_overlap(a: str, b: str) -> int:
            wa = {w for w in a.split() if len(w) >= 4}
            wb = {w for w in b.split() if len(w) >= 4}
            return len(wa & wb)
        _tl = _TWO_LEVEL_RE.match(_name_after_dash)
        if _tl and metadata_series:
            _root_name = _tl.group(1).strip()
            _root_num  = _tl.group(2)
            _sub_name  = _tl.group(3).strip()
            _meta_low  = _meta_norm(metadata_series.strip())
            if _meta_low == _meta_norm(_root_name) or _meta_low == _meta_norm(_sub_name):
                # Корень серии: known_series из папки/файла — ground truth.
                # Если папка серию не дала — fallback: metadata подтверждает sub,
                # значит root загрязнён авторским префиксом («Аберкромби. Земной круг»).
                _root_out = _root_name
                if known_series:
                    _known_root = known_series.split('\\')[0].strip()
                    _known_root = re.sub(r'\s+\d+\s*$', '', _known_root).strip()
                    if _known_root and _meta_norm(_root_name).endswith(_meta_norm(_known_root)):
                        _root_out = _known_root
                elif _meta_low == _meta_norm(_sub_name) and _meta_low != _meta_norm(_root_name):
                    # metadata — это sub, не root → root может содержать «Автор. Серия»
                    if '. ' in _root_name:
                        _stripped = _root_name.split('. ', 1)[1].strip()
                        if _stripped:
                            _root_out = _stripped
                return f'{_root_out} {_root_num}\\{_sub_name}'
        # Вариант Б: metadata подтверждает root-серию, подсерия без номера
        _tl2 = _TWO_LEVEL_TOM_RE.match(_name_after_dash)
        if _tl2 and metadata_series:
            _root_name = _tl2.group(1).strip()
            _root_num  = _tl2.group(2)
            _sub_name  = _tl2.group(3).strip()
            _meta_low  = _meta_norm(metadata_series.strip())
            if _meta_low == _meta_norm(_root_name) or _meta_low == _meta_norm(_sub_name):
                return f'{_root_name} {_root_num}\\{_sub_name}'

        # Вариант В: «Серия N. Подсерия N-M. Название» — диапазон томов внутри подсерии.
        # Пример: «Брия 1. Книга Длинного Солнца 1-2. Литания Длинного Солнца»
        #   → proposed_series = «Брия 1\Книга Длинного Солнца»
        # Условие безопасности: metadata_series обязательно подтверждает
        # либо root_name, либо sub_name (точно или по пересечению ≥2 слов длиной ≥4).
        _TWO_LEVEL_RANGE_RE = re.compile(
            r'^(.+?)\s+(\d{1,4})\.\s+([А-ЯЁA-Z][^.]{2,}?)\s+\d{1,4}\s*[-–—]\s*\d{1,4}\.\s+.+$',
            re.UNICODE,
        )
        _tlr = _TWO_LEVEL_RANGE_RE.match(_name_after_dash)
        if _tlr and metadata_series:
            _root_name = _tlr.group(1).strip()
            _root_num  = _tlr.group(2)
            _sub_name  = _tlr.group(3).strip()
            _meta_low  = _meta_norm(metadata_series.strip())
            _sub_low   = _meta_norm(_sub_name)
            _confirmed = (
                _meta_low == _meta_norm(_root_name)
                or _meta_low == _sub_low
                or _word_overlap(_meta_low, _sub_low) >= 2
            )
            if _confirmed:
                return f'{_root_name} {_root_num}\\{_sub_name}'

        # Вариант Г: «Серия N. Подсерия (ServiceWord)» — скомпилированный файл.
        # Пример: «Брия 1. Книга Длинного Солнца (Тетралогия)»
        #   → proposed_series = «Брия 1\Книга Длинного Солнца»
        _SERVICE_SUFFIX_RE = re.compile(
            r'^(.+?)\s+(\d{1,4})\.\s+([А-ЯЁA-Z][^(]{2,}?)\s*\((?:Тетралогия|Трилогия|Дилогия|Пенталогия|Сага|Цикл|[Сс]борник|[Аа]нтология)\)\s*$',
            re.UNICODE,
        )
        _tlg = _SERVICE_SUFFIX_RE.match(_name_after_dash)
        if _tlg and metadata_series:
            _root_name = _tlg.group(1).strip()
            _root_num  = _tlg.group(2)
            _sub_name  = _tlg.group(3).strip()
            _meta_low  = _meta_norm(metadata_series.strip())
            _sub_low   = _meta_norm(_sub_name)
            _confirmed = (
                _meta_low == _meta_norm(_root_name)
                or _meta_low == _meta_norm(f'{_root_name} {_root_num}')
                or _meta_low == _sub_low
                or _meta_low == _meta_norm(f'{_root_name} {_root_num}\\{_sub_name}')
                or (_meta_low.startswith(_meta_norm(_root_name)) and _meta_low.endswith(_sub_low))
                or _word_overlap(_meta_low, _sub_low) >= 2
            )
            if _confirmed:
                return f'{_root_name} {_root_num}\\{_sub_name}'

        # ══════════════════════════════════════════════════════════════════
        # ШАГ 0.5: «Фамилия N НазваниеСерии. Том N» (без тире-разделителя)
        # Пример: «Краснов 1 Последние дни Российской империи. Том 1»
        # Файл лежит в коллекционной папке — автор и серия закодированы
        # прямо в имени файла без стандартного разделителя « - ».
        # ══════════════════════════════════════════════════════════════════
        _SURNAME_N_SERIES_TOM = re.compile(
            r'^([А-ЯЁA-Z][а-яёa-zA-Z-]+)\s+(\d{1,4})\s+([А-ЯЁ].+?)'
            r'\.\s+(?:Том|Часть|Книга|Кн\.|Book|Vol\.?)\s+\d+\s*$',
            re.UNICODE,
        )
        _snst = _SURNAME_N_SERIES_TOM.match(name_for_parsing)
        if _snst:
            _snst_surname = _snst.group(1).lower().replace('ё', 'е')
            _snst_series_name = _snst.group(3).strip()
            # Проверяем все авторы (";"-разделитель) и оба конца слова (фамилия м.б. первым или последним)
            _surname_ok = not proposed_author
            if proposed_author and not _surname_ok:
                for _auth_part in re.split(r'[;]\s*', proposed_author):
                    _words = _auth_part.strip().split()
                    if not _words:
                        continue
                    for _candidate in (_words[0].lower().replace('ё', 'е'),
                                       _words[-1].lower().replace('ё', 'е')):
                        if (_snst_surname == _candidate
                                or _snst_surname.startswith(_candidate)
                                or _candidate.startswith(_snst_surname)):
                            _surname_ok = True
                            break
                    if _surname_ok:
                        break
            if _surname_ok and _snst_series_name:
                return _snst_series_name

        # ══════════════════════════════════════════════════════════════════
        # ШАГ 1 (NEW): Попробовать BlockLevelPatternMatcher 🎯
        # ══════════════════════════════════════════════════════════════════
        try:
            series_from_block = None
            file_patterns = self.settings.get_author_series_patterns_in_files() or []

            if file_patterns:
                best_score, best_pattern, _, series_from_block = self.block_matcher.find_best_pattern_match(
                    name_for_parsing, file_patterns
                )
                # 🔑 КРИТИЧНО: Проверить что паттерн содержит информацию о серии!
                # Если паттерн не содержит слова "Series", то это не формат с серией
                # Примеры БЕЗ серии: "Title (Author)", "Author - Title", "Author. Title"
                # Примеры С серией: "Title (Author. Series)", "Author - Title (Series)", "Author - Series. Title"
                pattern_str = best_pattern.get('pattern', '') if isinstance(best_pattern, dict) else str(best_pattern or '')
                pattern_has_subseries = 'subseries' in pattern_str.lower()
                if 'Series' not in pattern_str:
                    # Паттерн не содержит Series - игнорируем результат BlockLevelPatternMatcher
                    pattern_found_without_series = True  # ← ЗАПОМНИТЬ что паттерн БЕЗ Series!
                    series_from_block = None
                
                # Проверяем что это валидная серия
                if series_from_block and (not validate or self._is_valid_series(series_from_block, skip_author_check=True)):
                    # ✅ ДОБАВЛЕНО: Подтверждение результата с помощью metadata
                    # Если есть metadata_series - проверяем совпадает ли она с найденной
                    # Но сначала отбрасываем metadata_series если это blacklist-слово (издатель/серия-обёртка)
                    _effective_metadata_series = metadata_series.replace('\u2026', '...') if metadata_series else metadata_series
                    if _effective_metadata_series and self.filename_blacklist:
                        _ms_lower = _effective_metadata_series.lower()
                        if any(bl.lower() in _ms_lower for bl in self.filename_blacklist):
                            _effective_metadata_series = None
                    if _effective_metadata_series:
                        # Очищаем оба значения для сравнения
                        metadata_cleaned = self._extract_series_from_brackets(
                            self._extract_main_series_from_multi_level(_effective_metadata_series)
                        ).strip()
                        series_from_block_cleaned = self._extract_main_series_from_multi_level(series_from_block).strip()
                        
                        # Если НЕ совпадают - это сигнал, что BlockLevelPatternMatcher ошибся
                        # Не возвращаем результат, продолжаем со старыми методами
                        if metadata_cleaned.lower() != series_from_block_cleaned.lower():
                            # Проверяем: может metadata совпадает с КОМПОНЕНТОМ иерархии
                            # Пример: metadata="Хроники Кайлара", hierarchy="Кодекс ка'кари\Хроники Кайлара"
                            hierarchy_components = [c.strip().lower() for c in series_from_block_cleaned.split('\\') if c.strip()]
                            if metadata_cleaned.lower() in hierarchy_components:
                                # ✅ Metadata подтвердила один уровень иерархии
                                # Если metadata = ROOT компонент → возвращаем только root (безопасно)
                                # Если metadata = более глубокий уровень → полная цепочка подтверждена
                                # Пример (root): metadata="Третий Рим", hierarchy="Третий Рим\Последний натиск..."
                                #   → root подтверждён, но subseries не подтверждена → вернуть "Третий Рим"
                                # Пример (deep): metadata="Хроники Кайлара", hierarchy="Кодекс ка'кари\Хроники Кайлара"
                                #   → глубокий уровень подтверждён → вернуть полную цепочку
                                if series_from_block_cleaned:
                                    root_cmp = hierarchy_components[0] if hierarchy_components else ''
                                    if metadata_cleaned.lower() == root_cmp:
                                        # Root подтверждён. Если паттерн явно описывает SubSeries —
                                        # доверяем полной иерархии (паттерн знает о подсерии).
                                        # Пример: metadata="Второй Апокалипсис", pattern has Subseries
                                        #   → возвращаем "Второй Апокалипсис\Аспект-Император"
                                        if pattern_has_subseries:
                                            # Safety: если арк содержит корень — ложная иерархия
                                            # «Хоттабыч\Позывной Хоттабыч»: «хоттабыч» ⊂ «позывной хоттабыч»
                                            _sfb_parts = series_from_block_cleaned.split('\\', 1)
                                            if len(_sfb_parts) == 2:
                                                _arc_lc = _sfb_parts[1].lower().replace('ё', 'е')
                                                if root_cmp and root_cmp in _arc_lc:
                                                    return _sfb_parts[0].strip()
                                            return series_from_block_cleaned
                                        # Без Subseries в паттерне: subseries не подтверждена → только root
                                        return series_from_block_cleaned.split('\\')[0].strip()
                                    else:
                                        return series_from_block_cleaned
                            # Спец-случай: metadata — префикс block-серии, разница = одно сервисное слово.
                            # Пример: metadata="Дублинская", block="Дублинская серия"
                            # → "серия" — сервисное слово, но является частью названия.
                            # Файловое имя точнее → возвращаем полное название из filename.
                            elif not hierarchy_components or metadata_cleaned.lower() not in hierarchy_components:
                                _blk_lc = series_from_block_cleaned.lower()
                                _met_lc = metadata_cleaned.lower()
                                if _blk_lc.startswith(_met_lc + ' '):
                                    _suffix = _blk_lc[len(_met_lc):].strip()
                                    if any(_suffix == sw.lower() for sw in self.service_words if sw):
                                        processed_series = self._extract_main_series_from_multi_level(series_from_block)
                                        if processed_series:
                                            return processed_series
                            if best_score >= 0.85 and series_from_block_cleaned:
                                # Высокий score, но metadata не подтвердила иерархию
                                if '\\' in series_from_block_cleaned:
                                    # Если паттерн явно описывает SubSeries — доверяем полной иерархии
                                    # Пример: "Author - Series. Subseries. Title" → metadata="Звёздные Войны"
                                    # (обёртка-серия) не должна отменять найденную подсерию
                                    if pattern_has_subseries:
                                        return series_from_block_cleaned
                                    root = series_from_block_cleaned.split('\\')[0].strip()
                                    if root:
                                        return root
                                return series_from_block_cleaned
                            # ВНИМАНИЕ: Результат BlockLevelPatternMatcher не совпадает с metadata!
                            # Это может быть ошибка распознавания (例: "1-2 книги" вместо "Император из стали")
                            # Продолжаем без этого результата
                        else:
                            # ✅ Metadata подтвердила результат BlockLevelPatternMatcher!
                            processed_series = self._extract_main_series_from_multi_level(series_from_block)
                            if processed_series:
                                return processed_series
                            # processed_series пуст (напр. аббревиатура О.Р.З.) → не возвращаем сырое значение
                    else:
                        # Нет metadata для проверки, используем результат BlockLevelPatternMatcher как есть
                        processed_series = self._extract_main_series_from_multi_level(series_from_block)
                        if processed_series:
                            # Без metadata нельзя подтвердить многоуровневую иерархию.
                            # Исключение 1: паттерн явно содержит SubSeries — иерархия описана
                            # намеренно, доверяем всей строке даже без подтверждения metadata.
                            # Пример: "Author - Series. Subseries. Title" при score=1.0
                            # Исключение 2: series_from_block — многоуровневый контент скобок
                            # с сервисным словом в конце: «Серия N. Подсерия. Тетралогия»
                            # → подсерия значимая (это подцикл), не стираем.
                            _raw_parts = series_from_block.split('. ')
                            _last_raw_lc = _raw_parts[-1].strip().lower() if _raw_parts else ''
                            _raw_has_service_end = (
                                len(_raw_parts) >= 3 and
                                any(_last_raw_lc.startswith(sw.lower())
                                    for sw in self.service_words if sw)
                            )
                            if '\\' in processed_series and not pattern_has_subseries and not _raw_has_service_end:
                                root = processed_series.split('\\')[0].strip()
                                if root:
                                    processed_series = root
                            # Mark: this result came from block matcher with high confidence (score=1.0)
                            # so title-as-series guard in caller should not discard it
                            self._last_from_block_matcher = (best_score >= 0.99)
                            return processed_series
                        # processed_series пуст → аббревиатура или мусор, не возвращаем сырое значение
        except Exception as e:
            # Если случится ошибка, продолжаем со старым методом
            pass
        
        # ══════════════════════════════════════════════════════════════════
        # ШАГ 2 (OLD): Резервный метод - старые паттерны
        # ══════════════════════════════════════════════════════════════════
        
        # Анализируем структуру файла один раз
        file_blocks = self.block_selector.analyze_filename_blocks(name_for_parsing)
        
        best_series = None
        best_score = -999
        best_pattern = None
        
        if self.compiled_file_patterns:
            for idx, (pattern_str, compiled_regex, group_names) in enumerate(self.compiled_file_patterns, 1):
                # Проверяем regex совпадение
                match = compiled_regex.match(name_for_parsing)
                if not match:
                    continue
                
                # Извлекаем series из match
                series_candidate = None
                series_group_name = None
                
                # ✅ ДОБАВЛЕНО: Проверить что Title не состоит только из точек (это false-match)
                title_candidate = None
                for g_name in group_names:
                    if 'title' in g_name:
                        title_candidate = match.group(g_name).strip() if g_name in group_names else None
                        break
                
                # Если Title это только точки - skip this pattern (it's a false match)
                if title_candidate and all(c == '.' for c in title_candidate):
                    continue

                # Если Title начинается с "- " — паттерн захватил разделитель автор/название
                # как часть Title; это признак ложного совпадения (напр. К.Дж. → Series="Дж", Title="- Доминион")
                if title_candidate and title_candidate.startswith(('- ', '– ', '— ')):
                    continue

                for g_name in group_names:
                    if 'series' in g_name:
                        series_group_name = g_name
                        break
                
                # ✅ ЗАЩИТА: Если паттерн содержит service_words группу,
                # проверить что захваченное значение — действительно служебное слово.
                # Иначе паттерн "Author. service_words «Series»" сработает на любом тексте
                # перед «», например "Легенда о «Ночном дозоре»" → service_words="Легенда о" (не служебное!)
                if 'service_words' in group_names:
                    try:
                        sw_value = match.group('service_words').strip().rstrip('.').strip()
                        sw_value_lower = sw_value.lower()
                        is_real_service_word = any(
                            sw_value_lower == sw.lower() or sw_value_lower.startswith(sw.lower())
                            for sw in self.service_words
                        )
                        if not is_real_service_word:
                            continue  # "Легенда о" — не служебное, пропустить этот паттерн
                    except IndexError:
                        pass

                if series_group_name:
                    raw_series = match.group(series_group_name).strip()
                    
                    # ✅ ДОБАВЛЕНО: Отвергнуть если series это только точки
                    # Это часто бывает false-match когда многоточие в конце файла интерпретируется как разделитель
                    # Пример: "Авраменко Александр - Я не сдаюсь..." → паттерн видит ".." как "Title"
                    if raw_series and all(c == '.' for c in raw_series):
                        # Это только точки, не серия
                        series_candidate = None
                    else:
                        # Применяем соответствующую обработку
                        if 'subseries' in series_group_name or 'subsubseries' in series_group_name:
                            series_candidate = self._extract_main_series_from_multi_level(raw_series)
                        elif 'service_words' in series_group_name or '. ' in raw_series or (raw_series.split() and '-' in raw_series.split()[-1]):
                            # Structural check: if pattern expects "Series. service_words" (dot inside parens),
                            # the captured value must also contain a dot. Otherwise the filename structure
                            # doesn't match the pattern — e.g. "(весь цикл)" has no dot, so it can't be
                            # "Series. service_words" — skip this pattern.
                            if 'service_words' in series_group_name and '.' not in raw_series:
                                series_candidate = None
                                continue
                            series_candidate = self._extract_series_from_brackets(raw_series)
                        else:
                            series_candidate = raw_series
                
                if not series_candidate and '(' in pattern_str and ')' in pattern_str:
                    series_candidate = self._apply_config_pattern(pattern_str, name_for_parsing)
                
                # 🔑 НОВОЕ: Отвергнуть если series это только цифры (скорее всего год, не серия)
                # "2021", "2020", "1999" → отвергаем, это годы
                # "Год 2021" → оставляем, это может быть название серии
                if series_candidate and series_candidate.strip().isdigit():
                    # Это только цифры - скорее всего год, не название серии!
                    series_candidate = None
                
                if not series_candidate:
                    continue
                
                # БЛОЧНОЕ СРАВНЕНИЕ: Оцениваем соответствие структур
                pattern_blocks = self.block_selector.analyze_pattern_blocks(pattern_str)
                block_score = self.block_selector.score_blocks(file_blocks, pattern_blocks)
                
                # Валидируем series
                is_valid = not validate or self._is_valid_series(series_candidate, skip_author_check=True)
                
                # Выбираем лучший паттерн
                if is_valid and block_score > best_score:
                    best_series = series_candidate
                    best_score = block_score
                    best_pattern = pattern_str
        
        if best_series:

            # Проверка 1: если best_series - это serve_word, не возвращаем его
            # Serve_words это служебные слова, не названия серий
            # ВАЖНО: сравниваем целое слово, не префикс!
            best_series_lower = best_series.lower().strip()
            is_service_word = False
            for sw in self.service_words:
                sw_lower = sw.lower()
                if best_series_lower == sw_lower or best_series_lower.startswith(sw_lower + ' '):
                    is_service_word = True
                    break
            
            if is_service_word:
                # Это serve_word, игнорируем этот результат
                best_series = None
            else:

                
                # Проверка 2: КРИТИЧНО - проверить blacklist даже если validate=False
                # Blacklist всегда должна проверяться, это не результат валидации
                # а фильтр для явно запрещенных слов
                # ВАЖНО: проверяем ПОЛНОЕ совпадение целого слова, не подстроку!
                # "СИ" не должна совпадать с "Сид" - это разные слова
                is_blacklisted = False
                for bl_word in self.filename_blacklist:
                    bl_word_lower = bl_word.lower().strip()
                    
                    # Проверяем только полные совпадения целого слова:
                    # 1. Полное совпадение: "Тетралогия" == "Тетралогия"
                    # 2. Слово в начале: "Тетралогия и еще" → "Тетралогия" match
                    # 3. Слово в конце: "что-то Тетралогия" → "Тетралогия" match
                    # 4. Слово в середине: "то Тетралогия то" → "Тетралогия" match
                    # НО НЕ: "СИ" не совпадает с "Сид" (это не целое слово)
                    
                    if (best_series_lower == bl_word_lower or
                        best_series_lower.startswith(bl_word_lower + ' ') or
                        best_series_lower.endswith(' ' + bl_word_lower) or
                        ' ' + bl_word_lower + ' ' in ' ' + best_series_lower + ' '):
                        is_blacklisted = True
                        break
                
                if is_blacklisted:
                    # Это запрещенное слово, игнорируем
                    best_series = None
                else:
                    return best_series
        
        # 🔑 ВАЖНО: Если паттерн явно БЕЗ Series - не применяем fallback правила скобок/точки!
        # НО: если в конце имени файла явный числовой суффикс (N или N-M), это признак серии —
        # Rule 3B/4 должны попробовать его найти независимо от паттерна.
        # Числовой суффикс: в конце строки ИЛИ в середине перед ". Title"
        # "Серия 2. Заголовок" → тоже признак серийности
        _has_numeric_suffix = bool(
            re.search(r'\s+\d+(?:[-–—]\d+)?\s*$', name_for_parsing) or
            re.search(r'\s+\d+\.\s+\S', name_for_parsing)
        )
        if pattern_found_without_series and not _has_numeric_suffix:
            # Паттерн явно БЕЗ серии и нет числового суффикса — возвращаем пусто или metadata
            if metadata_series:
                return metadata_series if validate else ""
            return ""
        
        # Правило 1: [Серия] в квадратных скобках в начале
        # Из паттернов конфига ищем примеры с [...]
        match = re.search(r'^\[([^\[\]]+)\]', name_for_parsing)
        if match:
            series = match.group(1).strip()
            if not validate or self._is_valid_series(series):
                return series
        
        # Правило 2: Серия в скобках в КОНЦЕ 
        # Из паттернов конфига: "Author - Title (Series. service_words)"
        # Ищем скобку в конце, может быть с сервис-словами перед ней
        if '(' in name_for_parsing and ')' in name_for_parsing:
            # Ищем ПЕРВУЮ пару скобок (не последнюю!) - используем lookahead
            # При двойных скобках (Series) (Year) нужно взять (Series), не (Year)
            match = re.search(r'\(([^)]+)\)(?:\s*\(|\s*$)', name_for_parsing)
            if match:
                content_in_brackets = match.group(1).strip()
                
                # 🔑 КРИТИЧНО: Проверить если это ТОЛЬКО одно слово
                # Скобки с одним словом это обычно подтитулы или метаинформация: (Наследник), (Король), (СИ)
                # Это НЕ основные названия серий - серии это обычно многословные: "Солдат Удачи", "Боевая Фантастика"
                # Исключение: если одно слово явно часть паттерна с точками/запятыми - обработать
                is_single_word_brackets = ' ' not in content_in_brackets.strip() and '.' not in content_in_brackets.strip()
                
                if is_single_word_brackets:
                    # Это одно слово в скобках - вероятно подтитул, не серия
                    # Пропускаем это правило
                    pass  # ← Не извлекаем "Наследник", переходим к следующему правилу
                else:
                    # Hard check: if all words in brackets are SW or qualifiers → pure annotation,
                    # not a series name. E.g. "(весь цикл)", "(вся трилогия)", "(Дилогия)"
                    SW_QUALIFIERS = {'весь', 'вся', 'все', 'полный', 'полная', 'полное',
                                     'целый', 'целая', 'целое', 'complete', 'omnibus'}
                    bracket_words = content_in_brackets.lower().split()
                    is_pure_annotation = all(
                        w in self.service_words or w in SW_QUALIFIERS or w.isdigit()
                        for w in bracket_words
                    )
                    if is_pure_annotation:
                        pass  # "(весь цикл)", "(Трилогия)" — аннотация, не серия
                    else:
                        # Это многословная комбинация в скобках - может быть серия
                        potential_series = self._extract_series_from_brackets(content_in_brackets)
                        
                        # 🔑 ПРОВЕРКА: это не должна быть фамилия автора или список авторов!
                        looks_like_author = False
                        if ',' in potential_series:
                            # Содержит запятую - это список авторов, не серия
                            looks_like_author = True
                        elif '.' in potential_series:
                            # Содержит точку - для русских имён это часто инициал+фамилия
                            # "А.Михайловский" → это явно инициал в скобках
                            looks_like_author = True
                        
                        if not looks_like_author and (not validate or self._is_valid_series(potential_series)):
                            return potential_series
        
        # Правило 3: Серия. Название (точка как разделитель в начале)
        # Из паттернов конфига: "Series. Title" и "Author - Series.Title"
        # ВАЖНО: Не захватываем простые слова (обычно фамилии) перед точкой
        # "Белoус. Последний шанс" - "Белоус" это фамилия, не серия!
        # И не захватываем "Author - Series" паттерны - они обработаны config pattern
        # "Борисов Олег - Туман 1. Золото" должен дать "Туман", не "Борисов Олег - Туман"
        if '. ' in name_for_parsing:
            potential_series = name_for_parsing.split('. ')[0].strip()
            
            # Если содержит " - ", это скорее всего "Author - Series" паттерн
            if ' - ' in potential_series:
                pass  # Skip: config pattern was first priority, don't fall back to Rule 3
            else:
                _ps_words = potential_series.split()
                # Одно слово → вероятно фамилия автора
                # Заканчивается на одну заглавную букву → формат "Фамилия И." (инициал) = автор
                # Пример: "Кларк Ф" (Ф — инициал) или "Белоус" (одно слово)
                # Содержит аббревиатурный паттерн → формат инициалов "Сэнсом К.Дж" = автор
                _is_author_pattern = (
                    len(_ps_words) <= 1 or
                    (len(_ps_words[-1]) == 1 and _ps_words[-1][0].isupper()) or
                    bool(re.search(r'[А-ЯA-Z]\.[А-Яа-яA-Za-z]', potential_series))
                )
                if _is_author_pattern:
                    pass  # Likely author name format, not a series
                elif _has_numeric_suffix:
                    pass  # Пропускаем, Rule 3B обработает корректнее
                elif not validate or self._is_valid_series(potential_series):
                    return potential_series
        
        # Правило 3B: Author. Series N (без второго элемента после точки)
        # "Курилкин. Охотник 1" → "Охотник"
        # "Яманов. Бесноватый Цесаревич I" → "Бесноватый Цесаревич"
        # Структура: OneWord. MultipleWords NUM где NUM это арабские или римские цифры
        if '. ' in name_for_parsing:
            parts = name_for_parsing.split('. ', 1)
            if len(parts) == 2:
                first_part = parts[0].strip()
                second_part = parts[1].strip()
                
                # Проверяем что первая часть это вероятный автор
                # Убираем скобочные части (псевдоним/реальное имя) перед проверкой цифр:
                # "Leach23 (Михалек Дмитрий)" → "Leach23" → содержит цифры → всё равно автор
                # Нам важно чтобы СУТЬ части была авторской, а не чтобы не было цифр вообще.
                # Критерий: длина < 60 и НЕ начинается с цифры (т.е. не год/том).
                first_part_no_parens = re.sub(r'\s*\([^)]*\)', '', first_part).strip()
                looks_like_author = (
                    len(first_part) < 60 and
                    not first_part_no_parens[:1].isdigit()  # Не начинается с цифры
                )

                # Если первая часть начинается со служебного слова («Цикл», «Серия» и т.п.),
                # это НЕ автор, а «ServiceWord SeriesName».
                # Пример: «Цикл Варяг. Книги 1-5» → first_part=«Цикл Варяг» → series=«Варяг».
                if looks_like_author:
                    _fp_lower = first_part_no_parens.lower()
                    for _sw in self.service_words:
                        if _sw and ' ' not in _sw and _fp_lower.startswith(_sw.lower() + ' '):
                            _series_from_sw = first_part[len(_sw):].strip()
                            if _series_from_sw and (not validate or self._is_valid_series(_series_from_sw)):
                                return _series_from_sw
                            looks_like_author = False
                            break

                if looks_like_author:
                    # Проверяем диапазоны: "Совок 1-5", "Попаданец в Дракона 1-8" → True
                    series_match = re.match(r'^(.+?)\s+\d{1,2}[-–—]\d{1,2}\s*$', second_part)
                    # Проверяем арабские цифры 1–2 знака: "Охотник 1" → True
                    # 3+ цифры (888) — номер дела/произведения, не том серии.
                    if not series_match:
                        series_match = re.match(r'^(.+?)\s+\d{1,2}\s*$', second_part)
                    # Если нет арабских, проверяем римские цифры: "Бесноватый Цесаревич I" → True
                    if not series_match:
                        series_match = re.match(r'^(.+?)\s+[IVX]+\s*$', second_part)

                    if series_match:
                        potential_series = series_match.group(1).strip()
                        if not validate or self._is_valid_series(potential_series):
                            return potential_series
        
        # Правило 4: Author - Series N или Author - Series N-M (без точки после номера)
        # "Атаманов Михаил - Задача выжить 1" → "Задача выжить"
        # "Земляной Андрей - Один на миллион 1-3" → "Один на миллион"
        if ' - ' in name_for_parsing:
            match = re.match(r'^(.+?)\s*-\s*(.+?)\s+(?:\d+[-–—]\d+|\d{1,2})\s*$', name_for_parsing)
            if match:
                potential_series = match.group(2).strip()
                # Убедимся что это не автор (не похоже на имя)
                if not validate or self._is_valid_series(potential_series):
                    return potential_series
        
        # Правило 4B: Author - Series N. Title (число в середине, за ним точка и заголовок)
        # "Тидхар Леви - Центральная станция 2. Неом" → "Центральная станция"
        # Отличие от Правила 4: здесь после номера идёт '. Title', а не конец строки
        if ' - ' in name_for_parsing:
            match = re.match(
                r'^(.+?)\s*-\s*(.+?)\s+(\d{1,2})\.\s+.+$',
                name_for_parsing,
            )
            if match:
                potential_series = match.group(2).strip()
                if not validate or self._is_valid_series(potential_series):
                    return potential_series

        # Правило 5: Author - Title. Subtitle (fallback для файлов без номея)
        # "Земляной Андрей - Отморозки. Другим путем" → "Отморозки"
        # Попытаемся извлечь часть после " - " и до первой точки как Title (которая может быть Series)
        if ' - ' in name_without_ext and '. ' in name_without_ext:
            match = re.match(r'^(.+?)\s*-\s*([^.]+)\.\s+(.+)$', name_without_ext)
            if match:
                title_before_dot = match.group(2).strip()
                # Это Title (потенциальная Series) если он:
                # 1. Имеет несколько слов ИЛИ 
                # 2. Это нечто более подходящее серии чем фамилия  
                if len(title_before_dot.split()) > 1 or (title_before_dot and len(title_before_dot) > 3):
                    if not validate or self._is_valid_series(title_before_dot):
                        return title_before_dot
        
        if "охотник" in name_without_ext.lower() or "Наследник" in name_without_ext:
            pass

        # Если паттерн БЕЗ Series, но числовой суффикс обнаружен и Rules 3B/4
        # ничего не вернули, возвращаем metadata или пусто
        if pattern_found_without_series:
            if metadata_series:
                return metadata_series if validate else ""
            return ""

        return ""
    
    def _apply_config_pattern(self, pattern: str, filename: str) -> str:
        """
        Применить паттерн из конфига к имени файла и извлечь Series.
        
        Паттерны используют метаметки: (Author), (Series), (Title) и т.д.
        
        Args:
            pattern: Паттерн из конфига, напр. "Author - Series (service_words)"
            filename: Имя файла без расширения
            
        Returns:
            Извлеченное имя серии или пустая строка
        """
        # Основные шаблоны
        if pattern == "Author - Series (service_words)":
            # "Садов Сергей - Горе победителям (Дилогия)"
            # "Валериев Игорь - 2. Ермак. Поход (Ермак 4-6)"
            # "Авраменко Александр - Солдат удачи 3. Взор Тьмы (Наследник)"
            # ВАЖНО: проверяем содержимое скобок чтобы различить:
            # 1) "Author - Series (service_words)" ← скобки содержат ТОЛЬКОслужебные слова/числа
            # 2) "Author - Title (Series info)" ← скобки содержат РЕАЛЬНУЮ серию
            # 
            # Проблема: "Горъ Василий - Чужая кровь (Пророчество 5-7)"
            #   Group 2 = "Чужая кровь" (title, не серия!)
            #   Скобки = "Пророчество 5-7" (реальная серия!)
            #   → НЕ должен совпадать с "Author - Series (service_words)"
            match = re.match(r'^(.+?)\s*-\s*([^()]+?)\s*\(([^)]*)\)', filename)
            if match:
                series_candidate = match.group(2).strip()
                brackets_content = match.group(3).strip()
                
                # Анализируем что в скобках
                service_words_lower = [sw.lower() for sw in self.service_words]
                brackets_lower = brackets_content.lower()
                is_skip_keyword = any(kw.lower() in brackets_lower for kw in self.collection_keywords)
                
                is_pure_service_word = any(
                    brackets_lower.startswith(sw) or brackets_lower == sw
                    for sw in service_words_lower
                )
                
                is_numeric_range = bool(re.match(r'^\d+[-–—]\d+$', brackets_content))
                
                # НОВОЕ: проверяем если в скобках есть текст + числа (смешанный формат)
                # например "Пророчество 5-7" или "Ермак 4-6"
                # Это означает что скобки содержат РЕАЛЬНУЮ СЕРИЮ, а не служебные слова!
                # В этом случае паттерн "Author - Series (service_words)" НЕ СОВПАДАЕТ
                # - скорее всего это "Author - Title (Series)" паттерн
                has_text_and_numbers = bool(re.search(r'[а-яё\w]+\s+\d', brackets_lower)) or \
                                       bool(re.search(r'\d+\s+[а-яё\w]+', brackets_lower))
                
                # Если скобки содержат реальную серию (текст + числа), НЕ совпадаем
                if has_text_and_numbers:
                    # это не паттерн "Series (service_words)", это "Title (Series info)"
                    # Пусть обработает другой паттерн
                    return ""
                
                # Если скобки содержат ТОЛЬКОслужебное слово, число или skip-keyword → это не серия!
                if is_pure_service_word or is_numeric_range or is_skip_keyword:
                    # "Эпоха перемен (Трилогия)" → нет информации о серии в файле
                    # Нужно вернуть пусто и дать возможность fallback на metadata
                    return ""
                
                # Иначе это реальная серия в скобках (случай вроде "Авраменко - Солдат удачи (Наследник)")
                series = series_candidate
                # Удаляем префикс книги: "1. ", "2. ", "3. " и т.д.
                series = re.sub(r'^\s*\d+\s*[.,]\s*', '', series).strip()
                # Также удаляем том номер и название внутри серии: "Солдат удачи 3. Взор Тьмы" → "Солдат удачи"
                # НЕ трогать версии вида "2.0": (?!\.\d) защищает десятичные числа
                series = re.sub(r'\s+\d+(?!\.\d)[\s\.\:].+$', '', series).strip()
                # Если результат содержит '. ' — берём только часть до точки
                if '. ' in series:
                    before_dot = series.split('. ')[0].strip()
                    series = before_dot
                return series
        
        elif pattern == "Author - Title (Series. service_words)":
            # "Авраменко Александр - Солдат удачи (Солдат удачи. Тетралогия)"
            # Нужно извлечь Series из скобок
            match = re.match(r'^(.+?)\s*-\s*(.+?)\s*\(\s*([^)]+)\)', filename)
            if match:
                content_in_brackets = match.group(3).strip()
                # From "(Солдат удачи. Тетралогия)" extract "Солдат удачи"
                return self._extract_series_from_brackets(content_in_brackets)
        
        elif pattern == "Author - Series.Title":
            # "Авраменко Александр - Солдат удачи 1. Солдат удачи"
            # Извлекаем часть после " - " и до нумерованного тома (N.)
            # Улучшено: теперь захватывает несколько слов перед номером
            match = re.match(r'^(.+?)\s*-\s*(.+?)\s+\d+[\s\.\:]', filename)
            if match:
                series = match.group(2).strip()
                return series
        
        elif pattern == "Author. Series. Title":
            # "Анисимов. Вариант «Бис» 2. Год мертвой змеи"
            # Формат: Author. Series. Title
            # ВАЖНО: Не применяем если в filename есть скобки - это дело pattern "Author. Title (Series)"
            # "Кумин. Битва за звёзды (Исход. Тетралогия)" не должен обрабатываться так!
            # Наличие скобок означает что реюлярная серия в скобках, а не "Author. Series. Title"
            if '(' in filename:
                # Есть скобки - скорее всего "Author. Title (Series)" паттерн, пропускаем
                return ""
            
            parts = filename.split('. ')
            if len(parts) >= 3:
                # parts[1] должна быть Series
                series = parts[1].strip()
                # Удаляем trailing число (том/выпуск) из серии
                # "Негоциант 2" -> "Негоциант"
                series = re.sub(r'\s+\d+\s*$', '', series).strip()
                return series
        
        elif pattern == "Author, Author - Title (Series. service_words)":
            # "Земляной Андрей, Орлов Борис - Академик (Странник 4-5)"
            # Извлекаем Series из скобок
            match = re.search(r'\(\s*([^)]+)\)', filename)
            if match:
                content_in_brackets = match.group(1).strip()
                return self._extract_series_from_brackets(content_in_brackets)
        
        elif pattern == "Author. Title (Series. service_words)":
            # "Демченко. Хольмградские истории (Хольмградские истории. Трилогия)"
            # Извлекаем Series из скобок
            match = re.search(r'\(\s*([^)]+)\)', filename)
            if match:
                content_in_brackets = match.group(1).strip()
                return self._extract_series_from_brackets(content_in_brackets)
        
        elif pattern == "Author - Title (Series service_words)":
            # "Валериев Игорь - 2. Ермак. Поход (Ермак 4-6)"
            # Similar to "Author - Title (Series. service_words)" but without dot
            # Content in brackets: "Ермак 4-6" (space before number)
            match = re.match(r'^(.+?)\s*-\s*(.+?)\s*\(\s*([^)]+)\)', filename)
            if match:
                content_in_brackets = match.group(3).strip()
                return self._extract_series_from_brackets(content_in_brackets)
        
        elif pattern == "Author, Author. Title (Series)":
            # "Зурков, Черепнев. Бешеный прапорщик (Бешеный прапорщик 1-3)"
            # Извлекаем Series из скобок
            match = re.search(r'\(\s*([^)]+)\)', filename)
            if match:
                content_in_brackets = match.group(1).strip()
                return self._extract_series_from_brackets(content_in_brackets)
        
        elif pattern == "Author - Series service_words. Title":
            # "Игнатов Михаил - Путь 10. Защитник. Второй пояс (СИ)"
            # Извлекаем Series между " - " и номером
            # Паттерн: Author - Series Number. Title
            # ВАЖНО: Требуем пробелы ДО дефиса чтобы не совпасть с дефисом в серии
            # "Сердитый, Бирюков. Человек-саламандра 1" не должен совпасть
            # (здесь дефис без пробела перед ним)
            match = re.match(r'^(.+?)\s+-\s+(.+?)\s+\d+\.\s+', filename)
            if match:
                series = match.group(2).strip()
                return series
        
        return ""
    
    def _extract_main_series_from_multi_level(self, content: str) -> str:
        """
        Извлечь иерархию сери из многоуровневой группы.
        
        Обрабатывает паттерны вроде:
        - "Сид 1. Принцип талиона 1. Геката 1" → "Сид\Принцип талиона\Геката"
        - "Сид 1. Принцип талиона 1" → "Сид\Принцип талиона"
        - "Сид 1" → "Сид"
        - "Мир Вечного 2. Вечный. Тетралогия" → "Мир Вечного\Вечный"
        - "След Фафнира. Дилогия + внецикл. роман" → "След Фафнира"
        - "Дракон 1-3" → "Дракон"
        
        Args:
            content: Содержимое группы (может быть Series. SubSeries. SubSubSeries или Series. ServiceWords)
            
        Returns:
            Иерархия серий разделенная backslash (без номеров и без служебных слов)
        """
        if not content:
            return ""
        
        # Служебные слова, которые обозначают конец иерархии серий
        # Используем \b для границ слов, чтобы не путать "Серия Альфа" со служебным "Серия"
        service_words_pattern = r'\b(?:Дилогия|Трилогия|Тетралогия|Пентагония|внецикл|дополнение|прелюдия|эпилог)\b'
        
        # Если контент уже содержит '\' (результат BlockLevelPatternMatcher с SubSeries),
        # разбиваем по '\', а каждый компонент дополнительно чистим от номеров через '. '.
        # Иначе разбиваем по '. ' как обычно.
        if '\\' in content:
            raw_parts = []
            for chunk in content.split('\\'):
                # Внутри компонента может быть ". " — берём только первую часть (до точки)
                sub = chunk.split('. ')[0].strip()
                if sub:
                    raw_parts.append(sub)
            parts = raw_parts
        else:
            # Разделяем по точке+пробел (это разделитель уровней или служебной информации)
            parts = content.split('. ')
        
        if not parts:
            return ""
        
        # Обрабатываем каждую часть
        # "Сид 1" → "Сид"
        # "Принцип талиона 1" → "Принцип талиона"
        # Но ОСТАНАВЛИВАЕМСЯ, когда встречаем служебное слово
        hierarchy = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Проверяем, содержит ли эта часть служебные слова
            # Если содержит - это КОНЕЦ иерархии, не добавляем дальше
            if re.search(service_words_pattern, part, flags=re.IGNORECASE):
                # Проверяем: служебное слово — это ВСЯ часть (например отдельная «Тетралогия»)
                # или оно встроено в название серии («Саксонская трилогия 2»)?
                _part_without_sw = re.sub(service_words_pattern, '', part, flags=re.IGNORECASE).strip()
                _part_without_sw = re.sub(r'[\d\-\+\,\s]+$', '', _part_without_sw).strip()
                if not _part_without_sw or len(_part_without_sw) <= 2:
                    # Служебное слово составляет всю или почти всю часть — стоп-метка, прекращаем
                    break
                # Служебное слово встроено в название (например «Саксонская трилогия 2») —
                # убираем только хвостовой номер и добавляем часть целиком
                series_name = re.sub(r'\s*[\d\-\,\–]+\s*$', '', part).strip()
                if series_name:
                    hierarchy.append(series_name)
                break
            
            # Ищем только последовательность чисел/диапазонов в конце
            # Удаляем числа, но НЕ служебные слова (они должны остановить процесс)
            series_name = re.sub(
                r'\s*[\d\-\,\–]+\s*$',  # Только числа/диапазоны в конце
                '',
                part
            ).strip()
            
            # Дополнительно удаляем "№ N" или одиночный "№" в конце
            # Только 1–2 цифры: №888 — это номер дела/произведения, не том серии.
            series_name = re.sub(r'\s*№\s*\d{1,2}\s*$', '', series_name).strip()
            series_name = re.sub(r'\s*№\s*$', '', series_name).strip()
            
            # Однобуквенные компоненты — это части аббревиатуры (напр. «О. Р. З.»), а не уровни серии.
            # Сбрасываем всю иерархию, чтобы не собирать мусор вида «Р\или Сказ...»
            if len(series_name) <= 1:
                return ""  # Аббревиатура обнаружена, серия не выделена

            if series_name:  # Добавляем только непустые части
                hierarchy.append(series_name)
        
        # Объединяем через backslash
        return '\\'.join(hierarchy) if hierarchy else ""

    def _extract_series_from_brackets(self, content: str) -> str:
        """
        Извлечь имя серии из содержимого скобок.
        Обрабатывает:
        - "Серия. service_words" → "Серия"
        - "Серия N-M" → "Серия"
        - "Романы из цикла «Серия»" → "Серия"
        
        Args:
            content: Содержимое скобок без скобок
            
        Returns:
            Извлеченное имя серии
        """
        # Сбрасываем флаг иерархической серии
        self._last_was_hierarchical = False
        
        # IMMEDIATE CHECK: Если содержимое скобок содержит запятую - это вероятно список авторов, не серия
        if ',' in content:
            # Это список (авторов, соавторов и т.д.), не серия
            return ""
        
        # Сначала попробуем паттерн "из цикла" или "из серии"
        # "Романы из цикла «Отрок»" → "Отрок"
        cycle_match = re.search(r'из\s+(?:цикла|серии)\s+(.+)', content, re.IGNORECASE)
        if cycle_match:
            series_candidate = cycle_match.group(1).strip()
            # Удаляем внешние кавычки
            open_count = series_candidate.count('«')
            close_count = series_candidate.count('»')
            
            if (open_count > 0 and open_count == close_count and 
                series_candidate.startswith('«') and series_candidate.endswith('»')):
                series_candidate = series_candidate[1:-1]
            elif open_count > close_count and series_candidate.startswith('«'):
                series_candidate = series_candidate[1:]
                
            return series_candidate.strip()
        
        # Если есть точка - берем до неё (это Series. service_words)
        if '. ' in content:
            parts = content.split('. ')
            after_dot = parts[1].strip() if len(parts) > 1 else ''
            after_dot_lower = after_dot.lower()
            
            # Проверка служебных слов + blacklist + collection_keywords
            # "Мир Алекса Королёва. Сборник" → after_dot="сборник" → берём "Мир Алекса Королёва"
            all_check_words = self.service_words + self.filename_blacklist + self.collection_keywords
            is_service_word = any(
                after_dot_lower.startswith(sw.lower()) 
                for sw in all_check_words
            )
            
            if is_service_word:
                return parts[0].strip()
            
            # ИЕРАРХИЧЕСКАЯ СЕРИЯ: "Отрок 2. Сотник 1-3"
            # Признаки: parts[0] = "Слова Число", parts[1] = "Слова Число/Диапазон"
            # → это главная серия + подсерия → возвращаем parts[0] КАК ЕСТЬ (с номером тома!)
            # Отличие от обычного случая: after_dot начинается с заглавной буквы (имя подсерии)
            # и содержит число или диапазон
            part0 = parts[0].strip()
            # parts[0] заканчивается числом: "Отрок 2", "Серия 5"
            part0_has_trailing_num = bool(re.search(r'\s+\d+\s*$', part0))
            # parts[1] начинается с заглавной буквы и содержит число: "Сотник 1-3", "Книга 2"
            after_dot_is_subseries = (
                after_dot and
                after_dot[0].isupper() and
                bool(re.search(r'\d', after_dot))
            )
            
            # Доп. признак: последняя часть — служебное слово (Тетралогия, Трилогия…)
            # «Мир Вечного 2. Вечный. Тетралогия» → parts[-1]="Тетралогия" → service word
            all_check_words_lower = [w.lower() for w in self.service_words + self.filename_blacklist + self.collection_keywords]
            last_part_is_service = (
                len(parts) > 2 and
                any(parts[-1].strip().lower().startswith(w) for w in all_check_words_lower)
            )

            if part0_has_trailing_num and (after_dot_is_subseries or last_part_is_service):
                # Иерархическая серия: возвращаем главную серию С номером тома
                # "Отрок 2. Сотник 1-3" → "Отрок 2"
                # "Мир Вечного 2. Вечный. Тетралогия" → "Мир Вечного 2"
                # Устанавливаем флаг чтобы _clean_series_name не убирала trailing number
                self._last_was_hierarchical = True
                return part0
        
        # НОВОЕ: Если есть service word в конце БЕЗ точки
        # "Я иду искать! Тетралогия" -> "Я иду искать!"
        # "Демон 1-3" -> "Демон"
        # service_markers используются для проверки последнего слова
        service_markers = self.service_words
        
        # Ищём service words в конце контента (отделённые пробелом или в начале слова)
        # "Я иду искать! Тетралогия" -> parts = ["Я иду искать!", "Тетралогия"]
        # "Демон 1-3" -> parts = ["Демон", "1-3"]
        words = content.split()
        if len(words) > 1:
            last_word_lower = words[-1].lower()
            
            # Проверяем, является ли последнее слово service word
            is_last_service_word = any(
                last_word_lower.startswith(sw.lower()) or 
                last_word_lower == sw.lower()
                for sw in self.service_words
            )
            
            # Или это диапазон номеров
            is_numeric_range = bool(re.match(r'^\d+[-–—]\d+$', words[-1]))
            
            if is_last_service_word or is_numeric_range:
                # Возьмём все слова кроме последнего
                series_candidate = ' '.join(words[:-1]).strip()
                if series_candidate:
                    # Защита: не стрипаем сервисное слово если оно часть названия.
                    # «Дублинская серия» → без «серия» остаётся «Дублинская» (1 слово) → сохраняем.
                    # «Я иду искать! Тетралогия» → без «Тетралогия» остаётся 3 слова → стрипаем.
                    # Применяем только к сервисным словам-дескрипторам (не к числовым диапазонам).
                    if is_last_service_word and not is_numeric_range:
                        _sig_remaining = sum(
                            1 for w in series_candidate.split()
                            if sum(c.isalpha() for c in w) >= 4
                        )
                        if _sig_remaining < 2:
                            # Слишком мало значимых слов — «серия» часть названия, сохраняем
                            pass  # не возвращаем, падаем дальше
                        else:
                            return series_candidate
                    else:
                        return series_candidate
        
        # Если есть числовой диапазон (1-3, 4-6), берем до него
        series_candidate = re.sub(r'\s*[\d\-]+\s*$', '', content).strip()
        
        return series_candidate if series_candidate else ""
    
    def _remove_blacklist_words(self, text: str) -> str:
        """
        Удалить только слова из blacklist из текста, оставить остальное.
        
        Логика:
        - "Господин следователь (СИ)" → "(СИ)" в blacklist → "Господин следователь"
        - "Последний солдат СССР" → "СССР" в конце + есть слова перед ним → оставить как есть
        - Но если ТОЛЬКО blacklist-word → вернуть пусто
        
        Args:
            text: Исходный текст
            
        Returns:
            Текст с удаленными blacklist-словами, или пусто если ничего не осталось
        """
        if not text or not self.filename_blacklist:
            return text

        text_lower = text.lower()

        # Если текст начинается с collection_keyword (например "Сборник авторов"),
        # весь блок — маркер коллекции, не название серии — отвергаем целиком.
        for kw in (self.collection_keywords or []):
            if text_lower.startswith(kw.lower()):
                return ""

        original_text = text

        # Проходим по каждому слову в blacklist
        for bl_word in self.filename_blacklist:
            bl_word_lower = bl_word.lower().strip()
            if not bl_word_lower:
                continue
            
            # Ищем это слово как целое слово (не substring)
            # Паттерн: слово с границами (пробелы, скобки, пунктуация)
            import re
            pattern = r'(?:^|\s|\(|-)' + re.escape(bl_word_lower) + r'(?:\s|\)|$|[,.\-\!?])'

            # Перед удалением: если blacklist-слово стоит перед именами собственными
            # (все слова после него с заглавной буквы), это профессиональный префикс —
            # не удаляем. Пример: «Детектив Джейкоб Лев» — не трогаем.
            _m = re.search(pattern, original_text, flags=re.IGNORECASE)
            if _m:
                _after = original_text[_m.end():].strip()
                _alpha_after = [w for w in _after.split() if w and w[0].isalpha()]
                if _alpha_after and all(w[0].isupper() for w in _alpha_after):
                    continue  # Не удаляем: профессиональный префикс перед именами

            # Заменяем найденные вхождения на пробел (или пусто)
            original_text = re.sub(
                pattern,
                ' ',
                original_text,
                flags=re.IGNORECASE
            )
        
        # Очищаем множественные пробелы и пустые скобки
        cleaned = re.sub(r'\s+', ' ', original_text).strip()
        cleaned = re.sub(r'\(\s*\)', '', cleaned).strip()
        # Убираем висячие (несбалансированные) скобки в конце строки.
        # Пример: "Серия (СИ" → "Серия" (открытая скобка без закрытой)
        # НЕ трогаем: "Серия (Крылов)" — скобки сбалансированы
        if cleaned.count('(') != cleaned.count(')'):
            cleaned = re.sub(r'\s*[\(\)]\s*$', '', cleaned).strip()

        # Защита контекста: если BL-удаление уничтожило большую часть смысла — вернуть оригинал.
        # «Значимое слово» = ≥4 букв, не чистое число.
        # Правило: если в оригинале было ≥2 значимых слова, а в результате осталось <2 —
        # BL-слово является неотъемлемой частью названия, не жанровым тегом. Сохраняем.
        # Пример: «Попаданец XIX века» → без «Попаданец» остаётся «XIX века» (1 знач. слово)
        #         → возвращаем «Попаданец XIX века».
        # НЕ срабатывает: «Попаданец (СИ)» — оригинал имеет только 1 знач. слово.
        def _sig(t: str) -> int:
            # Считаем только буквенные символы в слове (скобки/цифры не учитываем)
            return sum(1 for w in t.split() if sum(c.isalpha() for c in w) >= 4)

        if _sig(cleaned) < 2 and _sig(text) >= 2:
            return text

        return cleaned if cleaned else ""

    def _contains_blacklist_word(self, text: str) -> bool:
        """
        Проверить, содержит ли text слово(а) из blacklist.
        
        Args:
            text: Проверяемый текст (например, название папки для series)
            
        Returns:
            True если найдено хотя бы одно blacklist слово, False иначе
        """
        if not text or not self.filename_blacklist:
            return False
        
        text_lower = text.lower()
        
        # Проходим по каждому слову в blacklist
        for bl_word in self.filename_blacklist:
            bl_word_lower = bl_word.lower().strip()
            if not bl_word_lower:
                continue
            
            # Проверяем наличие как целого слова (word boundary check)
            # Ищем в виде отдельного слова, не как substring
            # Например: "боевая фантастика" в "Боевая фантастика. Циклы" → FOUND
            #           но не "боевая" как часть слова
            
            # Используем word boundaries: \b работает для ASCII, но для кириллицы нужен свой paттерн
            import re
            pattern = r'(?:^|\W)' + re.escape(bl_word_lower) + r'(?:\W|$)'
            if re.search(pattern, text_lower):
                return True
        
        return False
    
    def _is_valid_series(self, text: str, extracted_author: str = None, skip_author_check: bool = False) -> bool:
        """
        Проверить что text выглядит как название серии, не как другое.
        Проверяет против:
        - filename_blacklist (список запрещенных слов)
        - collection_keywords (сборники, антологии)
        - service_words (том, книга, выпуск)
        - AuthorName (не похоже на имя автора) - ЗА ИСКЛЮЧЕНИЕМ случаев когда это иное слово
        
        Args:
            text: Проверяемый текст (название серии)
            extracted_author: Опционально, извлечённый из файла автор. Если passed, не отвергаем
                            series если она не совпадает с author (важно для паттернов "Author - Series")
            skip_author_check: Если True - пропускаем проверку на похожесть на автора (используется при
                            предварительной валидации без контекста об авторе)
        """
        if not text or len(text) < 2:
            return False
        
        text_lower = text.lower()
        
        # ПРОВЕРКА -1: Исключить названия литературных премий
        # Признаки: содержит слово "премия"/"award"/"prize" ИЛИ заканчивается на "– YYYY" / "- YYYY"
        # Пример: "Литературная премия «Электронная буква – 2019»"
        _award_keywords = ('премия', 'award', 'prize', 'лауреат', 'номинант')
        if any(kw in text_lower for kw in _award_keywords):
            return False
        # Год со знаком тире в конце (после снятия кавычек) — тоже признак номинации/премии
        # ИСКЛЮЧЕНИЕ: дефис БЕЗ пробела как часть составного слова (СССР-2023 — не премия).
        # Отвергаем только: пробел перед любым тире, или длинное тире (– —) без пробела.
        if re.search(r'\s[–—\-]\s*\d{4}\s*[»"\']*\s*$', text) or \
           re.search(r'[–—]\s*\d{4}\s*[»"\']*\s*$', text):
            return False

        # ПРОВЕРКА -0.5: Исключить иерархические серии где любой сегмент — одна буква.
        # Пример: "Р\или Сказ о том..." — это аббревиатура О. Р. З., а не иерархия серий.
        if '\\' in text:
            _segments = [s.strip() for s in text.split('\\')]
            if any(len(s) <= 1 for s in _segments):
                return False

        # ПРОВЕРКА 0: Исключить технические фрагменты (калибры, характеристики)
        # Примеры: ".45", ".357", ",45caliber", "9mm" - это не названия серий
        # Паттерн: начинается с точки/запятой И это только цифры + буквы для единиц
        # Или: это только цифры + буквы без полноценного названия (< 3 букв)
        
        # Случай 1: ".NN" или ",NN" (калибр оружия)
        if re.match(r'^[.,]\d+$', text_lower):
            return False
        
        # Случай 2: чистые цифры с единицами вроде "9mm", "45acp"
        # (более 2 букв после цифр - это "real words", менее 2 букв это техника)
        if re.match(r'^\d+[a-z]{1,2}$', text_lower):
            return False
        
        # Случай 3: только цифры и 1-3 символа (вроде ".45" → "45", ".357" → "357")
        # Это вероятный калибр оружия, а не серия
        # Но берем осторожно - "99" может быть реальная серия
        # Поэтому отвергаем ТОЛЬКО если это 1-2 символа (как "45", "9", "357" = 3 цифры-это OK на грани)
        if re.match(r'^\d{1,2}$', text_lower):
            return False
        
        # ПРОВЕРКА 1: filename_blacklist - запрещенные слова
        # ВАЖНО: проверяем целые слова, не substring!
        # "СИ" в blacklist относится к метатегам "(СИ)" в конце, а не к "Сид"
        # ЭКСПЦИЯ: если blacklist-word это последнее слово И перед ним есть другие слова,
        # это вероятно часть series name, а не сама папка. Пример: "Последний солдат СССР"
        # где "СССР" в blacklist, но это реальная series потому что есть реальные слова перед ней

        # ПЕРЕПРОВЕРКА ПЕРЕД ЦИКЛОМ: перечень жанров через запятую
        # Пример: "Путешествия, приключения, фантастика" — каждая часть одно слово,
        # хотя бы одна часть в blacklist → это издательская рубрика, не серия.
        if ',' in text_lower:
            _comma_parts = [p.strip() for p in text_lower.split(',')]
            # Применяем только когда каждая часть — ≤2 слова (перечень, не «X, или Y»)
            if _comma_parts and all(len(p.split()) <= 2 for p in _comma_parts if p):
                _bl_lower_set = {bl.lower() for bl in self.filename_blacklist}
                if any(p in _bl_lower_set for p in _comma_parts if p):
                    return False

        for bl_word in self.filename_blacklist:
            bl_word_lower = bl_word.lower()
            # Match bl_word as a whole word or at word boundary
            pattern = r'(?:^|\s|\(|-)' + re.escape(bl_word_lower) + r'(?:\s|\)|$)'
            if re.search(pattern, text_lower):
                # Если это ТОЛЬКО blacklist word (например "СССР" или "СССР по категориям"),
                # отвергаем. Но если есть реальные слова ПЕРЕД ним, это series.
                # Пример: "Последний солдат СССР" ← реальная series даже если СССР в blacklist
                words = text_lower.split()
                bl_word_index = None
                
                # Найдем позицию blacklist-word в списке слов
                for i, word in enumerate(words):
                    if word.lower() == bl_word_lower or bl_word_lower in word:
                        bl_word_index = i
                        break
                
                # Если blacklist-word в КОНЦЕ и есть ≥2 слова перед ним
                # (≥2, а не просто >0, чтобы исключить формат «Категория. Жанр»,
                # например «Современность. Фантастика» — только 1 слово перед blacklist-словом)
                if bl_word_index is not None and bl_word_index >= 2 and bl_word_index == len(words) - 1:
                    # Это вероятно series (реальные слова + blacklist-word в конце)
                    # Пример: "Последний солдат" + "СССР" = "Последний солдат СССР"
                    continue  # Не отвергаем
                elif bl_word_index == 0 and len(words) == 1:
                    # Это вероятно папка (ТОЛЬКО blacklist-word)
                    # Пример: "СССР"
                    return False
                elif bl_word_index == 0 and len(words) >= 3:
                    # Blacklist-слово в начале многословной фразы (≥3 слов) →
                    # жанр-префикс в названии серии, допускаем.
                    # Пример: "Попаданец в Дракона", "Детектив из прошлого"
                    continue
                else:
                    # В других случаях (blacklist-word в середине или начале):
                    # Проверяем паттерн «Профессия/Звание + Имя собственное».
                    # Если после blacklist-слова идут слова с заглавной буквы (имена),
                    # это название серии с профессией персонажа, а не жанровый тег.
                    # Пример: «Детектив Джейкоб Лев» → «Джейкоб», «Лев» — заглавные → допускаем.
                    # Пример: «детективный роман» → следующее слово строчное → отвергаем.
                    _bl_idx = bl_word_index if bl_word_index is not None else 0
                    _orig_words = text.split()
                    _words_after = [w for w in _orig_words[_bl_idx + 1:] if w and w[0].isalpha()]
                    if _words_after and all(w[0].isupper() for w in _words_after):
                        continue  # «Профессия + Имя» — допускаем как название серии
                    return False
        
        # ПРОВЕРКА 2: Исключить очевидные сборники/антологии
        # Эти фразы обычно многословные (сборник, антология, коллекция)
        # поэтому substring check более безопасен
        for keyword in self.collection_keywords:
            if keyword.lower() in text_lower:
                return False
        
        # ПРОВЕРКА 3: Исключить сервис-слова (том, книга, выпуск)
        # ВАЖНО: Отвергаем ТОЛЬКО если это просто service_word или service_word + число!
        # НЕ отвергаем легитимные названия серий типа "Цикл Скорпиона" или "Серия Огня"!
        # 
        # Примеры что отвергаем ("том 1", "выпуск", "книга 3", "цикл")
        # Примеры что СОХРАНЯЕМ ("Цикл Скорпиона", "Том Риддл", "Серия Огня")
        for service_word in self.service_words:
            service_word_lower = service_word.lower()
            words = text_lower.split()
            
            if not words:
                continue
            
            first_word = words[0]
            
            # 1. Отвергаем если текст это РОВНО service_word ("том", "выпуск")
            if first_word == service_word_lower and len(words) == 1:
                return False
            
            # 2. Отвергаем если это service_word + число ("том 1", "выпуск 5", "книга 2")
            if first_word == service_word_lower and len(words) >= 2:
                second_word = words[1]
                # Проверяем что второе слово это число, римская цифра или "и" (для "и т.д.")
                if re.match(r'^\d+$', second_word) or \
                   re.match(r'^[IVX]+$', second_word, re.IGNORECASE) or \
                   second_word in ['и', '-']:
                    return False
            
            # 3. Специальная проверка для однобуквенных сокращений типа "т."
            # Отвергаем "т. 1" или "т. " но не "т.сервис-слово-другое"
            if len(service_word_lower) == 1:
                if text_lower.startswith(service_word_lower + '.'):
                    # Это может быть "т. 1" или просто "т."
                    remainder = text_lower[2:].strip()
                    if not remainder or re.match(r'^\d+', remainder):
                        return False
        
        # ПРОВЕРКА 4: Убедиться что это НЕ похоже на автора!
        # Если skip_author_check=True - пропускаем эту проверку
        # (используется при предварительной валидации без контекста об авторе)
        if not skip_author_check:
            # КРИТИЧНО: если extracted_author передан, мы уже знаем что это не автор
            # Например в паттерне "Author - Series" мы извлекли series из second part
            # и у нас есть информация об авторе - нет смысла отвергать series
            # только потому что она выглядит как фамилия (может быть совпадение)
            try:
                author = AuthorName(text)
                if author.is_valid:
                    # Это похоже на валийного автора... но есть ли контекст?
                    if extracted_author:
                        # У нас есть информация об извлечённом авторе
                        # Пропускаем проверку на автора если text отличается от автора
                        # "Охотник" != "Янковский Дмитрий" → это не автор, это серия
                        try:
                            extracted_author_obj = AuthorName(extracted_author)
                            extracted_author_normalized = extracted_author_obj.normalized or extracted_author_obj.raw_name
                            
                            # Нормализуем text как если бы это был автор
                            text_as_author_obj = AuthorName(text)
                            text_as_author_normalized = text_as_author_obj.normalized or text_as_author_obj.raw_name
                            
                            # Если normalized версии совпадают - это один и тот же автор
                            if extracted_author_normalized != text_as_author_normalized:
                                # Это РАЗНЫЕ авторы/имена → text это серия, не автор
                                return True
                        except Exception:
                            # Если нормализация не сработала - пытаемся простое сравнение
                            if extracted_author.lower() != text.lower():
                                return True
                    
                    # Если контекста нет или совпадает - отвергаем как автора
                    return False
            except Exception:
                pass  # Если парсинг не сработал - это вероятно серия
        
        return True
    
    def _extract_series_from_metadata(self, metadata_series: str) -> str:
        """
        Применить паттерны из series_patterns_in_metadata для очистки metadata серии.
        
        Паттерн "Series. Title" означает: извлечь всё перед первой точкой.
        Пример: "Рукопись Памяти-3. Забытое грядущее" → "Рукопись Памяти-3"
        
        Args:
            metadata_series:值ание серии из метаданных
        
        Returns:
            Очищенное название серии
        """
        if not metadata_series or not self.metadata_patterns:
            return metadata_series
        
        text = metadata_series.strip()
        
        # Применяем каждый паттерн
        for pattern_obj in self.metadata_patterns:
            pattern = pattern_obj.get('pattern', '')
            
            if pattern == "Series. Title":
                # "Серия. Название" → "Серия"
                # Извлекаем всё перед первой точкой + пробелом
                if '. ' in text:
                    series = text.split('. ')[0].strip()
                    if series:
                        return series
        
        return text
    
    def _clean_series_name(self, text: str, keep_trailing_number: bool = False) -> str:
        """
        Очистить название серии от паразитных символов и информации:
        - Номера томов: "Солдат удачи 1", "Солдат удачи 2. Название"
        - Названия книг: "Серия 1. Название книги"
        - Служебные слова: "Трилогия", "Тетралогия"
        
        Примеры:
            "Солдат удачи 3. Взор Тьмы" → "Солдат удачи"
            "Вариант «Бис» 1" → "Вариант «Бис»"
            "Война в Космосе 5" → "Война в Космосе"
            "Странник (Серия 3)" → "Странник" (скобки обработаны)
        
        Args:
            text: Исходный текст
        
        Returns:
            Очищенное название серии
        """
        if not text:
            return text
        
        original = text.strip()
        
        # Правило -2: Удалить обрамляющие кавычки-ёлочки «» — они обозначают серию в имени файла,
        # но не должны быть частью итогового названия: «СССР-2023» → СССР-2023
        text = re.sub(r'^«\s*', '', text).strip()
        text = re.sub(r'\s*»$', '', text).strip()
        if not text:
            return original
        
        # Правило -1: Удалить ведущий дефис/тире (артефакт разбиения по ". " в паттернах "Author - Series")
        # Пример: "- Сказания Тремейна" → "Сказания Тремейна"
        text = re.sub(r'^[-–—]\s*', '', text).strip()
        # Удалить хвостовой дефис/тире (артефакт когда " - N" разобрался как "series -" + "N")
        # Пример: "Режиссер Советского Союза -" → "Режиссер Советского Союза"
        text = re.sub(r'\s*[-–—]+\s*$', '', text).strip()
        if not text:
            return ""
        
        # Правило 0: Удалить скобки с информацией в конце
        # "(к-во, год, описание)" → убрать
        text = re.sub(r'\s*\([^)]*\)\s*$', '', text).strip()
        
        # Правило 1: Удалить всё после "номер. слова" (объективное)
        # Паттерн: "слова цифра. слова" → берем только "слова"
        # НЕ трогать: "Цивилизация 2.0 1. Выбор пути" — "2" здесь часть версии "2.0"
        match = re.match(r'^(.+?)\s+\d+(?!\.\d)[\.\:]\s+.+$', text)
        if match:
            text = match.group(1).strip()

        if not keep_trailing_number:
            # Правило 2: Удалить номер тома/выпуска в конце
            # Паттерны: "Серия 1", "Серия 2", "Серия (том) 3", и т.д.
            # Удаляем: пробел + одна или две цифры + конец
            # НЕ трогать: "Цивилизация 2.0" — "0" идёт после ".", не отдельное число
            text = re.sub(r'(?<!\.)\s+\d{1,2}\s*$', '', text).strip()
            
            # Правило 2B: Удалить "№ N" или просто "№" в конце
            # "Смертельный аромат № 5" → "Смертельный аромат"
            # "Смертельный аромат №5" → "Смертельный аромат"
            # "Смертельный аромат №" → "Смертельный аромат"
            # Только 1–2 цифры: №888 — это номер дела/произведения, не том серии.
            text = re.sub(r'\s*№\s*\d{1,2}\s*$', '', text).strip()
            text = re.sub(r'\s*№\s*$', '', text).strip()
            
            # Правило 3: Удалить всё после "номер " (менее строгое)
            # Паттерн: "слова цифра слова" → берем только "слова"
            # Исключение: год (1900–2099) — часть названия ("Боевой 1918 год")
            match = re.match(r'^(.+?)\s+(\d+)\s+.+$', text)
            if match and not (1900 <= int(match.group(2)) <= 2099):
                text = match.group(1).strip()
        
        # Правило 4: Удалить служебные слова в скобках
        # "Серия (Трилогия)" → "Серия"
        text = re.sub(r'\s*\([^)]+\)\s*$', '', text).strip()
        
        # Правило 5: Удалить служебные слова в конце (простые, без скобок)
        # После серии часто идут: "- Трилогия", "- Цикл", и т.д.
        # Guard: если после удаления остаётся одно прилагательное — не удаляем,
        # т.к. слово + служебный суффикс образует имя собственное («Саксонская трилогия»).
        _ADJECTIVE_ENDINGS = (
            'ский', 'ская', 'ское', 'ских', 'ским', 'ской',
            'цкий', 'цкая', 'цкое', 'цкой',
            'ный', 'ная', 'ное', 'ной', 'ных', 'ним',
            'дний', 'дняя', 'днее', 'зний', 'зняя', 'знее',
        )
        for service_word in self.service_words:
            # ВАЖНО: Используем \b для word boundary чтобы не удалять буквы из конца слова
            # Пример: НЕ удаляем "т" из «Адъютант» даже если «т» в service_words
            pattern = r'\s*[\-–—]?\s*\b' + re.escape(service_word) + r'\b\s*$'
            _candidate = re.sub(pattern, '', text, flags=re.IGNORECASE).strip()
            if _candidate == text:
                continue  # слово не нашлось
            # Не удаляем если результат — одно прилагательное («Саксонская», «Кавказский»)
            _cnd_words = _candidate.split()
            if len(_cnd_words) == 1 and _cnd_words[0].lower().replace('ё', 'е').endswith(_ADJECTIVE_ENDINGS):
                continue
            text = _candidate
        
        # Правило 6: Повторно удалить обрамляющие кавычки-ёлочки после всех остальных правил
        # Случай: «СССР-2023» 2 → strip « → СССР-2023» 2 → strip number → СССР-2023» → strip »
        text = re.sub(r'^«\s*', '', text).strip()
        text = re.sub(r'\s*»$', '', text).strip()

        # Правило 7: Удалить завершающую одиночную точку
        # "Араб." → "Араб"  (метатег в FB2 может содержать точку в конце)
        # НЕ удалять троеточие: "Муля, не нервируй..." → без изменений
        if text.endswith('.') and not text.endswith('..') and not text.endswith('\u2026'):
            text = text[:-1].strip()
        
        return text if text else original

    
    def _matches_with_tolerance(self, text1: str, text2: str, tolerance: float = 0.85) -> bool:
        """
        Проверить что два текста совпадают с учетом опечаток, разницы в регистре и пунктуации.
        
        Args:
            text1: Первый текст
            text2: Второй текст
            tolerance: Минимальная степень совпадения (0.0-1.0)
        
        Returns:
            True если тексты совпадают с достаточной точностью
        """
        # Очистить от пунктуации и привести к нижнему регистру
        clean1 = re.sub(r'[^\w\s]', '', text1).lower().strip()
        clean2 = re.sub(r'[^\w\s]', '', text2).lower().strip()
        
        if not clean1 or not clean2:
            return False
        
        # Точное совпадение
        if clean1 == clean2:
            return True
        
        # Проверить что одна строка содержит другую полностью
        if clean1 in clean2 or clean2 in clean1:
            return True
        
        # Проверить используя Levenshtein distance (приблизительное совпадение)
        # Если совпадает > tolerance % символов
        max_len = max(len(clean1), len(clean2))
        if max_len == 0:
            return False
        
        # Простой подсчет: совпадающие символы / длина более длинной строки
        matches = sum(1 for a, b in zip(clean1, clean2) if a == b)
        similarity = matches / max_len
        
        return similarity >= tolerance
    
    def _is_hierarchical_series(self, text: str) -> bool:
        """
        Проверить является ли текст иерархической серией вида "MainSeries N" 
        где N — номер тома в главной серии (не просто trailing number для удаления).
        
        Признак: текст заканчивается числом, и это число — часть имени серии,
        потому что оригинальный контент скобок был "MainSeries N. SubSeries M-K".
        
        Используется чтобы не убирать trailing number в _clean_series_name.
        
        Примеры:
            "Отрок 2" → True (было "Отрок 2. Сотник 1-3")
            "Солдат удачи 3" → False (обычный номер тома)
        
        Простая эвристика: если текст = "Слова Число" и число <= 20 — 
        мы не можем точно знать без контекста. Поэтому этот метод
        должен вызываться только когда контекст известен.
        """
        # Этот метод — заглушка, реальная логика в _extract_series_from_brackets
        # который возвращает результат с флагом через специальный маркер
        return bool(re.match(r'^.+\s+\d+$', text.strip()))

    def _is_author_surname(self, series_candidate: str, author: str) -> bool:
        """
        Проверить что extracted series это не просто фамилия автора.
        
        Примеры:
            ("Белоус", "Белоус Олег") → True (это фамилия)
            ("А.Белоус", "Алексей Белоус") → True (сокращенное - инициал + фамилия)
            ("Белоус", "Иванов Сергей") → False (не фамилия)
            ("Солдат удачи", "Авраменко Александр") → False (это серия)
        
        Args:
            series_candidate: Извлеченная серия
            author: Автор в формате "Фамилия Имя" или "Имя Фамилия"
            
        Returns:
            True если series - это фамилия автора
        """
        if not series_candidate or not author:
            return False
        
        author_parts = author.strip().split()
        if not author_parts:
            return False
        
        series_lower = series_candidate.lower()
        series_normalized = re.sub(r'[^\w]', '', series_lower)
        
        # Проверяем полное совпадение: серия == полное имя автора
        # Пример: "Александрова Наталья" == "Александрова Наталья" → True
        if series_lower.strip() == author.lower().strip():
            return True
        
        # Проверяем КАЖДУЮ часть автора (может быть "Фамилия Имя" или "Имя Фамилия")
        for part in author_parts:
            part_lower = part.lower()
            part_normalized = re.sub(r'[^\w]', '', part_lower)
            
            # Точное совпадение целой части (например: "Белоус" = "Белоус")
            if part_normalized == series_normalized:
                return True
            
            # Для сокращенного формата (А.Фамилия), проверяем совпадение в конце
            # Например: "А.Белоус" содержит "Белоус" (последняя часть после последней точки)
            if '.' in series_lower:
                # Извлекаем последний слог после крайней точки  (А. → А, В.К. → К, Белоус → Белоус)
                # Разбиваем по точке и берем последнюю часть, которая содержит кириллицу
                match = re.search(r'([А-Яа-яЁё]+)\.?$', series_lower)
                if match:
                    surname_part = match.group(1).lower()
                    surname_part_normalized = re.sub(r'[^\w]', '', surname_part)
                    
                    # Проверяем, совпадает ли эта часть с частью автора
                    if part_normalized == surname_part_normalized:
                        return True
        
        return False
    
    def _balance_quotes(self, text: str) -> str:
        """
        Восстановить парные кавычки в тексте.
        
        Если в тексте есть открывающиеся кавычки но не хватает закрывающихся,
        автоматически добавляет закрывающиеся сдачи.
        
        Обрабатывает три типа кавычек:
        - Русские guillemets: « и »
        - Двойные кавычки: " и "
        - Одиночные кавычки: ' и '
        
        Примеры:
            "Вариант «Бис" → "Вариант «Бис»"
            "Цикл «Война «Ночи" → "Цикл «Война «Ночи»»"
            "Название "серия" → "Название "серия""
            "Текст 'цикл" → "Текст 'цикл'"
        
        Args:
            text: Исходный текст
        
        Returns:
            Текст с уравновешенными кавычками
        """
        if not text:
            return text
        
        # Определить типы кавычек и их пары
        quote_pairs = [
            ('«', '»'),  # Russian guillemets
            ('"', '"'),  # Double quotes
            ("'", "'"),  # Single quotes
        ]
        
        result = text
        
        for open_quote, close_quote in quote_pairs:
            open_count = result.count(open_quote)
            close_count = result.count(close_quote)
            
            # Если открывающихся больше, чем закрывающихся
            if open_count > close_count:
                missing = open_count - close_count
                # Добавляем недостающие закрывающиеся кавычки в конец
                result = result + close_quote * missing
        
        return result
    
    def _fix_russian_grammar(self, series: str) -> str:
        """
        Исправляет грамматические ошибки в названии серии по правилам русского языка.
        
        Правило: перед союзом 'что' в придаточном предложении нужна запятая.
        
        Примеры:
        - "Сделай что сможешь" → "Сделай, что сможешь"
        - "Расчеты что нужны" → "Расчеты, что нужны"
        - "что-то" → не изменяется (это не союз, а местоимение)
        
        Args:
            series: Название серии
            
        Returns:
            Исправленное название серии
        """
        if not series:
            return series
        
        # Ищем слово "что" как отдельное слово (не часть другого слова)
        # Используем word boundaries \b для точного совпадения
        # Проверяем что запятая еще не стоит перед "что"
        
        # Паттерн: что-то вроде "...слово что..." где перед "что" НЕТ запятой
        # Заменяем на "...слово, что..."
        pattern = r'(\S)\s+что\b'  # Пробел + "что" как отдельное слово, перед ним не запятая
        
        # Проверяем что "что" это отдельное слово (не часть "что-то" или "кто-то")
        def replacer(match):
            prefix = match.group(1)
            # Если перед словом уже есть запятая, не добавляем еще одну
            if prefix == ',':
                return match.group(0)
            # Если это дефис (как в "что-то"), не трогаем
            if prefix == '-':
                return match.group(0)
            # Иначе добавляем запятую
            return f"{prefix}, что"
        
        result = re.sub(pattern, replacer, series)
        return result

    def _score_pattern_match(self, pattern: str, filename: str, extracted_series: str) -> int:
        """
        Оценить степень соответствия паттерна структуре файла.
        Выбирает ЛУЧШИЙ паттерн из нескольких кандидатов.
        
        Критерии оценки:
        1. Специфичность паттерна (более специфичные выше)
        2. Совпадение структурных элементов с файлом
        3. Качество результата (количество слов в серии)
        
        Args:
            pattern: Паттерн из конфига
            filename: Имя файла без расширения
            extracted_series: Извлеченная серия
            
        Returns:
            Оценка (чем выше, тем лучше совпадение). -1 = нет результата.
        """
        if not extracted_series:
            return -1

        # ── HARD DISQUALIFIERS ──────────────────────────────────────────────────
        # Если паттерн требует структурный элемент, которого нет в имени файла,
        # этот паттерн не может подойти → сразу возвращаем -1.

        # Паттерн требует ' - ' (разделитель-тире), но в имени файла его нет
        if ' - ' in pattern and ' - ' not in filename:
            return -1

        # Паттерн требует запятую (соавторы), но в имени файла её нет
        if ',' in pattern and ',' not in filename:
            return -1

        # Паттерн требует скобки '(', но в имени файла их нет
        if '(' in pattern and '(' not in filename:
            return -1

        # ── POSITIVE SCORING ────────────────────────────────────────────────────
        # Начисляем очки за каждый структурный элемент, который паттерн
        # правильно предсказывает. Также начисляем очки, когда паттерн
        # правильно предсказывает ОТСУТСТВИЕ элемента (двунаправленное).

        score = 0
        max_score = 0

        # Тире ' - '
        max_score += 3
        if ' - ' in pattern:
            if ' - ' in filename:
                score += 3
        else:
            # Паттерн без тире — награждаем, если и в файле нет тире
            if ' - ' not in filename:
                score += 3

        # Запятая (соавторы)
        max_score += 2
        if ',' in pattern:
            if ',' in filename:
                score += 2
        else:
            if ',' not in filename:
                score += 2

        # Скобки '('
        max_score += 2
        if '(' in pattern:
            if '(' in filename:
                score += 2
        else:
            if '(' not in filename:
                score += 2
        
        # КРИТИЧНА ПРОВЕРКА: Если файл имеет скобки с series info, а паттерн 
        # это "Author - Series (...)" - нужно проверить ЧТО в скобках!
        # 
        # ПРАВИЛЬНО: "Author - Series (service_word)" 
        #   пример: "Горе победителям (Дилогия)" - в скобках ТОЛЬКО служебное слово
        # 
        # НЕПРАВИЛЬНО: "Author - Title (Series. Details)"
        #   пример: "Заголовок (Серия 1-3)" - в скобках сложная структура
        #
        has_brackets = '(' in filename and ')' in filename
        if pattern == 'Author - Series (service_words)' and has_brackets:
            # Проверяем что находится в скобках - берем ПЕРВУЮ пару скобок, не последнюю
            bracket_match = re.search(r'\(([^)]+)\)(?:\s*\(|\s*$)', filename)
            if bracket_match:
                bracket_content = bracket_match.group(1).strip().lower()
                
                # Проверяем наличие сложной структуры (точки, запятые и т.д.)
                has_complex_structure = '.' in bracket_content or ',' in bracket_content
                
                # Проверяем: это ТОЛЬКО service_word (одно слово из списка)?
                is_only_service_word = False
                for sw in self.service_words:
                    if bracket_content == sw.lower():
                        is_only_service_word = True
                        break
                
                if is_only_service_word and not has_complex_structure:
                    # ✓ ПРАВИЛЬНО: в скобках только служебное слово (Дилогия, Трилогия и т.д.)
                    # Это РОВНО соответствует паттерну "Author - Series (service_words)"
                    # Даём БОНУС за правильное распознавание структуры
                    score += 3
                elif has_complex_structure:
                    # ✗ НЕПРАВИЛЬНО: в скобках сложная структура с точками/запятыми
                    # Это структура "Author - Title (info)", не "Author - Series (service_word)"
                    # Штрафуем за неправильный паттерн
                    score -= 5
                    if score < -1:
                        return -1

        # service_words в паттерне
        max_score += 1
        if 'service_words' in pattern:
            score += 1

        # Бонус: серия извлечена из скобок — более надёжный источник
        # Паттерны "(Series. service_words)" и "(Series service_words)" надёжнее чем "Author - Series (...)"
        # потому что в скобках явно указана серия, а не Title
        # ВАЖНО: не даём бонус если extracted_series это service_word!
        # Service words (Тетралогия, Дилогия, Трилогия) — это не названия серий,
        # это описания количества книг. Если извлекли service_word из скобок —
        # это не означает что скобки содержали название серии.
        max_score += 3
        bracket_series_patterns = [
            "Author - Series (service_words)",  # Добавлен: "Author - Series (service_word)"
            "Author - Title (Series. service_words)",
            "Author - Title (Series service_words)",
            "Author. Title (Series. service_words)",
            "Author. Title (Series. Title. service_words)",
            "Author, Author - Title (Series. service_words)",
            "Author, Author. Title (Series)",
            "Author, Author. Title (Series. Title. service_words)",
            # Patterns with year metadata at the end
            "Author - Title (Series. service_words) - year",
            "Author - Series (service_words) - year",
            "Author - Title (Series service_words) - year",
            "Author. Title (Series. service_words) - year",
            "Author, Author. Title (Series. Title. service_words) - year",
            # Multi-level series patterns (главная серия. подсерия. подподсерия)
            "Author - Title (Series service_words. SubSeries service_words. SubSubSeries service_words)",
        ]
        if pattern in bracket_series_patterns:
            # Проверяем что extracted_series это не service_word перед начислением бонуса
            extracted_series_lower = extracted_series.lower().strip()
            is_service_word = False
            for sw in self.service_words:
                sw_lower = sw.lower()
                if extracted_series_lower == sw_lower or extracted_series_lower.startswith(sw_lower + ' '):
                    is_service_word = True
                    break
            
            # Только даём бонус если это НЕ service_word
            if not is_service_word:
                score += 3
        
        # Длина извлечённой серии: больше слов = надёжнее
        word_count = len(extracted_series.split())
        max_score += 6
        if word_count >= 2:
            score += min(6, word_count * 2)
        elif word_count == 0:
            return -1

        if max_score == 0:
            return 0

        return max(0, score)
