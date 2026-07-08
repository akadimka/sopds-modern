"""
PASS 3 для СЕРИЙ: Нормализация названий серий.
Аналог pass3_normalize.py (для авторов) но для СЕРИЙ.
"""

import re
from typing import List

try:
    from series_normalizer import _nfc_lower_yo
except ImportError:
    import unicodedata
    def _nfc_lower_yo(s):
        return unicodedata.normalize("NFC", s).lower().replace("ё", "е")

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

from ..logger import Logger
from ..settings_manager import SettingsManager


class Pass3SeriesNormalize:
    """Нормализация названий серий."""
    
    def __init__(self, logger: Logger = None, settings=None):
        self.logger = logger or Logger()
        self.settings = settings or SettingsManager('config.json')
        # Get series conversions from config.json if available
        try:
            # Try to access the settings directly
            self.series_conversions = self.settings.settings.get('series_conversions', {})
        except (AttributeError, KeyError):
            self.series_conversions = {}
        
        # Load cleanup patterns from config
        try:
            self.cleanup_patterns = self.settings.settings.get('series_cleanup_patterns', [])
        except (AttributeError, KeyError):
            self.cleanup_patterns = []

        # Pre-compile service-word patterns once — avoids re-compiling per record
        import re as _re
        raw_service_words = self.settings.get_list('service_words') or []
        self._service_word_patterns = [
            _re.compile(r'\s*\b' + _re.escape(w) + r'\b(\s+\d+)?\s*$', _re.IGNORECASE)
            for w in raw_service_words if w
        ]
    
    def execute(self, records: List[BookRecord]) -> None:
        """
        Нормализовать названия серий:
        - Убрать номера выпусков в конце (Серия (1-3) → Серия)
        - Привести к стандартному capitalizations
        - Применить преобразования из config.json
        """
        for record in records:
            if not record.proposed_series:
                continue

            original = record.proposed_series
            normalized = self._normalize_series_name(original)
            normalized = self._sanitize_for_folder(normalized)

            # Если нормализация укоротила серию (напр. убрала "трилогия"),
            # но metadata_series подтверждает полное название — оставляем оригинал.
            if normalized != original and record.metadata_series:
                if _nfc_lower_yo(record.metadata_series.strip()) == _nfc_lower_yo(original):
                    normalized = original

            # Bracket-author suffix ([Громов], [Иванов]) всегда убираем —
            # даже если metadata guard восстановил оригинал.
            normalized = re.sub(r'\s*\[[^\]]*\]\s*$', '', normalized).strip()

            # series_conversions применяем ПОСЛЕ metadata guard — они имеют
            # приоритет над метаданными (явная конфигурация важнее автоопределения).
            for old_name, new_name in self.series_conversions.items():
                if normalized.lower() == old_name.lower():
                    normalized = new_name
                    break

            # Издательские префиксы из series_cleanup_patterns применяем ПОСЛЕ metadata guard:
            # metadata может сама содержать издательскую метку («Романы МИФ. Серия»),
            # и metadata guard её восстанавливал до очистки.
            for _cpat in self.cleanup_patterns:
                if _cpat.startswith('^'):  # только anchor-паттерны (префиксы)
                    _after = re.sub(_cpat, '', normalized, flags=re.IGNORECASE).strip()
                    if _after and _after != normalized:
                        normalized = _after
                        break

            # FOLDER-PREFIX GUARD: если серия вида «Коллекция. Подсерия»,
            # а «Коллекция» совпадает с именем одной из родительских папок —
            # оставляем только «Подсерия».
            # Пример: «Артефакт - детектив. Астра Ельцова»,
            #   папка «Артефакт & Детектив» → серия «Астра Ельцова»
            if '. ' in normalized and '\\' not in normalized and record.file_path:
                import re as _re2
                from pathlib import Path as _P
                prefix, suffix = normalized.split('. ', 1)
                suffix = suffix.strip()
                if suffix:
                    prefix_words = set(_re2.sub(r'[^\w]', ' ', prefix.lower()).split())
                    prefix_words.discard('')
                    parts = _P(record.file_path).parts[:-1]
                    # Серийная папка (откуда взята серия) — последняя в пути.
                    # Не проверяем её: prefix может быть частью самого имени серийной папки.
                    series_folder = parts[-1] if parts else ''
                    for folder in parts:
                        if folder == series_folder:
                            continue
                        folder_words = set(_re2.sub(r'[^\w]', ' ', folder.lower()).split())
                        folder_words.discard('')
                        if prefix_words and folder_words:
                            overlap = prefix_words & folder_words
                            ratio = len(overlap) / len(prefix_words)
                            if ratio >= 0.6:
                                normalized = suffix
                                break

            if normalized != record.proposed_series:
                record.proposed_series = normalized

        # --- Схлопывание избыточной иерархии ---
        # «ОБХСС\ОБХСС 82» → «ОБХСС 82»: подсерия начинается с того же слова что корень,
        # значит «ОБХСС\» — избыточный префикс. После схлопывания punct-унификация
        # совместит «ОБХСС 82» и «ОБХСС-82» (одинаковый punct_key = "обхсс 82").
        for rec in records:
            s = rec.proposed_series or ''
            if '\\' not in s:
                continue
            root, sub = s.split('\\', 1)
            root_norm = _nfc_lower_yo(re.sub(r'[^\w\s]', ' ', root.strip()))
            root_norm = re.sub(r'\s+', ' ', root_norm).strip()
            sub_norm  = _nfc_lower_yo(re.sub(r'[^\w\s]', ' ', sub.strip()))
            sub_norm  = re.sub(r'\s+', ' ', sub_norm).strip()
            # Подсерия начинается с корня (слово-в-слово) и добавляет что-то ещё
            if root_norm and sub_norm.startswith(root_norm + ' '):
                rec.proposed_series = sub.strip()

        # --- Унификация по punct-нормализованному ключу ---
        # Если несколько вариантов одной серии отличаются только пунктуацией
        # (напр. "Ревизор. Возвращение в СССР" и "Ревизор возвращение в СССР"),
        # выбираем каноническое название по приоритету источника.
        _SRC_PRIORITY = {
            'folder_dataset': 6, 'folder_hierarchy': 5,
            'folder_meta_consensus': 4, 'folder_metadata_confirmed': 3,
            'filename': 2, 'metadata': 1,
        }

        def _punct_key(s: str) -> str:
            s = _nfc_lower_yo(s.strip())
            return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', s)).strip()

        # Собираем: punct_key → список (priority, series_value)
        from collections import defaultdict
        _key_variants: dict = defaultdict(list)
        for rec in records:
            if not rec.proposed_series:
                continue
            pk = _punct_key(rec.proposed_series)
            pri = _SRC_PRIORITY.get(rec.series_source or '', 0)
            _key_variants[pk].append((pri, rec.proposed_series))

        # Для каждого ключа с несколькими вариантами — берём вариант с высшим приоритетом
        # При равном приоритете — более длинный (с пунктуацией)
        _canonical: dict = {}
        for pk, variants in _key_variants.items():
            best = max(variants, key=lambda x: (x[0], len(x[1])))
            _canonical[pk] = best[1]

        # Применяем канонические имена
        unified = 0
        for rec in records:
            if not rec.proposed_series:
                continue
            pk = _punct_key(rec.proposed_series)
            canon = _canonical.get(pk)
            if canon and canon != rec.proposed_series:
                rec.proposed_series = canon
                unified += 1

        if unified:
            print(f'[SERIES PASS 3] Unified {unified} series names by punct-normalization')

    def _normalize_series_name(self, series: str) -> str:
        """Нормализовать формат названия серии."""
        
        # Шаг 1: Убрать лишние пробелы
        series = ' '.join(series.split())
        
        # Шаг 1.1: Обработать двоеточия (в т.ч. китайское «：» U+FF1A).
        # Если перед двоеточием стоит имя из 1–2 слов (напр. «Байши Сюсянь: Название»),
        # берём часть ПОСЛЕ двоеточия — это и есть реальное название серии.
        # Иначе просто убираем двоеточие (чтобы не ломать «Война: год первый»).
        for colon_char in ('：', ':'):
            if colon_char in series:
                before, after = series.split(colon_char, 1)
                before = before.strip()
                after = after.strip()
                # Считаем «before» именем-префиксом если это 1–2 слова с заглавной буквы
                _words = before.split()
                _is_name_prefix = (
                    len(_words) in (1, 2) and
                    all(w and w[0].isupper() for w in _words)
                )
                if _is_name_prefix and after:
                    series = after
                else:
                    series = series.replace(colon_char, '')
                break
        
        # Шаг 1.5: Заменить ё на е для унификации
        # "Тёмный век" → "Темный век"
        # "Чужие звёзды" → "Чужие звезды"
        # "Ёлка" → "Елка"
        import unicodedata as _ud
        series = _ud.normalize('NFC', series).replace('\u0451', '\u0435').replace('\u0401', '\u0415')
        
        # Шаг 1.7: Убрать суффикс-дизамбигуатор в квадратных скобках
        # "Золотой век[Иггульден]" → "Золотой век"
        # "Пастух[Кросс]" → "Пастух"
        # В названиях серий квадратные скобки всегда служат меткой автора, не частью имени.
        series = re.sub(r'\s*\[[^\]]*\]\s*$', '', series).strip()

        # Шаг 1.8: Убрать суффикс-дизамбигуатор в круглых скобках без цифр:
        # а) одно слово-фамилия:         "Дракон (Трофимов)"            → "Дракон"
        # б) слова через " - " (дефис):  "Серия (Замполит - Башибузук)" → "Серия"
        # в) слова через ", " (запятая): "Серия (Ларин, Барчук)"        → "Серия"
        # Признак: все слова начинаются с заглавной буквы, нет цифр.
        # Исключение: если перед скобками только цифра — это порядковое название
        # ("1 (Первый)" — скобки несут смысл, не убираем).
        _AUTH_WORD = r'[А-ЯЁA-Z][А-Яа-яёЁA-Za-z]+'
        _AUTH_SEP  = r'(?:\s*[-–,]\s*' + _AUTH_WORD + r')*'
        _auth_disambig_match = re.search(
            r'\s*\(' + _AUTH_WORD + _AUTH_SEP + r'\s*\)\s*$',
            series
        )
        if _auth_disambig_match:
            before_parens = series[:_auth_disambig_match.start()].strip()
            if not re.match(r'^\d+$', before_parens):
                series = before_parens

        # Шаг 1.9: Убрать хвостовой идентификатор-номер тома
        # "Отряд «Сигма» 07+" → "Отряд «Сигма»"
        # "Серия 05" → "Серия"
        # Правило: убирать если число либо zero-padded (0X, XX) либо имеет суффикс +
        # Примеры что НЕ должно strip: "Война 1941" (4 цифры), "100 лет" (не trailing)
        series = re.sub(r'\s+(?:0\d+\+?|\d+\+)\s*$', '', series).strip()

        # Шаг 1.95: Убрать служебный префикс «Цикл «...»» / «Цикл "..."»
        # "Цикл «Солдат удачи»" → "Солдат удачи"
        # "Цикл «Вариант «Бис»»" → "Вариант «Бис»"
        # НО: "Каледонийский цикл" — слово "цикл" НЕ в начале → не трогаем.
        _cycle_match = re.match(
            r'^[Цц]икл\s*[«"\'«](.+?)[»"\'»]\s*$',
            series
        )
        if _cycle_match:
            series = _cycle_match.group(1).strip()

        # Шаг 1.99: Убрать незакрытую скобку в конце строки.
        # Возникает когда блок-матчер обрезает имя файла на точке:
        # "Пророчество (т. 1)" → блок = "Пророчество (т" → убираем " (т"
        # Применяем только если после '(' нет закрывающей ')'.
        _uc = re.search(r'\s*\([^)]*$', series)
        if _uc:
            series = series[:_uc.start()].strip()

        # Шаг 2: Убрать номер в скобках если есть
        # "Война в Космосе (1-3)" → "Война в Космосе"
        # НО: не убираем если содержимое скобок начинается со СЛОВА ("Хроники 7-8" —
        # это контекст нумерации в иерархии, а не просто диапазон томов).
        _m2 = re.search(r'\s*\(([^)]*\d[^)]*)\)\s*$', series)
        if _m2:
            _inner = _m2.group(1).strip()
            # Стрипаем только если содержимое НЕ начинается с буквенного слова
            if not re.match(r'^[А-ЯЁA-Za-zа-яё]', _inner):
                series = series[:_m2.start()].strip()
        
        # Шаг 3: Убрать скобки с информацией об авторстве/сотрудничестве
        # "Лорд Системы (соавтор Яростный Мики)" → "Лорд Системы"
        # "Title (with author X)" → "Title"
        for pattern in self.cleanup_patterns:
            series = re.sub(pattern, ' ', series, flags=re.IGNORECASE)
        
        # Уберем несколько пробелов если они появились после удаления скобок
        series = ' '.join(series.split())
        
        # Шаг 4: Убрать лишние служебные слова в конце
        # "Война и Мир том 1" → "Война и Мир"
        # НО: не убирать если остаток — одно слово ("Каирский цикл" → не strip, т.к. "цикл" — часть названия)
        for pat in self._service_word_patterns:
            candidate = pat.sub('', series).strip()
            if len(candidate.split()) >= 2:
                series = candidate
        
        # Шаг 5: Применить conversions из config (если настроены)
        for old_name, new_name in self.series_conversions.items():
            if series.lower() == old_name.lower():
                series = new_name
                break
        
        return series.strip()

    def _sanitize_for_folder(self, value: str) -> str:
        """Убрать символы, недопустимые в именах папок Windows/Linux, и случайные '='."""
        import re
        return re.sub(r'[/:*?<>=|]', '', value).strip()
