"""
FB2 Compilation Service

Объединяет несколько FB2-файлов одного автора и одной серии в один файл.
Работает исключительно с данными из BookRecord (результат pipeline generate_csv).

Порядок сортировки книг (многоуровневый):
  1. series_number — явный номер тома (целое число)
  2. Число в начале имени файла: "1. Название", "02 Название"
  3. date в <title-info> FB2 (год написания)
  4. date в <publish-info> FB2 (год издания)
  → Если порядок не определён → группа помечается как неопределённая

Выходной файл: UTF-8, структура:
  <description> с метаданными из первого файла группы (автор, жанр)
  <sequence name="Серия" number="1-7"/>
  Один <body> на каждую книгу с <title><p>N. Название</p></title>
"""

import re
import html as _html
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Optional, Dict

try:
    from .series_normalizer import _nfc_lower_yo as _norm_key
except ImportError:
    def _norm_key(s: str) -> str:
        return unicodedata.normalize('NFC', s).lower().replace('ё', 'е')

try:
    from .passes.pass1_read_files import BookRecord
except ImportError:
    BookRecord = None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CompilationBook:
    """Одна книга внутри группы компиляции."""
    record: object          # BookRecord
    abs_path: Path          # Абсолютный путь к файлу
    sort_key: Tuple         # (sort_level 0-3, value) — для сортировки
    sort_source: str        # "series_number" | "filename" | "title_date" | "publish_date"
    order_ambiguous: bool   # True если порядок не определён
    volume_label: str = ''  # Отображаемый номер тома: "1", "1-3", "Свиток 1" и т.п.


@dataclass
class CompilationGroup:
    """Группа файлов для компиляции."""
    author: str
    series: str
    books: List[CompilationBook]
    order_determined: bool  # False если хотя бы у одной книги ambiguous
    volume_range: str       # "1-7" или ""
    duplicate_paths: List[Path] = None  # Файлы-дубликаты для автоматического удаления
    kept_paths: List[Path] = None       # Файлы, которые остаются (для cleanup_only групп)
    excluded_paths: List[Path] = None        # Исключены вручную — не компилируются и не удаляются
    auto_excluded_paths: List[Path] = None  # Исключены автоматически из-за пробела в томах
    alphabetical_order: bool = False    # True — порядок не определён, отсортировано по названию
    cleanup_only: bool = False          # True — новая компиляция не нужна, только удалить дубликаты
    part_count: int = 0                 # > 0 если книги имеют паттерн N.M (том.часть): общее число частей
    series_complete: bool = True        # False если за пределами run'а есть другие тома серии

    def __post_init__(self):
        if self.duplicate_paths is None:
            self.duplicate_paths = []
        if self.kept_paths is None:
            self.kept_paths = []
        if self.excluded_paths is None:
            self.excluded_paths = []
        if self.auto_excluded_paths is None:
            self.auto_excluded_paths = []


@dataclass
class CompilationResult:
    """Результат компиляции одной группы."""
    group: CompilationGroup
    output_path: Path
    books_compiled: int
    source_paths: List[Path]
    success: bool
    error: str = ""


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class FB2CompilerService:
    """Сервис компиляции: анализирует записи и создаёт объединённые FB2."""

    # Regex для извлечения числа из начала stem:
    # "1. Название", "02 - Название", "3 Название", "1 Возмездие неизбежно"
    # Последний вариант: цифра + пробел + заглавная буква (без спецсимвола)
    _STEM_NUM_RE = re.compile(
        r'^(\d{1,4})\s*[.\-–—_)]\s*'           # "1. " / "02 - " / "3_"
        r'|^(\d{1,4})\s+(?=[А-ЯЁA-Z\(])'       # "1 Название" (пробел + заглавная)
        r'|\s+(\d{1,4})\s*[.\-–—_)]\s'          # внутри: " 3. "
        r'|\s+(\d{1,4})$',                       # в конце: "Серия 1"
        re.UNICODE
    )

    # Сервисные слова для N томов (2..N)
    _SERIES_WORDS = [
        None,          # 0 — не используется
        None,          # 1 — одиночная книга
        'Дилогия',    # 2
        'Трилогия',   # 3
        'Тетралогия', # 4
        'Пенталогия', # 5
        'Гексалогия', # 6
        'Гепталогия', # 7
        'Окталогия', # 8
        'Ноналогия', # 9
        'Декалогия', # 10
    ]

    # Regex для очистки сервисных слов/диапазонов из имени серии
    _SERIES_CLEAN_RE = re.compile(
        r'\s*\([^)]*\)\s*$'               # trailing (…)
        r'|\s*[тт]\.\s+\d+[-–]\d+\s*$'   # trailing т. 1-4
        r'|\s*\d+[-–]\d+\s*$',             # trailing 1-4
        re.IGNORECASE | re.UNICODE,
    )

    @staticmethod
    def _series_to_display(series: str) -> str:
        """Конвертировать внутренний формат серии в отображаемую строку.

        'Отрок_Сотник\\1. Отрок'  → 'Отрок_Сотник 1. Отрок'
        'Хроники\\Первый цикл'    → 'Хроники. Первый цикл'
        'Серия'                   → 'Серия'
        """
        if '\\' not in series:
            return series
        root, sub = series.split('\\', 1)
        sub = sub.strip()
        m = re.match(r'^(\d+)\s*[\.\)]\s*(.+)$', sub)
        if m:
            return f'{root} {m.group(1)}. {m.group(2).strip()}'
        return f'{root}. {sub}'

    @classmethod
    def _clean_series_name(cls, series: str) -> str:
        """Убрать сервисные слова и диапазоны из имени серии.

        «Солдат удачи (Тетралогия)»       → «Солдат удачи»
        «Солдат удачи. Тетралогия 1-4»   → «Солдат удачи. Тетралогия» (точечные
        субсерии не трогаем — они часть иерархии).
        """
        cleaned = cls._SERIES_CLEAN_RE.sub('', series).strip()
        # Убрать хвостовое сервисное слово после последней точки, если оно совпадает
        for kw in cls._SERIES_WORDS[2:]:
            if kw and cleaned.rstrip().lower().endswith('.' + kw.lower()):
                cleaned = cleaned[:-(len(kw) + 1)].strip()
                break
            if kw and re.search(
                rf'[.(\s]{re.escape(kw)}$', cleaned, re.IGNORECASE
            ):
                cleaned = re.sub(
                    rf'[.\s]*{re.escape(kw)}\s*$', '', cleaned,
                    flags=re.IGNORECASE
                ).strip()
                break
        return cleaned or series

    @classmethod
    def _run_stats(cls, books: list) -> tuple:
        """Вычислить статистику run'а для именования компиляции.

        Returns (top_lo, top_hi, n_volumes, has_subseries):
            top_lo      — минимальная верхнеуровневая позиция (sort_key[1])
            top_hi      — максимальная эффективная верхнеуровневая позиция
                          (раскрывает volume_label "N-M" для level-0 книг без secondary)
            n_volumes   — суммарное число логических томов
                          (раскрывает ВСЕ volume_label, включая sub-level)
            has_subseries — есть ли книги с sort_key[2] != 0
        """
        level0 = [b for b in books if b.sort_key[0] == 0]
        if not level0:
            return 1, 1, len(books), False, None

        _RNG = re.compile(r'^(\d+)\s*[-–—]\s*(\d+)$')

        # Для подсерий без числа в корне позиция хранится в sort_key[2] (parent_num=0).
        # Пример: "Отрок_Сотник\1. Отрок" → sort_key=(0,0,sub_ordinal,0).
        all_sub_plane = all(b.sort_key[1] == 0 for b in level0) and any(b.sort_key[2] != 0 for b in level0)
        if all_sub_plane:
            sub_positions = [b.sort_key[2] for b in level0 if b.sort_key[2] != 0]
            top_lo = min(sub_positions)
            top_hi = max(sub_positions)
            has_subseries = False
        else:
            top_lo = min(b.sort_key[1] for b in level0)

            top_hi_vals = []
            for b in level0:
                vl = (b.volume_label or '').strip()
                if b.sort_key[2] != 0:
                    top_hi_vals.append(b.sort_key[1])
                else:
                    m = _RNG.match(vl)
                    top_hi_vals.append(int(m.group(2)) if m else b.sort_key[1])
            top_hi = max(top_hi_vals)

            _top_pos_list = [b.sort_key[1] for b in level0]
        # has_subseries: либо у кого-то sort_key[2]!=0, либо несколько файлов
        # занимают одну и ту же top-позицию (разные подсерии одной дуги).
        has_subseries = (
            any(b.sort_key[2] != 0 for b in level0)
            or len(_top_pos_list) > len(set(_top_pos_list))
        )

        # dot_part: «Том N Книга M» — secondary = номер книги внутри тома.
        # n_volumes = число различных томов (sort_key[1]), не число файлов.
        all_dot_part = all(getattr(b, 'sort_source', '') == 'dot_part' for b in level0)
        if all_dot_part:
            n_volumes = len({b.sort_key[1] for b in level0})
        else:
            # Диапазоны считаем через объединение множеств (чтобы не задваивать пересечения).
            # Индивидуальные книги считаем по +1 — у них нет пересечений после dedup.
            _range_covered: set = set()
            _individual = 0
            for b in level0:
                vl = (b.volume_label or '').strip()
                m = _RNG.match(vl)
                if m:
                    _range_covered.update(range(int(m.group(1)), int(m.group(2)) + 1))
                else:
                    _individual += 1
            n_volumes = len(_range_covered) + _individual

        # Для групп с подсериями (has_subseries=True) определяем число верхних дуг —
        # различных значений sort_key[1]. Именно они определяют слово «Пенталогия» и т.п.,
        # тогда как n_volumes остаётся общим числом книг (для «в N книгах»).
        n_top_arcs = len({b.sort_key[1] for b in level0 if b.sort_key[1] != 0}) if has_subseries else None

        return top_lo, top_hi, n_volumes, has_subseries, n_top_arcs

    @classmethod
    def _series_suffix(cls, n_volumes: int, lo: int, hi: int = None, part_count: int = 0,
                       series_complete: bool = True, use_parts: bool = False) -> str:
        """Вернуть суффикс для имени файла компиляции.

        n_volumes       — число логических томов в run'е
        lo              — первая позиция run'а
        hi              — последняя позиция run'а (если None — вычисляется как lo+n_volumes-1)
        part_count      — для dot_part: число физических частей; если > n_volumes,
                          добавляем «в N книгах» к служебному слову
        series_complete — True если серия считается завершённой на этом run'е
                          (нет других томов/блоков за его пределами). Если False —
                          используем «книги N-M» вместо «Дилогия/Трилогия/…»,
                          т.к. наличие пропущенных/будущих томов означает незаконченность.

        Правила:
          • lo ∈ {0, 1} И series_complete → служебное слово (Дилогия, Трилогия…)
            или «в N книгах» если слова нет.
          • lo ∈ {0, 1} И NOT series_complete → «книги 1-N» (неполная серия).
          • lo > 1 (частичный run) → «т. N» или «т. N-M».
        """
        if hi is None:
            hi = lo + n_volumes - 1
        n_books = part_count if part_count > 0 else n_volumes
        if lo in (0, 1):
            if series_complete:
                if 2 <= n_volumes < len(cls._SERIES_WORDS) and cls._SERIES_WORDS[n_volumes]:
                    word = cls._SERIES_WORDS[n_volumes]
                    if n_books > n_volumes:
                        return f'{word} в {n_books} книгах'
                    return word
                if n_books == 1:
                    return 'в 1 книге'
                return f'в {n_books} книгах'
            else:
                # Серия незавершена — за пределами run'а есть другие тома
                if lo == hi:
                    return f'книга {lo}'
                return f'книги {lo}-{hi}'
        # Частичный run — указываем диапазон томов/частей
        _lbl = 'ч.' if use_parts else 'т.'
        if lo == hi:
            _base = f'{_lbl} {lo}'
        else:
            _base = f'{_lbl} {lo}-{hi}'
        # Если известно суммарное число книг (arc-point предкомпиляции) — добавляем счёт
        n_books_total = part_count if part_count > 0 else n_volumes
        if n_books_total > n_volumes:
            return f'{_base} в {n_books_total} книгах'
        return _base

    @staticmethod
    def _suppress_redundant_suffix(safe_series: str, suffix: str) -> str:
        """Убрать из суффикса «в N книгах/томах» если серия уже содержит то же число."""
        import re as _re
        _SERIES_COUNT = _re.compile(
            r'\bв\s+(\d+)\s+(?:томах|книгах)\b|\b(\d+)\s+томов\b|\b(\d+)\s+книг(?:и)?\b',
            _re.IGNORECASE,
        )
        m = _SERIES_COUNT.search(safe_series)
        if not m:
            return suffix
        count = int(next(g for g in m.groups() if g is not None))
        m_suf = _re.search(r'\bв\s+(\d+)\s+(?:томах|книгах)\b', suffix, _re.IGNORECASE)
        if m_suf and int(m_suf.group(1)) == count:
            stripped = suffix[:m_suf.start()].rstrip(' ,;–-')
            return stripped
        return suffix

    def __init__(self, logger=None):
        self.logger = logger

    def _log(self, msg: str):
        if self.logger:
            self.logger.log(msg)

    # ------------------------------------------------------------------
    # Группировка записей
    # ------------------------------------------------------------------

    def find_groups(
        self,
        records: List,
        work_dir: Path,
        on_group=None,
    ) -> List[CompilationGroup]:
        """Найти все группы (автор + серия) с ≥2 файлами.

        Args:
            records: Список BookRecord из pipeline.
            work_dir: Корневая папка (для построения абсолютных путей).
            on_group: опциональный callback(CompilationGroup) — вызывается сразу
                      при добавлении каждой группы (до финальной сортировки).

        Returns:
            Список CompilationGroup, отсортированный по (author, series).
        """
        # Группировка по (author_lower, series_lower)
        # Нормализуем ё→е в ключах, чтобы "Тёмные звёзды" и "Темные звезды" попали в одну группу.
        # Для подсерий ("Серия N\Подсерия") ключ группировки — корневое название без номера,
        # чтобы файлы "Серия 04\Роман" и "Серия" попали в одну компиляцию.
        def _punct_norm(s: str) -> str:
            """Нормализовать знаки препинания для ключа группировки серий.

            Заменяем : . , ; ! ? « » " ' — – - на пробел, схлопываем пробелы.
            Это позволяет "Ревизор. Возвращение" и "Ревизор возвращение"
            (а также "Ревизор: Возвращение") попасть в один бакет.
            Применяем ТОЛЬКО к ключу группировки — само название серии не меняем.
            """
            normalized = _norm_key(s)
            normalized = re.sub(r'[:.;,!?«»"\'—–\-]+', ' ', normalized)
            return re.sub(r'\s+', ' ', normalized).strip()

        def _series_group_key(series: str) -> str:
            if '\\' not in series:
                return _punct_norm(series)
            root, sub = series.split('\\', 1)
            root = root.strip()
            sub = sub.strip()
            root_no_num = re.sub(r'\s+\d{1,4}\s*$', '', root).strip()
            # Корень без числа — подсерии независимы (Отрок_Сотник\Отрок vs \Сотник).
            # Исключение: подсерия начинается с буквенного порядкового номера «А.», «Б.», «В.»…
            # Такие подсерии объединяются в один бакет по корню (Серия\Б. X + Серия\В. Y → Серия).
            if root_no_num == root:
                if re.match(r'^[А-ЯЁ]\.\s', sub):
                    return _punct_norm(root)
                return _punct_norm(series)
            # Корень с числом (Серия 1\X, Серия 3\Y):
            # ключ = «Серия|X» — объединяем только одноимённые подсерии,
            # разные подсерии (X≠Y) остаются в разных бакетах.
            sub_key = _punct_norm(sub) if sub else ''
            base_key = _punct_norm(root_no_num) if root_no_num else _punct_norm(series)
            return f'{base_key}|{sub_key}' if sub_key else base_key

        buckets: Dict[Tuple[str, str], List] = {}
        for rec in records:
            author = (rec.proposed_author or '').strip()
            series = (rec.proposed_series or '').strip()
            if not author or not series:
                continue
            # Для иерархических серий «Серия N\Подсерия» ключ = корень без числа,
            # чтобы «Пожиратель 7-8\Город воров» слился с «Пожиратель 1-6, 9-11».
            # НО: если корень не имеет числа («Антиблицкриг\ВоенТур»), подсерия
            # независима и компилируется отдельно — используем полный путь как ключ.
            if '\\' in series:
                _arc_root = series.split('\\')[0].strip()
                _arc_base = re.sub(r'\s+\d{1,4}(?:\s*[-–—]\s*\d{1,4})?\s*$', '', _arc_root).strip()
                if _arc_base != _arc_root:
                    # Корень с числом → ключ = корень без числа (сливаем с плоскими томами)
                    sk = _punct_norm(_arc_base)
                else:
                    # Корень без числа → подсерия потенциально независима, используем полный путь
                    sk = _series_group_key(series)
            else:
                sk = _series_group_key(series)
            key = (_norm_key(author), sk)
            buckets.setdefault(key, []).append(rec)

        # Комбинированное слияние бакетов: работает когда автор И/ИЛИ серия отличаются.
        # Условия слияния пары бакетов (ak1,sk1) и (ak2,sk2):
        #   • авторы: words(ak1) ⊆ words(ak2) или равны
        #   • серии:  sk1 является хвостовым суффиксом sk2, или равны
        #   • хотя бы одно из двух условий — строгое (не равенство)
        #   • метаподтверждение: хотя бы у одной записи из бакета с ДЛИННОЙ серией
        #     metadata_series (после _punct_norm) совпадает с КОРОТКОЙ серией.
        # Канонический ключ: более длинный автор + более длинная серия.
        def _word_suffix_key(short_sk: str, long_sk: str) -> bool:
            ws = short_sk.split()
            wl = long_sk.split()
            return bool(ws) and len(ws) < len(wl) and wl[-len(ws):] == ws

        def _author_words(ak: str) -> set:
            return set(re.sub(r'[,;]', ' ', ak).split())

        all_keys = list(buckets.keys())
        merged = True
        while merged:
            merged = False
            all_keys = list(buckets.keys())
            for i, (ak1, sk1) in enumerate(all_keys):
                if (ak1, sk1) not in buckets:
                    continue
                for (ak2, sk2) in all_keys[i + 1:]:
                    if (ak2, sk2) not in buckets:
                        continue
                    if (ak1, sk1) == (ak2, sk2):
                        continue

                    aw1 = _author_words(ak1)
                    aw2 = _author_words(ak2)

                    # Автор: определяем направление подмножества.
                    # Лишние токены допустимы только если выглядят как инициалы/аббревиатуры
                    # (длина ≤3 символов или содержат точку). Иначе — соавтор, не сливаем.
                    def _is_abbrev_only(extra: set) -> bool:
                        def _is_abbrev(t: str) -> bool:
                            if len(t) <= 3 or '.' in t:
                                return True
                            # А_З_К / а_з_к — буквы через подчёркивания (псевдоним-инициалы)
                            # токены уже в нижнем регистре → проверяем [а-яёa-z]
                            if re.match(r'^[а-яёa-z](?:_[а-яёa-z])+$', t):
                                return True
                            return False
                        return all(_is_abbrev(t) for t in extra)

                    if aw1 == aw2:
                        author_dir = 0        # равны
                    elif aw1 < aw2 and _is_abbrev_only(aw2 - aw1):
                        author_dir = 12       # ak1 короче, ak2 — канонический (аббревиатура)
                    elif aw2 < aw1 and _is_abbrev_only(aw1 - aw2):
                        author_dir = 21       # ak2 короче, ak1 — канонический (аббревиатура)
                    elif aw1 < aw2 and sk1 == sk2:
                        author_dir = 12       # ak1 ⊂ ak2, одна серия → допускаем слияние
                    elif aw2 < aw1 and sk1 == sk2:
                        author_dir = 21       # ak2 ⊂ ak1, одна серия → допускаем слияние
                    else:
                        continue              # пересекаются или разные серии — соавтор

                    # Серия: определяем направление суффикса
                    if sk1 == sk2:
                        series_dir = 0
                    elif _word_suffix_key(sk1, sk2):
                        series_dir = 12       # sk1 короче, sk2 — содержит
                    elif _word_suffix_key(sk2, sk1):
                        series_dir = 21       # sk2 короче, sk1 — содержит
                    else:
                        continue              # никакого суффиксного отношения

                    # Хотя бы одно измерение должно быть строгим
                    if author_dir == 0 and series_dir == 0:
                        continue

                    # Канонический: длинный автор + длинная серия
                    canon_ak = ak2 if author_dir == 12 else ak1
                    canon_sk = sk2 if series_dir == 12 else sk1
                    short_sk  = sk1 if series_dir == 12 else sk2

                    # Метаподтверждение для серийного суффикса
                    if series_dir != 0:
                        long_recs = buckets.get((ak1, sk1) if series_dir == 21 else (ak2, sk2), [])
                        confirmed = any(
                            _punct_norm(r.metadata_series or '') == short_sk
                            or _word_suffix_key(short_sk, _punct_norm(r.metadata_series or ''))
                            for r in long_recs
                        )
                        if not confirmed:
                            continue

                    canon_key = (canon_ak, canon_sk)
                    # Поглощаем оба бакета в canonical (они могут быть разными от canon)
                    for src_key in [(ak1, sk1), (ak2, sk2)]:
                        if src_key != canon_key and src_key in buckets:
                            buckets.setdefault(canon_key, []).extend(buckets.pop(src_key))
                    merged = True

        # Дополнительный проход: объединяем бакет «Серия» с «Серия\Арка» того же автора,
        # если оба существуют. Это нужно когда часть книг имеет plain proposed_series,
        # а другие — proposed_series с именованной дугой (Серия\Дуга).
        # Пример: «Не ГГ» (тт.1,4) + «Не ГГ\Курсанты» (тт.2-3) → одна группа «Не ГГ».
        # В отличие от общего цикла выше, этот проход не трогает независимые подсерии
        # (Антиблицкриг\ВоенТур без umbrella), потому что там нет plain-бакета.
        _all_keys = list(buckets.keys())
        for (ak, sk) in _all_keys:
            if (ak, sk) not in buckets:
                continue
            # Ищем бакеты того же автора, чей ключ начинается с sk + backslash
            for (ak2, sk2) in list(buckets.keys()):
                if ak2 != ak or sk2 == sk:
                    continue
                if (ak2, sk2) not in buckets:
                    continue
                sk2_norm = _norm_key(sk2)
                sk_norm  = _norm_key(sk)
                if sk2_norm.startswith(sk_norm + '\\'):
                    # sk — plain umbrella, sk2 — её подсерия → сливаем в umbrella
                    buckets[(ak, sk)].extend(buckets.pop((ak2, sk2)))

        groups: List[CompilationGroup] = []

        def _emit(g: CompilationGroup) -> None:
            # Дедупликация по имени файла (case-insensitive): из пары с одинаковым
            # именем оставляем больший файл в books, меньший → duplicate_paths.
            if not g.cleanup_only and g.books:
                _seen: dict = {}  # lower_name → CompilationBook (наибольший)
                _name_dups: list = []
                for _b in g.books:
                    _key = _b.abs_path.name.lower()
                    if _key not in _seen:
                        _seen[_key] = _b
                    else:
                        _prev = _seen[_key]
                        try:
                            _sz_b    = _b.abs_path.stat().st_size
                            _sz_prev = _prev.abs_path.stat().st_size
                        except OSError:
                            _sz_b = _sz_prev = 0
                        if _sz_b >= _sz_prev:
                            _name_dups.append(_prev)
                            _seen[_key] = _b
                        else:
                            _name_dups.append(_b)
                if _name_dups:
                    if g.duplicate_paths is None:
                        g.duplicate_paths = []
                    for _nd in _name_dups:
                        g.duplicate_paths.append(_nd.abs_path)
                    g.books = [_b for _b in g.books if _b not in _name_dups]
            groups.append(g)
            if on_group:
                on_group(g)

        for (_, _), recs in buckets.items():
            if len(recs) < 2:
                continue
            author = recs[0].proposed_author.strip()

            # Определяем имя серии для группы.
            # Если все записи принадлежат одной подсерии — используем полный путь
            # (Root\Sub), чтобы сохранить имя и порядковый номер подсерии.
            # Если записи из разных подсерий (объединённая группа вида Серия N\X +
            # Серия M\Y) — используем очищенный корень.
            _all_subs = {r.proposed_series.strip().split('\\', 1)[1]
                         for r in recs if '\\' in r.proposed_series}
            _s0 = recs[0].proposed_series.strip()
            # Первая запись с подсерией ('\\') — используем её как источник серии
            # если recs[0] оказался плоской записью (folder_dataset без arc-детекции).
            _s_with_sub = next((r.proposed_series.strip() for r in recs
                                if '\\' in r.proposed_series), None)
            # Если в группе есть записи без подсерии — их название задаёт зонтичную серию.
            # Пример: "Не ГГ" (тт.1,4) + "Не ГГ\Курсанты" (тт.2-3) → серия = "Не ГГ".
            _plain = next((r.proposed_series.strip() for r in recs
                           if '\\' not in r.proposed_series), None)
            if _plain:
                series = _plain
            elif len(_all_subs) == 1 and _s_with_sub:
                # Единственная подсерия и нет плоских книг — берём полный путь
                series = _s_with_sub
            else:
                if '\\' in _s0:
                    _root = _s0.split('\\')[0].strip()
                    series = re.sub(r'\s+\d{1,4}\s*$', '', _root).strip() or _root
                else:
                    series = _s0

            books = [self._make_book(rec, work_dir) for rec in recs]
            duplicate_paths: List[Path] = []

            # --- Если все книги в группе — уже предкомпиляции с разными series_number,
            # это отдельные скомпилированные подсерии — не объединяем их дальше.
            # Пример: "Вселенная Сафари 2. Егерь (Трилогия)" + "Вселенная Сафари 3.
            # Чёрный археолог (Трилогия)" → оба уже готовы, merge не нужен.
            # volume_label может быть ещё "2"/"3" (до контекстной коррекции),
            # поэтому проверяем через _precompiled_range напрямую.
            _precomp_ranges = {id(b): self._precompiled_range(b, series) for b in books}
            _all_precompiled = all(hi > 0 for lo, hi in _precomp_ranges.values())
            if _all_precompiled and len(books) >= 2:
                _sn_vals = [b.record.series_number or '' for b in books]
                # Только если series_number — простые целые числа (arc-номера: 2, 3…),
                # а не диапазоны ("1-3") и не пустые значения.
                _plain_ints = all(re.match(r'^\d+$', sn) for sn in _sn_vals)
                if _plain_ints and len(set(_sn_vals)) == len(_sn_vals):
                    # Дополнительная проверка: если все arc-позиции одноточечные (lo==hi),
                    # это отдельные arc'и родительской серии — их нужно компилировать вместе.
                    # Пропускаем только если хотя бы один имеет многокнижный диапазон (lo<hi).
                    _any_multi = any(lo < hi for lo, hi in _precomp_ranges.values())
                    if _any_multi:
                        continue  # пропускаем — подсерии с внутренними диапазонами

            # --- Контекстная коррекция: книги с сервисным словом (Трилогия…)
            # без явного series_number, которые не были опознаны _precompiled_range
            # как предкомпиляция из-за отсутствия связи с именем серии в stem.
            # Если в группе уже есть отдельные тома 1..N (N = число из слова),
            # принудительно задаём series_number='1-N' и пересчитываем sort_key.
            _known_positions = {
                (b.sort_key[2] if b.sort_key[0] == 0 and b.sort_key[1] == 0 else b.sort_key[1])
                for b in books if b.sort_key[0] == 0
            } - {0}
            _SWORDS_IDX = {kw.lower(): idx for idx, kw in enumerate(self._SERIES_WORDS) if kw}
            _SWORDS_PAT = re.compile(
                '|'.join(re.escape(kw) for kw in _SWORDS_IDX),
                re.IGNORECASE | re.UNICODE,
            )
            for book in books:
                # Уже опознанная предкомпиляция — пропускаем
                if self._RANGE_NUM_RE.match(book.volume_label or ''):
                    continue
                stem_title = (book.abs_path.stem + ' ' + (book.record.file_title or '')).lower()
                m = _SWORDS_PAT.search(stem_title)
                if not m:
                    continue
                n_vols = _SWORDS_IDX[m.group(0).lower()]
                # Условие: все тома 1..N присутствуют среди других книг группы
                if set(range(1, n_vols + 1)).issubset(_known_positions):
                    book.record.series_number = f'1-{n_vols}'
                    # Пересчитываем через _precompiled_range
                    lo, hi = self._precompiled_range(book, series)
                    if hi > lo:
                        book.sort_key = (0, lo, 0, 0)
                        book.volume_label = f'{lo}-{hi}'
                        book.sort_source = 'filename_range'
                        book.order_ambiguous = False

            # --- Коррекция «Сборника»: книга с «Сборник» в имени без подсерии.
            # Читаем <annotation> сборника и сопоставляем имена всех дуг группы
            # с её текстом — так один сборник может покрывать несколько подсерий.
            # Все совпавшие дуги: отдельные книги → duplicate_paths.
            _SBORNIK_RE = re.compile(r'\bсборник\b', re.IGNORECASE)
            # Карта дуг: arc_num → {'name': str, 'books': [CompilationBook]}
            # Все книги с подсерией в proposed_series (содержат '\\').
            _arc_map2: dict = {}
            for _b in books:
                if '\\' not in (_b.record.proposed_series or ''):
                    continue
                _arc_num = _b.sort_key[1] if _b.sort_key[0] == 0 and _b.sort_key[1] else 0
                if not _arc_num:
                    continue
                if _arc_num not in _arc_map2:
                    _sub = (_b.record.proposed_series or '').split('\\')
                    _arc_part = _sub[1].strip() if len(_sub) >= 2 else ''
                    _arc_name = re.sub(r'^\d+\.\s*', '', _arc_part).lower().replace('ё', 'е')
                    _arc_map2[_arc_num] = {'name': _arc_name, 'books': []}
                _arc_map2[_arc_num]['books'].append(_b)

            if _arc_map2:
                for _book in list(books):
                    if self._RANGE_NUM_RE.match(_book.volume_label or ''):
                        continue
                    if not _SBORNIK_RE.search(_book.abs_path.stem):
                        continue
                    if '\\' in (_book.record.proposed_series or ''):
                        continue
                    # Приоритет: аннотация из файла, запасной — имя файла
                    _search_text = self._extract_annotation_text(_book)
                    if not _search_text:
                        _search_text = _book.abs_path.stem.lower().replace('ё', 'е')
                    # Ищем ВСЕ совпавшие дуги:
                    # 1) по названию дуги (≥2 слов совпадают)
                    # 2) по названиям книг дуги (хотя бы одна книга упомянута)
                    _matched_arcs = []
                    for _arc_num, _arc_info in _arc_map2.items():
                        # Критерий 1: название дуги
                        _words = [w for w in _arc_info['name'].split() if len(w) >= 3]
                        _score = sum(1 for w in _words if w in _search_text)
                        if _score >= 2:
                            _matched_arcs.append(_arc_num)
                            continue
                        # Критерий 2: хотя бы одна книга дуги упомянута в тексте
                        for _ab in _arc_info['books']:
                            _btitle = (_ab.record.file_title or _ab.abs_path.stem).lower().replace('ё', 'е')
                            _btitle = re.sub(r'^\d+\.\s*', '', _btitle).strip()
                            _bwords = [w for w in _btitle.split() if len(w) >= 4]
                            if _bwords and sum(1 for w in _bwords if w in _search_text) >= min(2, len(_bwords)):
                                _matched_arcs.append(_arc_num)
                                break
                    if not _matched_arcs:
                        continue
                    _matched_arcs.sort()
                    # Сборник занимает позицию наименьшей дуги
                    _book.sort_key = (0, _matched_arcs[0], 0, 0)
                    _book.volume_label = str(_matched_arcs[0])
                    _book.sort_source = 'inferred_sbornik'
                    _book.order_ambiguous = False
                    # Все книги совпавших дуг → дубликаты Сборника.
                    # Сборник эмитируется как cleanup_only группа и убирается из books,
                    # чтобы оставшиеся дуги обрабатывались независимо.
                    _all_arc_books_to_remove: set = set()
                    _sbornik_dup_paths = []
                    for _arc_num in _matched_arcs:
                        for _arc_book in _arc_map2[_arc_num]['books']:
                            _sbornik_dup_paths.append(_arc_book.abs_path)
                            _all_arc_books_to_remove.add(_arc_book.abs_path)
                    books = [b for b in books if b.abs_path not in _all_arc_books_to_remove]
                    _arc_range = (
                        f'{_matched_arcs[0]}-{_matched_arcs[-1]}'
                        if len(_matched_arcs) > 1 else str(_matched_arcs[0])
                    )
                    _emit(CompilationGroup(
                        author=author, series=series, books=[],
                        order_determined=True,
                        volume_range=_arc_range,
                        duplicate_paths=_sbornik_dup_paths,
                        kept_paths=[_book.abs_path],
                        cleanup_only=True,
                    ))
                    books = [b for b in books if b.abs_path != _book.abs_path]

            # --- Групповая коррекция: если большинство книг группы используют
            # series_number из метаданных, то книги где filename перебил метаданные
            # исправляем обратно по мета. Это решает случай когда файлы пронумерованы
            # "1. Книга 1", "2. Книга 2", "3. Книга 3" но book 3 на самом деле том 4.
            _sn_meta_books = [b for b in books if b.sort_source == 'series_number'
                              and b.sort_key[0] == 0 and b.sort_key[1] > 0]
            _sn_file_books = [b for b in books if b.sort_source == 'filename'
                              and b.sort_key[0] == 0 and b.record.series_number
                              and re.match(r'^\d+$', b.record.series_number.strip())]
            if len(_sn_meta_books) > len(_sn_file_books) and _sn_file_books:
                for book in _sn_file_books:
                    meta_n = int(book.record.series_number.strip())
                    if meta_n < 1900 and meta_n > 0 and meta_n != book.sort_key[1]:
                        book.sort_key = (0, meta_n, 0, book.sort_key[3])
                        book.sort_source = 'series_number'
                        book.volume_label = str(meta_n)
                        book.order_ambiguous = False

            # --- Сопоставление многосоставных заголовков с книгами группы ----------
            # Внешние компиляции (e.g. «Спартанец. Великий царь. Удар в сердце»)
            # не имеют <sequence number> и «Том N» — только заголовки разделов.
            # Сопоставляем части многосоставного file_title с file_title других книг
            # в группе: если ≥2 совпадений → задаём series_number диапазоном позиций.
            _pos_to_title: Dict[int, str] = {}
            for b in books:
                if b.sort_key[0] == 0 and b.sort_key[1] > 0 and not b.order_ambiguous:
                    t = (b.record.file_title or '').strip()
                    if t:
                        _pos_to_title[b.sort_key[1]] = t.lower().replace('ё', 'е')
            if _pos_to_title:
                for book in books:
                    if self._RANGE_NUM_RE.match(book.volume_label or ''):
                        continue  # уже распознана как предкомпиляция
                    # Пропускаем файлы с ведущим числом в стеме — это обычная книга с позицией,
                    # а не внешняя компиляция. «4_Спартанец. Племя равных» — том 4, не сборник.
                    _stem_chk = book.abs_path.stem
                    if re.match(r'^\d', _stem_chk):
                        continue
                    multi = (book.record.file_title or '').strip().lower().replace('ё', 'е')
                    if not multi or len(re.findall(r'\.\s+[а-яёa-z]', multi, re.IGNORECASE)) < 1:
                        continue
                    own_pos = book.sort_key[1] if book.sort_key[0] == 0 else None
                    # Если заголовок этого файла совпадает с заголовком его собственной позиции
                    # (т.е. все книги группы имеют одинаковый file_title) — это обычный том,
                    # а не внешняя компиляция других книг.
                    if own_pos and _pos_to_title.get(own_pos, '') == multi:
                        continue
                    matched = sorted(
                        pos for pos, t in _pos_to_title.items()
                        if t and t in multi and pos != own_pos
                    )
                    if len(matched) < 2:
                        continue
                    lo_m, hi_m = matched[0], matched[-1]
                    # Требуем непрерывный диапазон: все позиции от lo до hi должны присутствовать
                    # среди совпавших. «1 и 4» без 2 и 3 — не трилогия.
                    if set(matched) != set(range(lo_m, hi_m + 1)):
                        continue
                    book.record.series_number = f'{lo_m}-{hi_m}'
                    lo2, hi2 = self._precompiled_range(book, series)
                    if hi2 > lo2:
                        book.sort_key = (0, lo2, 0, 0)
                        book.volume_label = f'{lo2}-{hi2}'
                        book.sort_source = 'filename_range'
                        book.order_ambiguous = False

            # --- Фильтр 1: обработка заранее скомпилированных файлов ----------
            # Признак: stem/title содержит сервисное слово (Трилогия …) или
            # series_number — диапазон вида "1-3".
            #
            # Три состояния:
            #   1. АКТУАЛЬНА (best_count >= regular_count): компиляция уже
            #      сделана — сохраняем предкомпиляцию, отдельные тома на удаление.
            #   2. ЧАСТИЧНО УСТАРЕЛА (best_count < regular_count, но предкомпиляция
            #      содержит тома которых нет отдельно, например том 1): включаем
            #      предкомпиляцию как источник + добавляем недостающие тома.
            #      Тома, уже покрытые предкомпиляцией, помечаем на удаление.
            #   3. ПОЛНОСТЬЮ УСТАРЕЛА (все тома предкомпиляции есть и по отдельности):
            #      удаляем предкомпиляцию, компилируем из отдельных томов.
            precompiled: List[Tuple[CompilationBook, int, int]] = []  # (book, lo, hi)
            regular_books: List[CompilationBook] = []
            for book in books:
                # Сначала проверяем inner_precompilation (EBLO-скомпилированная подсерия
                # «ч. N в K книгах»). _precompiled_range не знает этот паттерн,
                # поэтому обрабатываем до его вызова.
                if book.sort_source == 'inner_precompilation':
                    _rng_m = re.match(r'^(\d+)-(\d+)$', book.volume_label or '')
                    if _rng_m:
                        lo, hi = int(_rng_m.group(1)), int(_rng_m.group(2))
                        # sort_source оставляем 'inner_precompilation' — _best_is_inner
                        # проверяет именно его, чтобы не путать с обычными предкомпиляциями.
                        book.order_ambiguous = False
                        precompiled.append((book, lo, hi))
                        continue
                lo, hi = self._precompiled_range(book, series)
                if hi > lo:
                    # Обновляем sort_key и volume_label по реальному диапазону файла.
                    # Без этого "1-2. Название.fb2" получает sk=(0,2,0) vl='2' вместо
                    # sk=(0,1,0) vl='1-2', и _split_into_consecutive_runs считает
                    # что "1-2" и "3-4" не идут подряд (lo=4 ≠ hi=2+1).
                    # Для подсерий без числа в корне (parent_num=0) отдельные книги
                    # используют (0, 0, sub_ordinal, 0). Ставим предкомпиляцию в ту же
                    # плоскость, иначе она сортируется после всех (0 < lo).
                    _pre_series_root = series.split('\\')[0].strip() if '\\' in series else ''
                    _pre_root_has_num = bool(re.search(r'\d+\s*$', _pre_series_root))
                    if '\\' in series and not _pre_root_has_num:
                        book.sort_key = (0, 0, lo, 0)
                    else:
                        book.sort_key = (0, lo, 0, 0)
                    book.volume_label = f'{lo}-{hi}'
                    book.sort_source = 'filename_range'
                    book.order_ambiguous = False
                    precompiled.append((book, lo, hi))
                else:
                    regular_books.append(book)

            if precompiled:
                regular_count = len(regular_books)
                # Берём предкомпиляцию с максимальным охватом
                best_pre, best_lo, best_hi = max(precompiled, key=lambda t: t[2] - t[1])
                best_count = best_hi - best_lo + 1

                # Фаза 1: дедуплицировать контент-дубли (файлы с одинаковым диапазоном).
                # Для каждой группы (lo,hi): оставляем best_pre если он в группе, иначе первый.
                # Остальные → duplicate_paths. Это предотвращает взаимное покрытие:
                # Орёл: [1-2 Саймон] + [1-2 Скэрроу.] → Скэрроу. → дубль, Саймон остаётся.
                # Кожевников: [1-3 Олег] + [1-3 "."] → обе разные → одна остаётся.
                _by_range: dict = {}
                for entry in precompiled:
                    b, lo, hi = entry
                    _by_range.setdefault((lo, hi), []).append(entry)
                precompiled_unique: List[Tuple] = []
                for rng, entries in _by_range.items():
                    if len(entries) == 1:
                        precompiled_unique.append(entries[0])
                        continue
                    # Среди нескольких файлов с одинаковым диапазоном:
                    # сохраняем best_pre (если в группе) или первый по порядку
                    winner = next((e for e in entries if e[0] is best_pre), entries[0])
                    precompiled_unique.append(winner)
                    for e in entries:
                        if e is not winner:
                            duplicate_paths.append(e[0].abs_path)
                precompiled = precompiled_unique

                # Фаза 2: range coverage — проверяем только файлы с разными диапазонами.
                # Прочие предкомпиляции — на удаление ТОЛЬКО если их диапазон полностью
                # покрыт хотя бы одной другой (best или иной).
                other_precompiled: List[Tuple] = []
                for entry in precompiled:
                    book, lo, hi = entry
                    if book is best_pre:
                        continue
                    # Arc-point pre-compilations (lo==hi) не дедуплицируем друг против друга:
                    # два файла с одинаковым arc-position могут покрывать РАЗНЫЙ внутренний
                    # контент (например, Брия 1 кн.1-2 и Брия 1 кн.3-4 оба имеют arc-pos 1).
                    # Для подсерий (is_subseries) нужна проверка series_number — иначе
                    # «Дилогия арк 3» (lo=1,hi=2) ошибочно покроется «Тетралогией арк 2»
                    # (lo=1,hi=4), хотя это разные арки одной родительской серии.
                    _is_arc_point = (lo == hi)
                    _book_sn = (book.record.series_number or '').strip()
                    _is_subseries_bucket = '\\' in series
                    covered_by_any = (not _is_arc_point) and any(
                        (o_lo <= lo and hi <= o_hi)
                        and (not _is_subseries_bucket
                             or (o_book.record.series_number or '').strip() == _book_sn)
                        for (o_book, o_lo, o_hi) in precompiled
                        if o_book is not book
                    )
                    if covered_by_any:
                        duplicate_paths.append(book.abs_path)
                    else:
                        # Не полностью покрыт ни одной другой предкомпиляцией → источник
                        other_precompiled.append(entry)

                # АКТУАЛЬНА только если ВСЕ обычные тома входят в диапазон предкомпиляции
                # И нет других непокрытых предкомпиляций (other_precompiled пуст).
                # Пример: предкомпиляция 1-3 + обычный том 4 → НЕ актуальна (том 4 не покрыт).
                # Пример: предкомпиляция 1-2 + предкомпиляция 3-4 → НЕ актуальна (нужно объединить).
                _best_is_inner = best_pre.sort_source == 'inner_precompilation'
                _inner_arc_pos = best_pre.sort_key[1] if _best_is_inner else None

                def _vol_num_for_check(b: 'CompilationBook') -> Optional[int]:
                    if b.sort_key and b.sort_key[0] == 0:
                        if _best_is_inner:
                            # Внутренняя предкомпиляция: сравниваем по sk[2] (подпозиция),
                            # только если книга находится в той же arc-позиции.
                            if b.sort_key[1] == _inner_arc_pos and b.sort_key[2] != 0:
                                return b.sort_key[2]
                            return None
                        # Для подсерий без числа в корне позиция хранится в sort_key[2]
                        return b.sort_key[2] if b.sort_key[1] == 0 else b.sort_key[1]
                    return None

                all_covered = (
                    not other_precompiled and
                    (all(
                        (n := _vol_num_for_check(r)) is not None and best_lo <= n <= best_hi
                        for r in regular_books
                    ) if regular_books else True)
                )

                if all_covered:
                    # 1. АКТУАЛЬНА — компиляция уже сделана, новая не нужна.
                    # Отдельные тома, уже покрытые компиляцией, — на удаление.
                    for book in regular_books:
                        duplicate_paths.append(book.abs_path)
                    if duplicate_paths:
                        # Есть что удалить — сообщаем через cleanup_only группу
                        _emit(CompilationGroup(
                            author=author,
                            series=series,
                            books=[],
                            order_determined=True,
                            volume_range=f'{best_lo}-{best_hi}' if best_lo != best_hi else str(best_lo),
                            duplicate_paths=duplicate_paths,
                            kept_paths=[best_pre.abs_path],
                            cleanup_only=True,
                        ))
                    continue
                else:
                    # Определяем, какие тома предкомпиляции присутствуют отдельно
                    def _vol_num(b: CompilationBook) -> Optional[int]:
                        """Номер тома из sort_key если источник надёжен."""
                        if b.sort_key and b.sort_key[0] == 0:
                            if _best_is_inner:
                                if b.sort_key[1] == _inner_arc_pos and b.sort_key[2] != 0:
                                    return b.sort_key[2]
                                return None
                            # Для подсерий без числа в корне позиция в sort_key[2]
                            return b.sort_key[2] if b.sort_key[1] == 0 else b.sort_key[1]
                        return None

                    # Если regular_books пуст — нечем покрывать тома по отдельности.
                    # all(...) при пустом range даёт vacuous True — это неверно:
                    # «0 книг покрывают 4 тома» не означает «покрыты».
                    pre_covered_individually = bool(regular_books) and all(
                        any(_vol_num(r) == v for r in regular_books)
                        for v in range(best_lo, best_hi + 1)
                    )

                    if pre_covered_individually:
                        # 3. ПОЛНОСТЬЮ УСТАРЕЛА — все её тома есть по отдельности
                        duplicate_paths.append(best_pre.abs_path)
                        books = regular_books
                    else:
                        # 2. ЧАСТИЧНО УСТАРЕЛА
                        covered_individually = [
                            r for r in regular_books
                            if (n := _vol_num(r)) is not None and best_lo <= n <= best_hi
                        ]
                        remaining = [r for r in regular_books if r not in covered_individually]

                        # Проверяем: продолжают ли оставшиеся книги диапазон предкомпиляции?
                        # Пример: предкомп [1-4] + книги [5,6,7] → консекутивны (5 = 4+1)
                        #          → компилируем вместе → один файл 1-7
                        # Пример: предкомп [1-3] + книга [7] → НЕ консекутивны
                        #          → cleanup_only (удаляем покрытые) + книга [7] standalone
                        remaining_known_positions = [
                            n for r in remaining if (n := _vol_num(r)) is not None
                        ]
                        remaining_has_unknown = any(_vol_num(r) is None for r in remaining)
                        remaining_extends_pre = (
                            remaining_known_positions and
                            min(remaining_known_positions) == best_hi + 1
                        )

                        if covered_individually and not remaining_extends_pre and not remaining_has_unknown and not other_precompiled:
                            # Оставшиеся книги не продолжают предкомпиляцию и нет книг
                            # с неизвестной позицией → cleanup_only: предкомп остаётся,
                            # покрытые тома — на удаление; оставшиеся обрабатываются отдельно.
                            cov_dup_paths = list(duplicate_paths) + [r.abs_path for r in covered_individually]
                            _emit(CompilationGroup(
                                author=author, series=series, books=[],
                                order_determined=True,
                                volume_range=(
                                    f'{best_lo}-{best_hi}' if best_lo != best_hi else str(best_lo)
                                ),
                                duplicate_paths=cov_dup_paths,
                                kept_paths=[best_pre.abs_path],
                                cleanup_only=True,
                            ))
                            duplicate_paths = []
                            books = remaining
                        else:
                            # Оставшиеся книги продолжают серию (или есть книги без номера)
                            # → включаем предкомпиляцию как источник, компилируем вместе.
                            for r in covered_individually:
                                duplicate_paths.append(r.abs_path)
                            books = [best_pre] + remaining

                    # Добавляем прочие предкомпиляции с непересекающимися диапазонами
                    # как дополнительные источники (они уже НЕ в duplicate_paths).
                    for other_book, other_lo, other_hi in other_precompiled:
                        # Проверяем: все тома этой предкомпиляции уже есть отдельно?
                        other_fully_individual = bool(regular_books) and all(
                            any(_vol_num(r) == v for r in regular_books)
                            for v in range(other_lo, other_hi + 1)
                        )
                        if other_fully_individual:
                            duplicate_paths.append(other_book.abs_path)
                        else:
                            books.append(other_book)
                            # Индивидуальные книги в диапазоне [other_lo..other_hi]
                            # дублируют контент предкомпиляции → помечаем к удалению.
                            # Пример: «Щегол 6-11» + individual 6,7,8,9,10 →
                            # individual 6-10 в дубли (книга 11 есть только в предкомп.).
                            _cov = [r for r in list(books)
                                    if r is not other_book
                                    and (n := _vol_num(r)) is not None
                                    and other_lo <= n <= other_hi]
                            for r in _cov:
                                duplicate_paths.append(r.abs_path)
                                try:
                                    books.remove(r)
                                except ValueError:
                                    pass
            else:
                books = regular_books

            # --- Фильтр 1.5: приоритет доминирующей папки --------------------
            # Если большинство томов группы сосредоточено в одной папке,
            # файлы из неё получают приоритет: дубли тех же томов из других
            # папок помечаются к удалению. Тома, которых нет в доминирующей
            # папке, берутся из других папок как обычно.
            _eff_vol = self._book_eff_pos

            if books:
                # Считаем сколько уникальных позиций томов покрывает каждая папка
                from collections import Counter as _Counter
                folder_vol_sets: Dict[str, set] = {}
                for b in books:
                    folder = str(b.abs_path.parent)
                    folder_vol_sets.setdefault(folder, set())
                    rng_m = re.match(r'^(\d+)\s*[-–—]\s*(\d+)$', b.volume_label or '')
                    if rng_m:
                        # Предкомпиляция — добавляем весь диапазон, не только lo
                        lo_r, hi_r = int(rng_m.group(1)), int(rng_m.group(2))
                        folder_vol_sets[folder].update(range(lo_r, hi_r + 1))
                    else:
                        ev = _eff_vol(b)
                        if ev:
                            folder_vol_sets[folder].add(ev)
                # Считаем также число файлов в каждой папке (тайбрейкер при равных томах)
                folder_file_counts: Dict[str, int] = {}
                for b in books:
                    folder_file_counts[str(b.abs_path.parent)] = \
                        folder_file_counts.get(str(b.abs_path.parent), 0) + 1
                if len(folder_vol_sets) > 1:
                    dominant_folder = max(
                        folder_vol_sets,
                        key=lambda f: (len(folder_vol_sets[f]), folder_file_counts.get(f, 0))
                    )
                    dominant_vols = folder_vol_sets[dominant_folder]
                    if dominant_vols:
                        new_books = []
                        for b in books:
                            folder = str(b.abs_path.parent)
                            vol = _eff_vol(b) or None
                            # Предкомпиляция с диапазоном N-M, у которой hi > max(dominant_vols):
                            # содержит уникальный контент за пределами доминирующей папки.
                            rng_pre = re.match(r'^(\d+)\s*[-–—]\s*(\d+)$', b.volume_label or '')
                            has_unique = rng_pre and any(
                                v not in dominant_vols
                                for v in range(int(rng_pre.group(1)), int(rng_pre.group(2)) + 1)
                            )
                            if folder != dominant_folder and vol and vol in dominant_vols and not has_unique:
                                duplicate_paths.append(b.abs_path)
                            else:
                                new_books.append(b)
                        books = new_books

            # --- Фильтр 2: дедупликация по title (нормализованному) ----------
            # Из дублей оставляем более позднюю редакцию (по году в имени файла),
            # при равенстве — первый по алфавиту путь (детерминированный выбор).
            def _title_dedup_order(b: CompilationBook):
                year_m = re.search(r'[-–\s](\d{4})\b', b.abs_path.stem)
                year = int(year_m.group(1)) if year_m else 0
                # Предкомпиляция с бо́льшим диапазоном побеждает меньшую:
                # «1-16» должна выжить против «1-14» при одинаковом title_key.
                rng_m = re.match(r'^(\d+)\s*[-–—]\s*(\d+)$', b.volume_label or '')
                range_hi = int(rng_m.group(2)) if rng_m else 0
                return (-year, -range_hi, str(b.abs_path))

            seen_titles: Dict[str, CompilationBook] = {}
            for book in sorted(books, key=_title_dedup_order):
                # Если file_title совпадает с именем серии — он не несёт информации
                # о конкретном томе, используем stem файла как более информативный.
                raw_title = book.record.file_title or book.abs_path.stem
                if _norm_key(raw_title) == _norm_key(series):
                    raw_title = book.abs_path.stem
                title_key = self._normalize_title_key(raw_title, series)
                # Для книг с известной позицией тома (level-0) добавляем позицию к ключу,
                # чтобы не дедуплицировать разные тома с одинаковым названием.
                # Пример: «Маршал 1-5» и «Маршал 6-9» оба имеют file_title="Маршал" —
                # без этой защиты они бы считались дублями.
                if book.sort_key[0] == 0:
                    title_key = f"{title_key}\x00{book.sort_key[1]}"
                if title_key not in seen_titles:
                    seen_titles[title_key] = book
                else:
                    duplicate_paths.append(book.abs_path)
            books = list(seen_titles.values())

            # --- Фильтр 3: дедупликация по позиции тома ----------------------
            # Если после title-дедупликации остались книги с одинаковым sort_key
            # на уровнях 0 (series_number) или 1 (filename number), оставляем
            # первую по алфавиту, остальные помечаем как дубликаты.
            books = self._dedup_by_position(books, duplicate_paths)

            # --- Фильтр 4: дедупликация по содержимому -----------------------
            # Если два файла начинаются с практически одинакового текста
            # (SequenceMatcher ratio ≥ 0.85 на первых 2000 символах), один
            # из них — незарегистрированная предкомпиляция или дубликат с
            # другим форматированием. Оставляем файл с более детальной позицией
            # в серии (ненулевой subseries-компонент), иначе — больший по размеру.
            books = self._dedup_by_content(books, duplicate_paths)

            if len(books) < 2:
                # Если после dedup остался один файл, но есть дубликаты — создаём cleanup_only.
                # Пример: два файла с одинаковым sort_key (01. vs 1.) — dedup оставляет один,
                # другой попадает в duplicate_paths, но без группы они не удаляются.
                if duplicate_paths and books:
                    _emit(CompilationGroup(
                        author=author,
                        series=series,
                        books=[],
                        order_determined=True,
                        volume_range='',
                        duplicate_paths=duplicate_paths,
                        kept_paths=[books[0].abs_path],
                        cleanup_only=True,
                    ))
                continue
            books_sorted, order_determined, alphabetical_order = self._sort_books(books)

            if alphabetical_order:
                # Порядок по названию — нет номеров томов, пропуски неприменимы
                volume_range = ''
                _emit(CompilationGroup(
                    author=author,
                    series=series,
                    books=books_sorted,
                    order_determined=order_determined,
                    volume_range=volume_range,
                    duplicate_paths=duplicate_paths,
                    alphabetical_order=True,
                ))
            else:
                # Разбиваем числовые и нечисловые книги независимо:
                #   • числовые (level-0) → непрерывные подгруппы, пропуски не допускаются
                #   • нечисловые (даты / unknown) → отдельная группа «компиляция романов»
                # Это предотвращает ложное смешение диапазона (например, sn=1 + sn=3 + дата
                # дало бы volume_range='1-3' → «Трилогия», хотя тома 1 и 3 не идут подряд).
                numeric = [b for b in books_sorted if b.sort_key[0] == 0]
                others  = [b for b in books_sorted if b.sort_key[0] != 0]

                # Эвристика «неопределённый = том 1»:
                # Если ровно один файл без номера тома (год/неизвестен),
                # а среди числовых нет тома 1 — считаем его первым томом.
                if (len(others) == 1
                        and numeric
                        and min(b.sort_key[1] for b in numeric if b.sort_key[0] == 0) >= 2):
                    lone = others[0]
                    lone.sort_key = (0, 1, 0, 0)
                    lone.sort_source = 'assumed_first'
                    lone.order_ambiguous = False
                    lone.volume_label = '1'
                    numeric = sorted(numeric + [lone], key=lambda b: b.sort_key)
                    others = []

                first_group = True  # для назначения duplicate_paths только один раз

                # ── Числовые книги: непрерывные блоки ──────────────────────
                valid_runs = [r for r in self._split_into_consecutive_runs(numeric) if len(r) >= 2]
                lone_numeric = [b for r in self._split_into_consecutive_runs(numeric) if len(r) < 2 for b in r]

                # Серия считается завершённой только если нет одиночных томов за пределами
                # этого рана. Наличие других ранов (напр. arc 13) не делает run {1,2,3}
                # незавершённым — каждый ран оценивается независимо.
                for run in valid_runs:
                    # Детектируем паттерн N.M (том.часть): если ВСЕ книги в run
                    # получили sort_source='dot_part', то volume_range = диапазон томов,
                    # а part_count = общее число частей (файлов).
                    all_dot_part = run and all(b.sort_source == 'dot_part' for b in run)
                    if all_dot_part:
                        toms = sorted({b.sort_key[1] for b in run})
                        run_range = f'{toms[0]}-{toms[-1]}' if len(toms) > 1 else str(toms[0])
                        run_part_count = len(run)
                    else:
                        run_range = self._compute_volume_range(run)
                        run_part_count = 0
                    _emit(CompilationGroup(
                        author=author,
                        series=series,
                        books=run,
                        order_determined=True,
                        volume_range=run_range,
                        duplicate_paths=duplicate_paths if first_group else [],
                        alphabetical_order=False,
                        part_count=run_part_count,
                        series_complete=not bool(lone_numeric),
                    ))
                    first_group = False

                # ── Нечисловые книги: компиляция по году / по названию ─────
                # Если нечисловых >= 2 — обычная группа
                # Если нечисловых < 2, но есть одиночные числовые книги — объединяем всё вместе.
                # ИСКЛЮЧЕНИЕ: precompiled книги (volume_label содержит диапазон "N-M") не
                # объединяем с нечисловыми — они уже содержат несколько томов и не являются
                # "одиночными" книгами в смысле серии.
                _RANGE_VL = re.compile(r'^\d+\s*[-–—]\s*\d+$')
                lone_regular = [b for b in lone_numeric if not _RANGE_VL.match(b.volume_label or '')]
                all_others = others
                if len(others) < 2 and lone_regular:
                    # Объединяем одиночные обычные (не precompiled) + нечисловые,
                    # НО только если lone_regular ровно один — иначе это несколько томов
                    # с явными номерами и пробелом между ними (например, тома 7 и 9 без 8):
                    # такие группы не компилируем.
                    if len(lone_regular) == 1:
                        all_others = sorted(lone_regular, key=lambda b: b.sort_key) + list(others)
                        lone_numeric = [b for b in lone_numeric if b not in lone_regular]

                if len(all_others) >= 2:
                    # Не компилируем если ни одна книга не имеет реального номера тома
                    # (sort_key[0] == 0). Год публикации и «не определён» — не основание
                    # для компиляции: порядок чтения неизвестен.
                    if not any(b.sort_key[0] == 0 for b in all_others):
                        continue
                    all_oth_ambig = all(b.order_ambiguous for b in all_others)
                    oth_sorted = sorted(all_others, key=lambda b: b.sort_key)
                    _emit(CompilationGroup(
                        author=author,
                        series=series,
                        books=oth_sorted,
                        order_determined=not any(b.order_ambiguous for b in all_others),
                        volume_range='',
                        duplicate_paths=duplicate_paths if first_group else [],
                        alphabetical_order=all_oth_ambig,
                    ))
                    first_group = False

        # ── POST-PASS: подавить compile-группы, полностью покрытые другой группой.
        #
        # Случай A (parent-child): серия малой группы — подсерия большой (prefix + '\\')
        #   + диапазон томов входит в диапазон большой.
        #   Пример: Рубеж\Сирийский рубеж (т. 5-8) ⊂ Рубеж (1-11).
        #
        # Случай B (content-hash дубль): разные серии одного автора с полным
        #   совпадением content_hash книг — одни и те же файлы под разными именами серий.
        #   Побеждает группа с «правильной» серией: предпочитаем подсерийный путь (\\),
        #   потом более длинное имя серии, потом большее число книг.
        #   Пример: «Покоряя небо» (папка-сборник) vs «Авиатор\Назад в СССР» (правильное).
        _compile_only = [g for g in groups if not g.cleanup_only]
        if len(_compile_only) > 1:
            def _vols(g):
                vs = [b.sort_key[1] for b in g.books
                      if b.sort_key and b.sort_key[0] == 0 and b.sort_key[1] > 0]
                return (min(vs), max(vs)) if vs else None

            def _hashes(g):
                return {b.record.content_hash for b in g.books if b.record.content_hash}

            def _series_quality(g):
                """Чем выше — тем «правильнее» серия. Выбираем победителя при hash-дубле."""
                has_sub  = 1 if '\\' in g.series else 0
                ser_len  = len(g.series)
                n_books  = len(g.books)
                return (has_sub, ser_len, n_books)

            def _nt(s):
                s = re.sub(r'\[.*?\]|\(.*?\)', '', (s or '').lower())
                s = s.replace('ё', 'е')
                return re.sub(r'\s+', ' ', s).strip()

            # Группируем по автору — сравниваем только внутри одного автора.
            from collections import defaultdict as _dd
            _by_author = _dd(list)
            for _g in _compile_only:
                _by_author[_g.author].append(_g)

            _suppressed: set = set()
            for _author_groups in _by_author.values():
                if len(_author_groups) < 2:
                    continue
                for _small in _author_groups:
                    if id(_small) in _suppressed:
                        continue
                    for _large in _author_groups:
                        if _large is _small or id(_large) in _suppressed:
                            continue

                        # ── Случай A: parent-child ──────────────────────────
                        if _small.series.startswith(_large.series + '\\'):
                            _sr, _lr = _vols(_small), _vols(_large)
                            if _sr and _lr and _sr[0] >= _lr[0] and _sr[1] <= _lr[1]:
                                _covered = True
                            else:
                                _sh, _lh = _hashes(_small), _hashes(_large)
                                _covered = bool(_sh) and bool(_lh) and _sh <= _lh
                            if _covered:
                                _small.cleanup_only = True
                                _small.duplicate_paths = [b.abs_path for b in _small.books]
                                _small.kept_paths = []
                                _small.books = []
                                _suppressed.add(id(_small))
                                break

                        # ── Случай B/C: разные серии одного автора ──────────
                        else:
                            _sh, _lh = _hashes(_small), _hashes(_large)
                            _covered = False

                            # B: content_hash включение — файлы идентичны побайтово
                            if _sh and _lh and _sh <= _lh:
                                if _sh < _lh or _series_quality(_small) < _series_quality(_large):
                                    _covered = True

                            # C: title-overlap — одни и те же книги под разными именами серий
                            # (хэши отличаются из-за разных метаданных/редакций).
                            if not _covered and _series_quality(_small) < _series_quality(_large):
                                _tl = {_nt(b.record.file_title) for b in _large.books}
                                _matches = sum(
                                    1 for b in _small.books
                                    if len(_nt(b.record.file_title)) > 8
                                    and _nt(b.record.file_title) in _tl
                                )
                                if _small.books and _matches / len(_small.books) >= 0.75:
                                    _covered = True

                            if _covered:
                                _small.cleanup_only = True
                                _small.duplicate_paths = [b.abs_path for b in _small.books]
                                _small.kept_paths = []
                                _small.books = []
                                _suppressed.add(id(_small))
                                break

        groups.sort(key=lambda g: (g.author.lower(), g.series.lower()))
        self._log(f"Найдено групп для компиляции: {len(groups)}")
        return groups

    def _make_book(self, rec, work_dir: Path) -> CompilationBook:
        """Создать CompilationBook из BookRecord."""
        abs_path = work_dir / rec.file_path
        sort_key, sort_source, ambiguous, volume_label = self._determine_sort_key(rec, abs_path)
        return CompilationBook(
            record=rec,
            abs_path=abs_path,
            sort_key=sort_key,
            sort_source=sort_source,
            order_ambiguous=ambiguous,
            volume_label=volume_label,
        )

    # ------------------------------------------------------------------
    # Вспомогательные методы фильтрации
    # ------------------------------------------------------------------

    # Regex для диапазонного series_number вида "1-3", "1–7"
    _RANGE_NUM_RE = re.compile(r'^\d+\s*[-–—]\s*\d+$')

    # Ключевые слова, указывающие на номер тома внутри названия
    # Порядок важен: более специфичные — первыми
    _VOLUME_KEYWORDS_RE = re.compile(
        r'(?:свиток|том|книга|часть|выпуск|арка|цикл|эпизод|volume|book|part|vol\.?)'
        r'\s*[.:-]?\s*(\d{1,4})\b',
        re.IGNORECASE | re.UNICODE,
    )

    # Римские цифры после ключевых слов тома: «Том I», «Том II», «Vol. IV» и т.п.
    _VOLUME_ROMAN_RE = re.compile(
        r'(?:свиток|том|книга|часть|выпуск|арка|цикл|эпизод|volume|book|part|vol\.?)'
        r'\s*[.:-]?\s*(M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))\b',
        re.IGNORECASE | re.UNICODE,
    )

    # Паттерн N.M в начале stem или после разделителя — том.часть (например «1.2_Название»)
    _DOT_PART_RE = re.compile(
        r'(?:^|[\s_\-])([1-9]\d{0,1})\.([1-9]\d{0,1})(?:[\s_\-.]|$)',
        re.UNICODE,
    )

    # Паттерн «Том N. Часть M» / «Vol N. Part M» — два файла одного тома.
    # Группы: (1) номер тома (арабский или римский), (2) номер части (арабский).
    _VOLUME_PART_RE = re.compile(
        r'(?:том|volume|vol\.?)\s*[.:-]?\s*'
        r'(M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})|\d{1,4})'
        r'\s*[.,;]?\s*'
        r'(?:часть|part|ч\.?)\s*[.:-]?\s*(\d{1,4})\b',
        re.IGNORECASE | re.UNICODE,
    )

    @staticmethod
    def _roman_to_int(s: str) -> Optional[int]:
        """Конвертировать римскую цифру в целое. Возвращает None если s пустая или невалидна."""
        s = s.upper().strip()
        if not s:
            return None
        vals = {'I': 1, 'V': 5, 'X': 10, 'L': 50, 'C': 100, 'D': 500, 'M': 1000}
        result = 0
        prev = 0
        for ch in reversed(s):
            if ch not in vals:
                return None
            v = vals[ch]
            result += v if v >= prev else -v
            prev = v
        return result if result > 0 else None

    @classmethod
    def _extract_volume_part(cls, title: str, stem: str) -> Optional[Tuple[int, int]]:
        """Извлечь (том, часть) из паттерна «Том N. Часть M» / «Vol N. Part M».

        Возвращает (volume, part) или None если паттерн не найден.
        volume — целое число тома, part — номер части (1, 2, …).
        """
        for text in (title, stem):
            if not text:
                continue
            text_norm = unicodedata.normalize('NFKC', text)
            m = cls._VOLUME_PART_RE.search(text_norm)
            if m:
                vol_str, part_str = m.group(1), m.group(2)
                # vol_str может быть арабским или римским числом
                if vol_str.isdigit():
                    vol = int(vol_str)
                else:
                    vol = cls._roman_to_int(vol_str)
                if vol and 1 <= vol <= 100:
                    part = int(part_str)
                    if 1 <= part <= 20:
                        return vol, part
        return None

    @classmethod
    def _extract_dot_part(cls, stem: str, title: str) -> Optional[Tuple[int, int]]:
        """Извлечь (том, часть) из паттерна N.M в stem или title.

        Распознаёт: «1.2_Название», «Расходники 2.3», «2.1 Название» и т.п.
        Возвращает (том, часть) или None.
        Ограничения: N и M от 1 до 19 (исключаем годы и ISBN).
        """
        for text in (stem, title):
            if not text:
                continue
            m = cls._DOT_PART_RE.search(text)
            if m:
                vol, part = int(m.group(1)), int(m.group(2))
                if 1 <= vol <= 19 and 1 <= part <= 19:
                    return vol, part
        return None

    @classmethod
    def _extract_inline_volume_number(cls, title: str, stem: str) -> Optional[int]:
        """Извлечь номер тома из ключевых слов внутри названия.

        Ищет паттерны «Свиток 1», «Том 3», «Книга 2», «Часть 4» и т.п.,
        а также римские цифры: «Том I», «Том II», «Vol. IV».
        Возвращает число или None, если паттерн не найден.

        Проверяет как file_title, так и stem файла.
        """
        # «Книга N+M» / «Том N-M» — арифметическое выражение после ключевого слова.
        # Пример: «Книга 12+1» → 13, «Том 11+2» → 13.
        # Поддерживаем только «+» (прибавка) и «-» (вычитание) с малыми значениями.
        _KW_EXPR_RE = re.compile(
            r'(?:свиток|том|книга|часть|выпуск|арка|цикл|эпизод|volume|book|part|vol\.?)'
            r'\s*[.:-]?\s*(\d{1,4})\s*([+\-])\s*(\d{1,2})\b',
            re.IGNORECASE | re.UNICODE,
        )
        for text in (title, stem):
            if not text:
                continue
            # Нормализовать Unicode-символы римских цифр в ASCII: Ⅻ → XII, Ⅰ → I и т.п.
            text_norm = unicodedata.normalize('NFKC', text)
            # Сначала пробуем арифметическое выражение: «Книга 12+1» → 13
            m = _KW_EXPR_RE.search(text_norm)
            if m:
                base, op, delta = int(m.group(1)), m.group(2), int(m.group(3))
                result = base + delta if op == '+' else base - delta
                if 1 <= result <= 500:
                    return result
            m = cls._VOLUME_KEYWORDS_RE.search(text_norm)
            if m:
                return int(m.group(1))
            m = cls._VOLUME_ROMAN_RE.search(text_norm)
            if m:
                n = cls._roman_to_int(m.group(1))
                if n:
                    return n
        return None

    def _precompiled_range(self, book: CompilationBook, series: str) -> Tuple[int, int]:
        """Определить диапазон томов, охватываемых предкомпилированным файлом.

        Возвращает (lo, hi) где lo и hi — первый и последний тома включительно.
        Если файл не является предкомпиляцией, возвращает (0, 0).

        Критерии определения предкомпиляции:
        1. series_number — диапазон вида "1-3": возвращает (1, 3).
        2. Диапазон "N-M" в stem/title с привязкой к серии.
        3. stem/title содержит сервисное слово (Трилогия → 3 тома) — lo=1, hi=count.
        """
        series_lower = series.lower()
        series_words = [w for w in re.split(r'[\s\\]+', series_lower) if len(w) >= 4]
        is_subseries = '\\' in series

        def _has_series_link(txt: str) -> bool:
            import unicodedata as _ud2
            tl = _ud2.normalize('NFC', txt).lower().replace('\u0451', '\u0435')
            return not series_words or any(w in tl for w in series_words)

        # \u0411\u044b\u0441\u0442\u0440\u0430\u044f \u043f\u0440\u043e\u0432\u0435\u0440\u043a\u0430: \u0435\u0441\u043b\u0438 \u0432 \u0441\u0442\u0435\u043c\u0435 \u0441\u0435\u0440\u0432\u0438\u0441\u043d\u043e\u0435 \u0441\u043b\u043e\u0432\u043e \u0441\u0442\u043e\u0438\u0442 \u043d\u0435\u043f\u043e\u0441\u0440\u0435\u0434\u0441\u0442\u0432\u0435\u043d\u043d\u043e
        # \u043f\u0435\u0440\u0435\u0434 \u043d\u043e\u043c\u0435\u0440\u043e\u043c \u0442\u043e\u043c\u0430 (\u00ab\u0418\u0431\u0438\u0441\u043e\u0432\u0430\u044f \u0442\u0440\u0438\u043b\u043e\u0433\u0438\u044f 2. \u0414\u044b\u043c\u043d\u0430\u044f \u0440\u0435\u043a\u0430\u00bb), \u0444\u0430\u0439\u043b \u044f\u0432\u043b\u044f\u0435\u0442\u0441\u044f
        # \u043e\u0442\u0434\u0435\u043b\u044c\u043d\u044b\u043c \u0442\u043e\u043c\u043e\u043c \u0441\u0435\u0440\u0438\u0438, \u0430 \u043d\u0435 \u043a\u043e\u043c\u043f\u0438\u043b\u044f\u0446\u0438\u0435\u0439 \u2014 \u043d\u0435\u0437\u0430\u0432\u0438\u0441\u0438\u043c\u043e \u043e\u0442 \u043c\u0435\u0442\u0430\u0434\u0430\u043d\u043d\u044b\u0445.
        _stem_lc = book.abs_path.stem.lower().replace('\u0451', '\u0435')
        for _kw in self._SERIES_WORDS:
            if not _kw:
                continue
            if re.search(
                r'\b' + re.escape(_kw.lower().replace('\u0451', '\u0435')) + r'\s+\d{1,4}\s*[.\-\u2013\u2014]',
                _stem_lc,
            ):
                return 0, 0

        # Критерий 0: суффикс «(т. N-M)» / «(ч. N-M)» в stem — наш собственный формат
        # скомпилированного файла. Проверяем первым, т.к. дальнейший поиск диапазона
        # обрезается по точке в «т.» и не находит "N-M".
        _SUFFIX_RANGE_RE = re.compile(
            r'\((?:т|ч|том|часть|vol|book)\.?\s*(\d+)\s*[-–—]\s*(\d+)\s*\)',
            re.IGNORECASE | re.UNICODE,
        )
        _stem_val_early = book.abs_path.stem
        _sm = _SUFFIX_RANGE_RE.search(_stem_val_early)
        if _sm and _has_series_link(_stem_val_early):
            lo_e, hi_e = int(_sm.group(1)), int(_sm.group(2))
            if hi_e > lo_e:
                return lo_e, hi_e

        # Regex для удаления пометок тома родительской серии вида «(т. 7-8)»
        _VOL_ANNOT_STRIP = re.compile(
            r'\((?:т|том|vol|book|ч|часть)\.?\s*\d+[-–—]\d+\)', re.IGNORECASE | re.UNICODE
        )

        # Критерий 1: диапазон "N-M" в имени файла (stem) или title — приоритет выше метаданных
        # Имя файла отражает реальную организацию библиотеки, метаданные могут быть неточными.
        _RANGE_RE = re.compile(r'(\d+)\s*[-–—]\s*(\d+)', re.UNICODE)
        # Диапазон в самом начале stem: «01-2_...», «1-3 Название» — явная нумерация файла,
        # не требует проверки series_link (серия может не упоминаться в имени файла).
        _LEADING_RANGE_RE = re.compile(r'^\d+\s*[-–—]\s*\d+', re.UNICODE)
        _stem_val = book.abs_path.stem
        for candidate in (_stem_val, book.record.file_title or ''):
            if is_subseries:
                # Для подсерий: убираем пометки тома родительской серии вида «(т. 7-8)»,
                # затем проверяем series_link — иначе "Перелом 1-3" в подсерии
                # "Ратнинские бабы" ложно трактуется как предкомпиляция этой подсерии.
                # Исключение: ведущий диапазон "N-M..." принимаем без series_link.
                bare = _VOL_ANNOT_STRIP.sub('', candidate).strip()
                stem_leading = (candidate == _stem_val) and bool(_LEADING_RANGE_RE.match(bare))
                if not stem_leading and not _has_series_link(bare):
                    continue
                m = _RANGE_RE.search(bare)
            else:
                # Если диапазон стоит в начале имени файла — принимаем без series_link.
                # Пример: «01-2_Свободу демонам! Том 1 и 2» → диапазон 1-2 очевиден.
                stem_leading = (candidate == _stem_val) and bool(_LEADING_RANGE_RE.match(candidate))
                if not stem_leading and not _has_series_link(candidate):
                    continue
                # Ищем диапазон только в зоне ДО первой точки после вхождения названия серии.
                # Это исключает ложные срабатывания вида «Серия N. Подсерия M-K. Название»,
                # где M-K относится к подсерии, а не к основной серии.
                if not stem_leading:
                    _cand_low = candidate.lower()
                    _slink_pos = next((p for w in series_words
                                       for p in [_cand_low.find(w)] if p >= 0), -1)
                    if _slink_pos >= 0:
                        _after = candidate[_slink_pos:]
                        # Проверяем паттерн «SeriesName N. Подсерия M-K» — N стоит сразу
                        # после названия серии и является arc-позицией в родительской серии.
                        # Пример: «Брия 1. Книга Длинного Солнца 1-2» → arc N=1, не диапазон 1-2.
                        _arc_n_m = re.match(
                            r'^.{0,' + str(max(len(w) for w in series_words) + 5) + r'}'
                            r'\s+(\d{1,4})\s*\.',
                            _after
                        )
                        if _arc_n_m:
                            _arc_n_val = _arc_n_m.group(1)
                            _arc_n_int = int(_arc_n_val)
                            # Arc-номер не должен быть частью названия серии
                            if (_arc_n_int < 1900 and
                                    not re.search(r'(?<!\d)' + re.escape(_arc_n_val) + r'(?!\d)',
                                                  series_lower)):
                                return _arc_n_int, _arc_n_int  # arc-позиция, не внутренний диапазон
                        # Ищем точку-разделитель предложений, но НЕ десятичную точку (как в "2.0").
                        # Десятичная точка окружена цифрами с обеих сторон: (?<=\d)\.(?=\d).
                        _dot_m = re.search(r'(?<!\d)\.(?!\d)', _after)
                        _zone = _after[:_dot_m.start()] if _dot_m else _after
                        m = _RANGE_RE.search(_zone)
                        # Fallback: диапазон в самом конце стема, за пределами первой зоны.
                        # Пример: «Старатель. Золотая лихорадка. Урал. 19 век 1-6» — "1-6" в конце.
                        if m is None and candidate is _stem_val:
                            m = re.search(r'(\d+)\s*[-–—]\s*(\d+)\s*$', candidate)
                    else:
                        m = _RANGE_RE.search(candidate)
                else:
                    m = _RANGE_RE.search(candidate)
            if m:
                lo, hi = int(m.group(1)), int(m.group(2))
                if hi > lo:
                    return lo, hi

        # Критерий 1.5: паттерн "Серия. N-M книги/томов" — диапазон после точки.
        # Пример: «Император из стали. 1-2 книги» — зона до точки не содержит диапазона,
        # но "книги/томов" после числа однозначно указывает на предкомпиляцию.
        _BOOKS_RANGE_RE = re.compile(
            r'[\.\s]\s*(\d{1,4})\s*[-–—]\s*(\d{1,4})\s*(?:книги?|кн\.?|томов?|vols?\.?)\b',
            re.IGNORECASE | re.UNICODE,
        )
        for candidate in (_stem_val, book.record.file_title or ''):
            if _has_series_link(candidate):
                bm = _BOOKS_RANGE_RE.search(candidate)
                if bm:
                    lo, hi = int(bm.group(1)), int(bm.group(2))
                    if hi > lo:
                        return lo, hi

        # Критерий 1.6: паттерн "книги/томов N-M" — слово ПЕРЕД диапазоном.
        # Пример: «Преисподняя. Компиляция. Книги 1-5» — слово "Книги" стоит до диапазона.
        _BOOKS_BEFORE_RANGE_RE = re.compile(
            r'(?:книги?|кн\.?|томов?|vols?\.?)\s+(\d{1,4})\s*[-–—]\s*(\d{1,4})\b',
            re.IGNORECASE | re.UNICODE,
        )
        for candidate in (_stem_val, book.record.file_title or ''):
            if _has_series_link(candidate):
                bm = _BOOKS_BEFORE_RANGE_RE.search(candidate)
                if bm:
                    lo, hi = int(bm.group(1)), int(bm.group(2))
                    if hi > lo:
                        return lo, hi

        # Критерий 2.5: сервисное слово в имени ФАЙЛА (stem) — filename авторитетнее метаданных.
        # Пример: «Орел (Тетралогия)» → Тетралогия=4, хотя series_number может быть "1-2".
        # Проверяем stem ДО series_number, чтобы явное слово в имени файла не было перебито.
        # Исключение: «Ибисовая трилогия 1. Маковое море» — слово является частью названия
        # серии, за ним сразу идёт номер тома; такой файл — НЕ предкомпиляция.
        _stem_lower = book.abs_path.stem.lower()
        for idx, kw in enumerate(self._SERIES_WORDS):
            if kw and kw.lower() in _stem_lower:
                if _has_series_link(_stem_lower):
                    # «Серия N (Дилогия)» — N — номер подсерии, а не счётчик томов.
                    # В контексте зонтичной серии этот файл занимает одну позицию N,
                    # а не диапазон 1..N. Если имя серии не содержит N — мы в зонтичном
                    # контексте → не считаем предкомпиляцией (вернём 0,0 ниже).
                    # Пример: «Война Великого Бога 2 (Дилогия)» в серии «Война великого
                    # бога» → N=2, «2» нет в имени серии → (0,0).
                    # Если же серия «Война великого бога 2» → «2» есть → (1,2) ✓.
                    _kw_pos = _stem_lower.find(kw.lower())
                    _before_kw = _stem_lower[:_kw_pos]
                    # Паттерн «Серия N (Сервисное)»: N в скобках
                    _sub_n_m = re.search(r'(?<![–—\-\d])(\d{1,4})\s*\(\s*$', _before_kw)
                    if _sub_n_m:
                        _n_val = _sub_n_m.group(1)
                        if not re.search(r'(?<!\d)' + re.escape(_n_val) + r'(?!\d)', series_lower):
                            return 0, 0
                    # Паттерн «Серия N. Подсерия. Сервисное» (N через точку, не в скобках):
                    # Пример: «Вселенная Сафари 2. Егерь. Трилогия» — arc 2, не диапазон 1-3.
                    # Если N не входит в название серии → это arc-позиция, возвращаем (N, N).
                    _dot_n_m = re.search(
                        r'(?<![–—\-\d])(\d{1,4})\s*\.\s+\S+.*$', _before_kw
                    )
                    if _dot_n_m:
                        _n_val = _dot_n_m.group(1)
                        _arc_n = int(_n_val)
                        if (_arc_n < 1900 and
                                not re.search(r'(?<!\d)' + re.escape(_n_val) + r'(?!\d)', series_lower)):
                            return _arc_n, _arc_n  # arc-позиция в родительской серии
                    return 1, idx

        # Критерий 2: series_number — диапазон "N-M" из метаданных (запасной вариант)
        # Для подсерий допускаем только явный диапазон: одиночное число означает позицию
        # в родительской серии и не является признаком предкомпиляции подсерии.
        sn = (book.record.series_number or '').strip()
        if sn:
            m = re.match(r'^(\d+)\s*[-–—]\s*(\d+)$', sn)
            if m:
                lo, hi = int(m.group(1)), int(m.group(2))
                if hi > lo:
                    return lo, hi

        # Критерий 3: title содержит сервисное слово + признак серии.
        # (stem уже проверен в Критерии 2.5)
        for _kw_text in ((book.record.file_title or '').lower(),):
            if not _kw_text:
                continue
            for idx, kw in enumerate(self._SERIES_WORDS):
                if kw and kw.lower() in _kw_text:
                    if _has_series_link(_kw_text):
                        # Та же проверка «N (ServiceWord)» что и в Критерии 2.5:
                        # если перед сервисным словом стоит число N и серия N не содержит,
                        # это подсерия N — не считаем предкомпиляцией зонтичной серии.
                        _kw_pos3 = _kw_text.find(kw.lower())
                        _before3 = _kw_text[:_kw_pos3]
                        _sub_n3 = re.search(r'(?<![–—\-\d])(\d{1,4})\s*\(\s*$', _before3)
                        if _sub_n3:
                            if not re.search(r'(?<!\d)' + re.escape(_sub_n3.group(1)) + r'(?!\d)', series_lower):
                                return 0, 0
                        return 1, idx  # сервисное слово → предполагаем lo=1

        # Критерий 4: файл выглядит как компиляция (по имени/title) — читаем FB2-контент
        _COMPILATION_WORDS = re.compile(
            r'компилян|компиляц|сборник|omnibus|антолог|собрани', re.IGNORECASE | re.UNICODE
        )
        _title_text = (book.record.file_title or book.abs_path.stem).lower()
        if _COMPILATION_WORDS.search(_title_text) or _COMPILATION_WORDS.search(book.abs_path.stem.lower()):
            lo, hi = self._precompiled_range_from_content(book.abs_path, series)
            if hi > lo:
                return lo, hi

        # Критерий 5: несколько книжных заголовков в file_title → внешняя предкомпиляция
        # Пример: "Спартанец: Спартанец. Великий царь. Удар в сердце" — Трилогия Империи.
        # series_link НЕ требуем: компиляция может называть отдельные книги, не серию.
        # Авторитет — содержимое файла: _precompiled_range_from_content вернёт (0,0)
        # для одиночной книги с подзаголовками.
        # Минимальный фильтр: ≥2 заглавных буквы после «. » ИЛИ «Серия: Книга1. Книга2»
        # (двоеточие — классический маркер сборника).
        _multi_title = book.record.file_title or ''
        _is_multi = (
            len(re.findall(r'\.\s+[А-ЯЁA-Z]', _multi_title)) >= 2
            or (': ' in _multi_title and len(re.findall(r'\.\s+[А-ЯЁA-Z]', _multi_title)) >= 1)
        )
        # «Серия: Название. Том N. Подзаголовок» — структурированный одиночный том, не компиляция.
        # Маркер «. Том/Книга/Часть N» однозначно указывает на отдельную книгу серии.
        if _is_multi and re.search(
            r'\.\s+(?:Том|Книга|Часть|Vol\.?|Book)\s+\d+', _multi_title, re.IGNORECASE
        ):
            _is_multi = False
        if _is_multi:
            lo, hi = self._precompiled_range_from_content(book.abs_path, series)
            if hi > lo:
                return lo, hi

        return 0, 0

    # ---- регулярки для парсинга FB2 ----
    _FB2_SEQUENCE_RE = re.compile(
        r'<sequence[^>]+number=["\'](\d+)["\']', re.IGNORECASE | re.DOTALL
    )
    _FB2_SECTION_TITLE_RE = re.compile(
        r'<section[^>]*>\s*<title[^>]*>\s*<p[^>]*>(.*?)</p>', re.IGNORECASE | re.DOTALL
    )

    def _precompiled_range_from_content(self, abs_path: Path, series: str) -> Tuple[int, int]:
        """Определить диапазон томов по содержимому FB2-файла.

        Читает первые 64KB и ищет:
        1. <sequence number="N"> внутри отдельных секций — берём min/max N
        2. Заголовки секций первого уровня — ищем «Том N», «Книга N», римские цифры

        Возвращает (lo, hi) или (0, 0) если не удалось определить.
        """
        try:
            if not abs_path.exists():
                return 0, 0
            raw = abs_path.read_bytes()[:65536]
            try:
                text = raw.decode('utf-8', errors='replace')
            except Exception:
                text = raw.decode('cp1251', errors='replace')
        except Exception:
            return 0, 0

        nums: List[int] = []

        # Способ 1: <sequence number="N"> внутри <section> (наш компилятор прописывает их)
        for m in self._FB2_SEQUENCE_RE.finditer(text):
            n = int(m.group(1))
            if 1 <= n <= 100:
                nums.append(n)

        # Способ 2: заголовки секций первого уровня — ищем числа и ключевые слова
        for m in self._FB2_SECTION_TITLE_RE.finditer(text):
            title_text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
            # Ключевые слова тома + арабская цифра
            km = self._VOLUME_KEYWORDS_RE.search(title_text)
            if km:
                n = int(km.group(1))
                if 1 <= n <= 100:
                    nums.append(n)
                continue
            # Римские цифры в заголовке
            rm = self._VOLUME_ROMAN_RE.search(title_text)
            if rm:
                n = self._roman_to_int(rm.group(1))
                if n and 1 <= n <= 100:
                    nums.append(n)
                continue
            # Голые цифры в заголовке секции (без слов-маркеров) намеренно пропускаем:
            # это почти всегда нумерация глав внутри книги, не томов компиляции.

        if not nums:
            return 0, 0
        lo, hi = min(nums), max(nums)
        # Требуем хотя бы 2 разных номера, чтобы не принять один том за диапазон
        if lo == hi or hi - lo > 20:  # слишком большой пробел — ненадёжно
            return 0, 0
        return lo, hi

    def _precompiled_count(self, book: CompilationBook, series: str) -> int:
        """Обёртка для обратной совместимости. Возвращает hi - lo + 1 или 0."""
        lo, hi = self._precompiled_range(book, series)
        return (hi - lo + 1) if hi > lo else 0

    @classmethod
    def _normalize_title_key(cls, raw_title: str, series: str) -> str:
        """Нормализовать заголовок для дедупликации.

        Убирает возможный префикс в виде «<Серия>. » или «<Серия> » перед
        собственно названием книги, чтобы «Аквилон. Маг воды. Том 3» и
        «Маг воды. Том 3» воспринимались как один и тот же том.

        Применяет NFKC-нормализацию чтобы Unicode-символы римских цифр
        (Ⅰ U+2160 … Ⅻ U+216B) приводились к ASCII-эквивалентам (I … XII)
        и не создавали ложных дублей.
        """
        # NFKC: Ⅰ→I, Ⅱ→II, …, Ⅻ→XII и т.п.
        key = unicodedata.normalize('NFKC', raw_title).strip().lower().replace('ё', 'е')
        series_norm = unicodedata.normalize('NFKC', series).strip().lower().replace('ё', 'е')
        # Попробовать отрезать префикс вида "<серия>. " или "<серия> "
        for sep in ('. ', ' '):
            candidate = series_norm + sep
            if key.startswith(candidate):
                key = key[len(candidate):]
                break
        return key

    _STRIP_TAGS_RE = re.compile(r'<[^>]+>')
    # Читаем только первые 64 КБ файла — достаточно для захвата начала <body>
    _OPENING_READ_LIMIT = 65_536
    # Заголовки секций-предисловий — пропускаем при сравнении содержимого
    _PREFACE_SECTION_RE = re.compile(
        r'предисловие|вступлени[ея]|от\s+автор|от\s+переводчик|foreword|preface|'
        r'introduction|аннотаци[яи]|copyright|копирайт|все\s+права',
        re.IGNORECASE | re.UNICODE,
    )
    # URL и издательские копирайт-блоки — удаляем из сравниваемого текста
    _BOILERPLATE_RE = re.compile(
        r'https?://\S+|'                                          # URL
        r'©[^©\n]{1,120}|'                                       # © строка
        r'выпуск\s+произведения[^©\n]*|'                         # «Выпуск произведения без разрешения...»
        r'isbn[\s:]*[\d\-–—Xx]{5,}',                             # ISBN
        re.IGNORECASE | re.UNICODE,
    )

    def _extract_opening_text(self, book: CompilationBook, chars: int = 2000) -> str:
        """Вернуть первые `chars` символов нормализованного plain-text из <body>.

        Пропускает секции-предисловия (одинаковые у многих книг одной серии),
        удаляет URL и copyright-блоки, берёт текст первой содержательной секции.
        Читает только первые _OPENING_READ_LIMIT байт файла.
        """
        try:
            if not book.abs_path.exists():
                return ''
            with book.abs_path.open('rb') as fh:
                raw = fh.read(self._OPENING_READ_LIMIT)
            # Быстрое определение кодировки из XML-декларации (первые 256 байт)
            enc_m = re.search(rb'encoding\s*=\s*["\']([^"\']+)["\']', raw[:256], re.IGNORECASE)
            enc = enc_m.group(1).decode('ascii', errors='ignore') if enc_m else 'utf-8'
            try:
                text = raw.decode(enc, errors='replace')
            except (LookupError, UnicodeDecodeError):
                text = raw.decode('utf-8', errors='replace')
            # Находим начало основного <body> (не notes/footnotes)
            body_m = re.search(
                r'<(?:fb:)?body(?!\s[^>]*\bname\s*=)[^>]*>',
                text, re.IGNORECASE,
            )
            content = text[body_m.end():] if body_m else text

            # Ищем первую содержательную секцию, пропуская предисловия
            # Каждая <section> проверяется по заголовку <title>
            content_start = 0
            for sec_m in re.finditer(r'<(?:fb:)?section[^>]*>', content, re.IGNORECASE):
                sec_pos = sec_m.end()
                # Заголовок секции: следующий <title>…</title>
                title_m = re.search(
                    r'<(?:fb:)?title[^>]*>(.*?)</(?:fb:)?title>',
                    content[sec_pos:sec_pos + 400], re.IGNORECASE | re.DOTALL,
                )
                if title_m:
                    title_plain = self._STRIP_TAGS_RE.sub('', title_m.group(1)).strip()
                    if self._PREFACE_SECTION_RE.search(title_plain):
                        continue  # пропускаем предисловие
                # Первая не-предисловие секция
                content_start = sec_m.start()
                break

            plain = self._STRIP_TAGS_RE.sub(' ', content[content_start:])
            # Удаляем URL, © строки, ISBN — они одинаковы у всех книг издательства
            plain = self._BOILERPLATE_RE.sub(' ', plain)
            plain = re.sub(r'\s+', ' ', plain).strip()
            return plain[:chars]
        except Exception:
            return ''

    def _dedup_by_content(
        self,
        books: List[CompilationBook],
        duplicate_paths: List[Path],
        similarity_threshold: float = 0.85,
        compare_chars: int = 2000,
    ) -> List[CompilationBook]:
        """Убрать книги, чьё открывающее содержимое совпадает с другой книгой группы.

        Алгоритм двухфазный:
          1. Параллельное чтение первых 64 КБ всех файлов (ThreadPoolExecutor).
          2. Хэш-фильтр: одинаковый hash(text) → ratio=1.0, SequenceMatcher не нужен.
             Разные хэши → SequenceMatcher только если тексты достаточно длинные.

        Из пары дублей оставляем книгу с более конкретной позицией в серии
        (ненулевой subseries-компонент sort_key[2] или sort_key[3]). При равной
        конкретности — больший по размеру файл (вероятный сборник).
        """
        from difflib import SequenceMatcher
        from concurrent.futures import ThreadPoolExecutor

        if len(books) < 2:
            return books

        # Уже распознанные precompile-файлы (volume_label="N-M") исключаем:
        # их начало совпадает с томом 1 по определению — удалять том 1 нельзя.
        _RANGE_VL = re.compile(r'^\d+\s*[-–—]\s*\d+$')
        eligible = [b for b in books if not _RANGE_VL.match(b.volume_label or '')]
        if len(eligible) < 2:
            return books

        # ── Фаза 1: параллельное чтение файлов ────────────────────────────────
        workers = min(8, len(eligible))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            texts = list(pool.map(
                lambda b: self._extract_opening_text(b, compare_chars),
                eligible,
            ))
        opening = {id(b): t for b, t in zip(eligible, texts)}
        # Хэш для быстрого отсева идентичных пар
        hashes  = {id(b): hash(opening[id(b)]) for b in eligible}

        def _specificity(b: CompilationBook) -> int:
            sk = b.sort_key
            # Прямая arc-позиция (sk[1]>0) предпочтительнее косвенной (sk[1]=0, sk[2]>0).
            # «Спасатель 1» (0,1,0,0) конкретнее чем «01_Книга» (0,0,1,0).
            if len(sk) > 1 and sk[1] > 0:
                return 3  # прямая позиция — максимальный приоритет
            return (1 if len(sk) > 2 and sk[2] != 0 else 0) + (1 if len(sk) > 3 and sk[3] != 0 else 0)

        def _file_size(b: CompilationBook) -> int:
            try:
                return b.abs_path.stat().st_size
            except OSError:
                return 0

        # ── Фаза 2: попарное сравнение ─────────────────────────────────────────
        to_remove: set = set()
        for i, book_a in enumerate(eligible):
            if id(book_a) in to_remove:
                continue
            text_a = opening[id(book_a)]
            if not text_a:
                continue
            for book_b in eligible[i + 1:]:
                if id(book_b) in to_remove:
                    continue
                text_b = opening[id(book_b)]
                if not text_b:
                    continue
                # Книги с разными однозначными позициями в серии не могут быть
                # дубликатами — у них просто совпадает общий пролог/эпиграф.
                # Пример: «Китамар 1» и «Китамар 2» оба начинаются одной фразой.
                _pa, _pb = book_a.sort_key, book_b.sort_key
                if (_pa[0] == 0 and _pb[0] == 0
                        and _pa[1] != 0 and _pb[1] != 0
                        and _pa[1] != _pb[1]):
                    continue
                # Хэш-фильтр: одинаковые хэши → ratio=1.0 без SequenceMatcher
                if hashes[id(book_a)] == hashes[id(book_b)]:
                    ratio = 1.0
                else:
                    ratio = SequenceMatcher(None, text_a, text_b).ratio()
                if ratio < similarity_threshold:
                    continue
                # Похожи: решаем, какую оставить.
                # Приоритет 1: точность позиции в серии (прямой arc > косвенный subseries).
                # Приоритет 2 (тайбрейкер): размер файла — больший файл содержит больше текста.
                spec_a, spec_b = _specificity(book_a), _specificity(book_b)
                if spec_a != spec_b:
                    loser = book_b if spec_a > spec_b else book_a
                else:
                    size_a, size_b = _file_size(book_a), _file_size(book_b)
                    loser = book_b if size_a >= size_b else book_a
                to_remove.add(id(loser))
                duplicate_paths.append(loser.abs_path)
                self._log(
                    f"  ≈ Контент-дубликат (ratio={ratio:.2f}): "
                    f"{loser.abs_path.name} → удаляется в пользу "
                    f"{'другого' if loser is book_b else book_a.abs_path.name}"
                )
                if id(book_a) in to_remove:
                    break

        return [b for b in books if id(b) not in to_remove]

    def _dedup_by_position(
        self,
        books: List[CompilationBook],
        duplicate_paths: List[Path],
    ) -> List[CompilationBook]:
        """Убрать книги-дубликаты с одинаковой позицией тома.

        После title-дедупликации может остаться несколько книг с одним и тем же
        sort_key на уровне 0 (series_number) или 1 (filename number).  Из таких
        дублей оставляем первую по алфавиту (det. выбор), остальные идут в
        duplicate_paths.

        Книги с ambiguous sort_key (уровень 9) из этого фильтра исключены —
        для них позиция неизвестна, они останутся до проверки order_determined.
        """
        # Для книг с одинаковой позицией тома оставляем наиболее свежую версию.
        # Для книг с одинаковой позицией тома выбираем наиболее свежую версию.
        # Критерии (по убыванию надёжности):
        #   1. Дата из тега <date> в title-info FB2 — самый достоверный признак
        #   2. Явные ключевые слова "свежести" в имени файла или title
        #   3. Алфавитный порядок пути (детерминированный fallback)
        _FRESH_KEYWORDS = re.compile(
            r'новый?\s+вариант|новая?\s+редакц|переработан|updated?|revision|new\s+ver',
            re.IGNORECASE | re.UNICODE,
        )

        # Предвычисляем для каждой книги набор значимых слов stem (без ведущего числа)
        def _stem_words(book: CompilationBook) -> set:
            # Снимаем ведущий числовой префикс с любым разделителем: N. N- N_ N–
            s = re.sub(r'^\d+\s*[.\-–—_]\s*', '', book.abs_path.stem)
            return {w.lower() for w in re.split(r'\W+', s) if len(w) >= 3}

        _all_stem_words = [_stem_words(b) for b in books]

        def _naming_fit(book: CompilationBook) -> int:
            """Число других книг группы, разделяющих ≥1 значимое слово со stem этой книги."""
            bw = _stem_words(book)
            if not bw:
                return 0
            return sum(1 for ow in _all_stem_words if len(bw & ow) >= 1)

        def _struct_prefix(book: CompilationBook) -> str:
            """Структурный «подпись» файла для определения когерентного набора.

            Два файла когерентны, если их _struct_prefix совпадает:
            - «1_Спасатель» и «2_Спасатель» → оба дают sep='_' (нормализуем цифру)
            - «Шалашов, Ковальчук. Воля императора 1.» и «...2.» → оба дают
              'Шалашов, Ковальчук. Воля императора'
            - Файл без структуры → возвращаем уникальный stem, cohesion=0.
            """
            stem = book.abs_path.stem
            # Случай 1: файл начинается с числа (NN_ или NN-) → нормализуем до разделителя
            m = re.match(r'^\d+\s*([._\-])', stem)
            if m:
                return '\x00sep:' + m.group(1)  # уникальный тег, не путается с именами
            # Случай 2: автор-префикс с номером серии внутри
            series_root = ''
            if book.record and book.record.proposed_series:
                series_root = book.record.proposed_series.split('\\')[0].strip()
            if series_root:
                pat = re.escape(series_root) + r'\s+\d'
                m2 = re.search(pat, stem, re.IGNORECASE)
                if m2:
                    return stem[:m2.start() + len(series_root)].rstrip()
            return stem  # нет структуры — уникальная строка, cohesion=0

        _all_prefixes = [_struct_prefix(b) for b in books]

        def _naming_cohesion(book: CompilationBook) -> int:
            """Число других книг группы с тем же структурным префиксом."""
            pfx = _struct_prefix(book)
            if pfx == book.abs_path.stem:
                return 0  # нет структуры
            return sum(1 for p in _all_prefixes if p == pfx) - 1

        def _book_freshness(book: CompilationBook):
            """Ключ сортировки: чем свежее и ближе к паттерну группы — тем меньше (идёт первым)."""
            # 0. Когерентность именного набора: файл из одной «партии» (тот же разделитель N_/N.)
            #    предпочитается перед файлом из другой партии — даже если у второго год новее.
            #    Пример: «1_Спасатель. Злой город.» и «2_Спасатель-2» оба с '_' → cohesion=1;
            #             «1. Спасатель (2018)» с '.' → cohesion=0 → проигрывает.
            cohesion_key = -_naming_cohesion(book)

            # 1. Схожесть названия с другими книгами группы (больше = лучше = меньший ключ)
            fit_key = -_naming_fit(book)

            # 2. Дата из FB2 <date> — лексикографически сравниваем, инвертируем
            date_str = self._extract_date_from_fb2(book.abs_path) or ''
            date_key = tuple(-int(x) for x in date_str.split('-')) if date_str else (0,)

            # 3. Ключевые слова в имени/названии
            text = f"{book.abs_path.stem} {book.record.file_title or ''}"
            kw_key = 0 if _FRESH_KEYWORDS.search(text) else 1

            # 4. Одиночная книга имеет приоритет над многотомной предкомпиляцией.
            _ft = book.record.file_title or ''
            multi_key = 1 if len(re.findall(r'\.\s+[А-ЯЁA-Z]', _ft)) >= 2 else 0

            # 5. Год из имени файла (например "- 2022" → свежее 2018)
            year_m = re.search(r'[-–\s](\d{4})\b', book.abs_path.stem)
            year_key = -int(year_m.group(1)) if year_m else 0

            return (cohesion_key, fit_key, date_key, kw_key, multi_key, year_key, str(book.abs_path))

        sorted_books = sorted(books, key=_book_freshness)
        seen_positions: Dict[Tuple, CompilationBook] = {}
        result: List[CompilationBook] = []
        for book in sorted_books:
            level = book.sort_key[0]
            if level == 0:
                pos_key = book.sort_key  # (0, num, 0)
                if pos_key not in seen_positions:
                    seen_positions[pos_key] = book
                    result.append(book)
                else:
                    # Одинаковый номер в серии — проверяем title.
                    # Если названия разные (издатель присвоил один номер двум разным книгам)
                    # — оставляем обе, не считаем дублем.
                    existing = seen_positions[pos_key]
                    existing_title = _norm_key(existing.record.file_title or existing.abs_path.stem)
                    this_title = _norm_key(book.record.file_title or book.abs_path.stem)
                    if existing_title != this_title:
                        result.append(book)  # разные книги с одним номером — берём обе
                    else:
                        duplicate_paths.append(book.abs_path)
            else:
                result.append(book)
        return result

    def _determine_sort_key(
        self, rec, abs_path: Path
    ) -> Tuple[Tuple, str, bool, str]:
        """Многоуровневое определение порядка книги.

        Returns:
            (sort_key_tuple, source_name, is_ambiguous, volume_label)
            volume_label — отображаемый номер/диапазон: "1", "1-3", "2021" и т.п.
        """
        stem = Path(rec.file_path).stem


        # Источник А: series_number из FB2-метаданных.
        # Пропускаем для подсерий (proposed_series содержит '\') — там series_number
        # относится к родительской серии и не отражает позицию внутри подсерии.
        #
        # ВАЖНО: series_number берётся ТОЛЬКО если metadata_series соответствует
        # proposed_series. Если в FB2 указана другая серия (например, "Викинг" вместо
        # "Варяг"), её порядковый номер не имеет смысла для текущей серии.
        is_subseries = '\\' in (rec.proposed_series or '')
        sn = (rec.series_number or '').strip()

        # Ранняя детекция EBLO-скомпилированных подсерий: «... (ч. N в K книгах)»
        # Такой файл — предкомпиляция внутреннего диапазона 1-K дуги N.
        # Возвращаем arc-позицию N как sort_key[1] и «1-K» как volume_label,
        # чтобы find_groups распознал его как precompiled и почистил исходники.
        if is_subseries:
            _inner_comp_m = re.search(r'\(ч\.\s*(\d+)\s+в\s+(\d+)\s+книгах\)', stem)
            if _inner_comp_m:
                _arc_n = int(_inner_comp_m.group(1))
                _k = int(_inner_comp_m.group(2))
                if 0 < _arc_n < 1900 and _k > 1:
                    return (0, _arc_n, 0, 0), 'inner_precompilation', False, f'1-{_k}'

        # Исключение: filename_named_arc — series_number это ГЛОБАЛЬНАЯ позиция тома
        # (выставлена нашим же кодом в _detect_named_arcs), не позиция в подсерии.
        # Используем напрямую, минуя обычную subseries-логику.
        if sn and is_subseries and (rec.series_source or '') == 'filename_named_arc':
            if re.match(r'^\d+$', sn):
                _arc_n = int(sn)
                if _arc_n and _arc_n < 1900:
                    return (0, _arc_n, 0, 0), 'series_number', False, sn

        # Дробный sn вида «8.1», «8.2» или «0.1», «0.2» — позиция внутри тома/пролога.
        # Создаётся _detect_named_arcs или правилом 1.5 из дробного префикса имени файла.
        # sort_key: (0, major, minor, 0) — встраивается между целыми позициями.
        # Применяем для любого источника (не только filename_named_arc), т.к.
        # дробный sn может быть извлечён из префикса файла «0.1. Заголовок.fb2».
        if sn and not is_subseries:
            _frac_m = re.match(r'^(\d+)\.(\d+)$', sn)
            if _frac_m:
                _major = int(_frac_m.group(1))
                _minor = int(_frac_m.group(2))
                if _major < 1900:  # 0 допустим (пролог), проверяем только потолок
                    return (0, _major, _minor, 0), 'series_number', False, sn

        if sn and not is_subseries:
            meta_s = (rec.metadata_series or '').strip().lower().replace('ё', 'е')
            prop_s = (rec.proposed_series or '').strip().lower().replace('ё', 'е')
            # Слова proposed_series длиной ≥ 3 (ключевые слова серии)
            prop_words = {w for w in prop_s.split() if len(w) >= 3}
            # series_number применяем только если metadata_series не задана,
            # или совпадает с proposed_series, или содержит хотя бы одно ключевое слово
            # НО: если meta_s содержит значимые слова, которых нет в prop_s,
            # это БОЛЕЕ КОНКРЕТНАЯ серия (подсерия). Её sn — позиция внутри подсерии,
            # а не в proposed_series (umbrella). Пример: proposed="Рубеж",
            # metadata="Сирийский рубеж" → «сирийский» — лишнее слово → не совпадает.
            # Допускаем только нейтральные (сервисные) слова: цикл, серия, сага и т.п.
            _SERIES_NEUTRAL = {'цикл', 'серия', 'сага', 'the', 'series', 'cycle',
                               'трилогия', 'дилогия', 'тетралогия'}
            _meta_words = set(meta_s.split())
            _prop_words_set = set(prop_s.split())
            _extra = _meta_words - _prop_words_set - _SERIES_NEUTRAL
            # Цифры — маркеры позиции, а не содержательные слова серии.
            # «Война великого бога 2» vs «Война великого бога»: «2» не делает серию
            # "более специфичной" в смысле другой подсерии.
            _extra = {w for w in _extra if not w.isdigit()}
            _meta_more_specific = bool(_extra)  # True: meta добавляет значимые слова
            _series_ok = (
                not meta_s
                or meta_s == prop_s
                or bool(prop_words
                        and any(w in meta_s for w in prop_words)
                        and not _meta_more_specific)
            )
            if _series_ok:
                meta_num: Optional[int] = None
                if re.match(r'^\d+$', sn):
                    meta_num = int(sn)
                else:
                    rng = re.match(r'^(\d+)\s*[-–]\s*(\d+)$', sn)
                    if rng:
                        # Диапазон «1-N» в series_number чаще всего означает предкомпиляцию.
                        # Если stem содержит «P (СервисноеСлово)» — P есть позиция в
                        # зонтичной серии, тогда как «1» — позиция внутри подсерии P.
                        # Пример: «Война великого бога 2 (Дилогия)», sn="1-2" →
                        #   P=2 (из имени файла) — правильная позиция в зонтичной серии.
                        #   Если взять meta_num=1, sort_key=(0,1) совпадёт с книгой 1 →
                        #   dedup удалит Дилогию как «дубликат».
                        _sw_terms = '|'.join(
                            re.escape((kw or '').lower())
                            for kw in self._SERIES_WORDS if kw
                        )
                        _sub_cmp = re.search(
                            r'(?<![–—\-\d])(\d{1,4})\s*\((?:' + _sw_terms + r')',
                            stem.lower(),
                        ) if _sw_terms else None
                        meta_num = int(_sub_cmp.group(1)) if _sub_cmp else int(rng.group(1))

                if meta_num is not None:
                    # Паттерн N.M (dot_part) имеет приоритет над series_number:
                    # файлы вида «Расходники 2.3» должны получить sort_key (0,2,3,0)
                    # а не (0,5,0,0) из метаданных, иначе они не попадут в один run
                    # с файлами 1.1, 1.2, 2.1, у которых sn отсутствует.
                    _dp = self._extract_dot_part(stem, rec.file_title or '')
                    if _dp is not None:
                        _dv, _dpt = _dp
                        return (0, _dv, _dpt, 0), 'dot_part', False, f'{_dv}.{_dpt}'

                    # Перекрёстная проверка с именем файла.
                    # Если в stem явно написан другой номер — доверяем файлу,
                    # иначе метаданные могут быть ошибочными (например, sn='1' для тома 2).
                    fn_m = (self._STEM_NUM_RE.match(stem) or self._STEM_NUM_RE.search(stem)
                            or re.search(r'(?:^|[-–\s])(\d{1,4})\.\s+[А-ЯЁA-Z]', stem))
                    if fn_m:
                        fn_num = int(next(g for g in fn_m.groups() if g is not None))
                        # Числа >= 1900 — год в имени файла, не номер тома; игнорируем
                        if fn_num < 1900 and fn_num != meta_num:
                            # Расхождение: проверяем что meta_num не стоит ПОЗЖЕ в стеме.
                            # Иначе "Хоттабыч 1. Позывной Хоттабыч 5" → fn_num=1, meta_num=5,
                            # но "5" есть после "1" → метаданные точнее, не перебиваем.
                            _after_fn = stem[fn_m.end():]
                            if not re.search(r'\b' + str(meta_num) + r'\b', _after_fn):
                                return (0, fn_num, 0, 0), 'filename', False, str(fn_num)
                    # Дополнительная проверка: Roman numeral inline («Том Ⅱ», «Том III» …).
                    # FB2-метаданные нередко хранят series_number="1" для всех томов серии,
                    # тогда как имя файла содержит точный номер в виде римской цифры.
                    _ft2 = rec.file_title or ''
                    _ft2_is_series = bool(_ft2) and _norm_key(_ft2) == _norm_key(rec.proposed_series or '')
                    # Проверяем паттерн «Том N. Часть M» ДО roman_inline —
                    # иначе «Том XII. Часть вторая» даёт roman_inline=12 != meta_num=13
                    # и возвращает (0, 12, 0, 0) без учёта части.
                    # Условие: используем только если vol совпадает с meta_num.
                    # Иначе «Том 7. Часть 2» при sn='08' даёт (0,7,2,0) вместо (0,8,0,0):
                    # «Том 7» описывает структуру внутри тома, а не позицию в серии.
                    _ft_for_part = rec.file_title or ''
                    vp = self._extract_volume_part(_ft_for_part, stem)
                    if vp is not None:
                        vol, part = vp
                        if vol == meta_num:
                            return (0, vol, part, 0), 'volume_part', False, str(vol)
                    roman_inline = self._extract_inline_volume_number(
                        stem if _ft2_is_series else (_ft2 or stem), stem
                    )
                    if roman_inline is not None and roman_inline != meta_num:
                        # Если meta_num явно присутствует в стеме — доверяем метаданным.
                        # Иначе "Аватар Х. Часть 2" с meta_num=7 даёт roman_inline=2 →
                        # коллизия с книгой 2, хотя "7" есть прямо в имени файла.
                        # Дополнительно проверяем ведущий zero-padded префикс:
                        # \b6\b не находит "6" в "06_" (нет word boundary внутри "06"),
                        # но "06_..." с meta_num=6 должен считаться подтверждённым.
                        _meta_in_stem = bool(
                            re.search(r'\b' + str(meta_num) + r'\b', stem)
                            or re.match(r'^0*' + str(meta_num) + r'(?:[^0-9]|$)', stem)
                        )
                        if not _meta_in_stem:
                            return (0, roman_inline, 0, 0), 'inline_title', False, str(roman_inline)
                    # «Серия N. Подзаголовок. Том M» — meta_num = позиция в серии,
                    # «Том/Книга M» стоит ПОСЛЕ серийного суффикса «N.» → M как secondary.
                    # Пример: «Война великого бога 2. Внутренняя война. Том 1» → (0,2,1,0),
                    #          «Война великого бога 2. Внутренняя война. Том 2» → (0,2,2,0).
                    # Условие: fn_m нашёл meta_num, а TOM-ключевое слово идёт после него.
                    if fn_m:
                        _fn_num_chk = int(next(g for g in fn_m.groups() if g is not None))
                        # Не применяем secondary-check для ведущего числа-префикса
                        # (e.g. «13_Авиатор. Книга 12+1»): там fn_m стоит в позиции 0,
                        # а «Книга N» в остатке описывает тот же том другим способом.
                        # Паттерн применяется только когда число вложено в имя серии
                        # (e.g. «Война великого бога 2. Внутренняя война. Том 1»).
                        _is_leading_prefix = fn_m.start() == 0 and stem[:1].isdigit()
                        if _fn_num_chk == meta_num and not _is_leading_prefix:
                            _stem_nfc = unicodedata.normalize('NFKC', stem)
                            _kw_after = self._VOLUME_KEYWORDS_RE.search(_stem_nfc, fn_m.end())
                            if _kw_after:
                                _kw_n = int(_kw_after.group(1))
                                # Пропускаем если keyword-число совпадает с meta_num:
                                # в этом случае pass2 уже обновил series_number ← Том M,
                                # поэтому meta_num и _kw_n совпадают — secondary не нужен.
                                if _kw_n != meta_num:
                                    return (0, meta_num, _kw_n, 0), 'series_number', False, sn
                    # Если meta_num совпадает с числом в конце названия серии,
                    # это arc-номер (напр. "Позывной «Курсант» 2" → arc=2, sn=2).
                    # Реальный номер книги внутри arc ищем в file_title.
                    _series_trailing = re.search(r'\s+(\d{1,4})\s*$', prop_s)
                    if _series_trailing and int(_series_trailing.group(1)) == meta_num:
                        _ft_raw = (rec.file_title or '').strip()
                        _ft_book = re.search(r'[-–—\s]+(\d{1,4})\s*$', _ft_raw)
                        if _ft_book:
                            _book_n = int(_ft_book.group(1))
                            if 0 < _book_n < 1900 and _book_n != meta_num:
                                return (0, _book_n, 0, 0), 'subseries_number', False, str(_book_n)

                    return (0, meta_num, 0, 0), 'series_number', False, sn

        # When _series_ok is False but series_number was already set by Rule 2 (pass2),
        # trust that value instead of falling through to Source B which can't handle
        # non-digit-prefixed filenames like "Марков-Бабкин. Новый Михаил-8. ...".
        _series_ok_val = _series_ok if (sn and not is_subseries) else True
        if not _series_ok_val and sn and re.match(r'^\d+$', sn):
            fn_num_from_sn = int(sn)
            if fn_num_from_sn < 1900:
                return (0, fn_num_from_sn, 0, 0), 'series_number', False, sn

        if is_subseries:
            result = self._sort_key_for_subseries(rec, sn, stem)
            if result is not None:
                return result
        # Источник Б: число в начале/конце имени файла.
        # При многоуровневой нумерации ("Серия N. Подсерия M. ... Том K") извлекаем
        # secondary и tertiary, чтобы избежать коллизий sort_key между подсериями.
        num_m = self._STEM_NUM_RE.match(stem) or self._STEM_NUM_RE.search(stem) or re.search(
            r'(?:^|[-–\s])(\d{1,4})\.\s+[А-ЯЁA-Z]', stem
        )
        if num_m:
            num = int(next(g for g in num_m.groups() if g is not None))
            # Числа >= 1900 — скорее всего год в имени файла, не номер тома.
            if num < 1900:
                # Пробуем извлечь secondary и tertiary из остатка stem.
                # Сначала проверяем диапазон "N-M" (подсерия покрывает несколько томов).
                # Пример: «Брия 1. Книга Длинного Солнца 1-2. Литания» → num=1, _rest содержит "1-2."
                # secondary=1 (lo диапазона), volume_label="1-2" (для get_hi = 2).
                _rest = stem[num_m.end():]
                secondary = 0
                volume_label = str(num)
                _range_sec_m = re.search(
                    r'(?<!\d)(\d{1,4})\s*[-–—]\s*(\d{1,4})\s*[.\s]', _rest
                )
                if _range_sec_m:
                    _rlo = int(_range_sec_m.group(1))
                    _rhi = int(_range_sec_m.group(2))
                    if _rlo < 1900 and _rhi < 1900 and _rhi > _rlo:
                        secondary = _rlo
                        volume_label = f'{_rlo}-{_rhi}'
                        _rest = _rest[_range_sec_m.end():]
                if not secondary:
                    _sec_m = re.search(r'(?<![\d\-–—])(\d{1,4})\s*\.', _rest)
                    if _sec_m:
                        _sc2 = int(_sec_m.group(1))
                        if _sc2 < 1900:
                            secondary = _sc2
                            _rest = _rest[_sec_m.end():]
                if not secondary:
                    # Десятичный суффикс: «14.1 Название» → stem начинается с «N.M».
                    # _STEM_NUM_RE поглотил «N.», оставив «M Название» в _rest без точки.
                    # Проверяем прямо в стеме (не в _rest) чтобы не захватить «5. 10 лет».
                    _stem_dec_m = re.match(r'^\d{1,4}\.(\d{1,2})\b', stem)
                    if _stem_dec_m:
                        _sc3 = int(_stem_dec_m.group(1))
                        if 1 <= _sc3 <= 19:
                            secondary = _sc3
                            _rest = stem[_stem_dec_m.end():]
                # Ищем tertiary только в остатке стема (_rest), не в полном стеме.
                # Передача stem как fallback приводит к двойному счёту:
                # "Цикл «Ермак». Том 1" → num=1 из " 1$", _rest="", но stem содержит
                # "Том 1" → tertiary=1, итог sk=(0,1,0,1) вместо (0,1,0,0).
                tertiary = self._extract_inline_volume_number(_rest, '') or 0
                if secondary or tertiary:
                    return (0, num, secondary, tertiary), 'filename', False, volume_label
                return (0, num, 0, 0), 'filename', False, str(num)

        # Источник В: диапазон томов в скобках внутри stem — «(Серия 1-3)», «(4-6)»
        # Используем MIN как позицию сортировки: файл (1-3) → 1, файл (4-6) → 4
        range_m = re.search(
            r'\((?:[^()]*?\s)?(\d{1,4})\s*[-–—]\s*(\d{1,4})\)', stem
        )
        if range_m:
            lo, hi = range_m.group(1), range_m.group(2)
            return (0, int(lo), 0, 0), 'filename_range', False, f'{lo}-{hi}'

        # Источник Г: ключевое слово внутри title/stem («Свиток 1», «Том 3» …)
        # Если file_title совпадает с именем серии — он не несёт информации о конкретном
        # томе (например, «Тысяча и одна ночь. В 12 томах» для всех 12 файлов).
        # В таком случае используем только stem, где есть реальный номер тома.
        _ft = rec.file_title or ''
        _proposed = getattr(rec, 'proposed_series', '') or ''
        _ft_is_series = bool(_ft) and _norm_key(_ft) == _norm_key(_proposed)

        # Паттерн N.M (том.часть) — проверяем первым, до других inline-методов.
        # Пример: «Расходники 1.2_Название» → том=1, часть=2.
        dp = self._extract_dot_part(stem, _ft or '')
        if dp is not None:
            vol, part = dp
            return (0, vol, part, 0), 'dot_part', False, f'{vol}.{part}'

        # Проверяем паттерн «Том N. Часть M» до общего inline-поиска,
        # чтобы «Часть» не интерпретировалась как ключевое слово тома.
        vp = self._extract_volume_part(_ft or stem, stem)
        if vp is not None:
            vol, part = vp
            return (0, vol, part, 0), 'volume_part', False, str(vol)

        inline = self._extract_inline_volume_number(
            stem if _ft_is_series else (_ft or stem), stem
        )
        if inline is not None:
            return (0, inline, 0, 0), 'inline_title', False, str(inline)

        # Уровень 3: дата из FB2 title-info
        year = self._extract_year_from_fb2(abs_path, section='title-info')
        if year:
            return (2, year, 0, 0), 'title_date', False, str(year)

        # Уровень 4: дата из publish-info
        year = self._extract_year_from_fb2(abs_path, section='publish-info')
        if year:
            return (3, year, 0, 0), 'publish_date', False, str(year)

        # Порядок не определён
        return (9, 0, 0, 0), 'unknown', True, ''

    def _sort_key_for_subseries(
        self, rec, sn: str, stem: str
    ) -> Optional[Tuple]:
        """Подсерия: позиция (primary=родитель, secondary=подсерия, tertiary=том).

        Пример: «Остен Ард 3\\Последний 1\\Корона. Том 1» → (0, 3, 1, 1).
        Возвращает sort_key tuple если удалось определить позицию, иначе None.
        """
        # primary: номер родительской серии из proposed_series или из стема
        _root_part = (rec.proposed_series or '').split('\\')[0].strip()
        _parent_num_m = re.search(r'\s(\d{1,4})\s*$', _root_part)
        if _parent_num_m:
            parent_num = int(_parent_num_m.group(1))
        else:
            _root_re = re.compile(re.escape(_root_part) + r'\s+(\d{1,4})', re.IGNORECASE | re.UNICODE)
            _root_m = _root_re.search(stem)
            _c = int(_root_m.group(1)) if _root_m else 0
            parent_num = _c if _c and _c < 1900 else 0
        # Если корень не дал числа — пробуем ведущее число ПОДСЕРИИ:
        # «Отзвуки серебряного ветра\1. Мы — были!» → '1' из начала подсерии.
        if not parent_num and '\\' in (rec.proposed_series or ''):
            _sub_leading_part = (rec.proposed_series or '').split('\\', 1)[1].strip()
            _sub_lead_m = re.match(r'^(\d{1,4})[.\s]', _sub_leading_part)
            if _sub_lead_m:
                _pl = int(_sub_lead_m.group(1))
                if _pl < 1900:
                    parent_num = _pl
            elif re.match(r'^([А-ЯЁ])\.\s', _sub_leading_part):
                # Кириллическая буквенная нумерация: А.=1, Б.=2, В.=3 … (без Ё)
                _CYR_ORD = 'АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ'
                _letter = _sub_leading_part[0].upper()
                _idx = _CYR_ORD.find(_letter)
                if _idx >= 0:
                    parent_num = _idx + 1

        # secondary: номер подсерии внутри позиции родителя
        sub_ordinal = 0
        subseries_name = (rec.proposed_series or '').split('\\')[-1].strip()
        if subseries_name:
            _sub_re = re.compile(
                re.escape(subseries_name) + r'\s+(\d{1,4})',
                re.IGNORECASE | re.UNICODE,
            )
            _sm = _sub_re.search(stem) or _sub_re.search(rec.file_title or '')
            if _sm:
                _sc = int(_sm.group(1))
                if _sc < 1900:
                    sub_ordinal = _sc

        # tertiary: номер тома внутри подсерии («Том N», «Книга N»)
        inline = self._extract_inline_volume_number(rec.file_title or stem, stem) or 0

        # Fallback для подсерий без числа в корне: ведущее число stem — позиция подсерии.
        if not parent_num and not sub_ordinal and not inline:
            _fn_m = self._STEM_NUM_RE.match(stem)
            if _fn_m:
                _fn_n = int(next(g for g in _fn_m.groups() if g is not None))
                if _fn_n and _fn_n < 1900:
                    sub_ordinal = _fn_n

        # Fallback когда parent_num известен (из «N. Подсерия») но позиция внутри
        # подсерии не определена: ведущее число stem («1. Название») = sub_ordinal.
        # Пример: «Мир\3. Хикки» + файл «1. Чертова дюжина.fb2» → sub_ordinal=1.
        if parent_num and not sub_ordinal and not inline:
            _fn_m = self._STEM_NUM_RE.match(stem)
            if _fn_m:
                _fn_n = int(next(g for g in _fn_m.groups() if g is not None))
                if _fn_n and _fn_n < 1900 and _fn_n != parent_num:
                    sub_ordinal = _fn_n

        # Метаданные как fallback для sub_ordinal
        if not sub_ordinal and not inline and sn:
            meta_s_low = (rec.metadata_series or '').strip().lower().replace('ё', 'е')
            sub_name_low = subseries_name.lower().replace('ё', 'е')
            if meta_s_low and sub_name_low and (
                meta_s_low == sub_name_low
                or meta_s_low in sub_name_low
                or sub_name_low in meta_s_low
            ):
                if re.match(r'^\d+$', sn):
                    sub_ordinal = int(sn)

        # Если в имени подсерии есть диапазон «(Слово N-M)» и корень серии
        # не имеет собственного числа, используем lo диапазона как реальную позицию.
        if not parent_num and sub_ordinal:
            _sub_range_m = re.search(
                r'\(\s*(?:\w+\s+)?(\d{1,4})\s*[-–—]\s*(\d{1,4})\s*\)',
                subseries_name,
            )
            if _sub_range_m:
                _lo_r = int(_sub_range_m.group(1))
                if _lo_r and _lo_r < 1900:
                    _eff_pos = _lo_r + sub_ordinal - 1
                    return (0, _eff_pos, 0, 0), 'subseries_range', False, str(_eff_pos)

        if parent_num or sub_ordinal or inline:
            _lbl = str(parent_num)
            if sub_ordinal:
                _lbl += f'.{sub_ordinal}'
            if inline:
                _lbl += f'.{inline}'
            _src = 'subseries_number' if sub_ordinal else ('inline_title' if inline else 'parent_num')
            return (0, parent_num, sub_ordinal, inline), _src, False, _lbl

        return None

    def _extract_date_from_fb2(self, path: Path, section: str = 'title-info') -> Optional[str]:
        """Извлечь дату из <date> внутри указанной секции FB2.

        Возвращает строку вида 'YYYY-MM-DD' или 'YYYY' — пригодную для
        лексикографического сравнения (более поздняя дата > ранняя).
        Возвращает None если дата не найдена или файл недоступен.
        """
        try:
            if not path.exists():
                return None
            chunk = path.read_bytes()[:8192]
            try:
                text = chunk.decode('utf-8', errors='replace')
            except Exception:
                text = chunk.decode('cp1251', errors='replace')

            sec_m = re.search(
                rf'<(?:fb:)?{re.escape(section)}>(.*?)</(?:fb:)?{re.escape(section)}>',
                text, re.DOTALL | re.IGNORECASE,
            )
            if not sec_m:
                return None
            sec_text = sec_m.group(1)

            # Предпочитаем атрибут value="YYYY-MM-DD" как наиболее точный
            m = re.search(
                r'<(?:fb:)?date[^>]*value=["\'](\d{4}(?:-\d{2}(?:-\d{2})?)?)["\']',
                sec_text, re.IGNORECASE,
            )
            if m:
                return m.group(1)
            # Fallback: текстовое содержимое тега <date>YYYY-MM-DD</date>
            m = re.search(
                r'<(?:fb:)?date[^>]*>(\d{4}(?:-\d{2}(?:-\d{2})?)?)</(?:fb:)?date>',
                sec_text, re.IGNORECASE,
            )
            if m:
                return m.group(1)
        except Exception:
            pass
        return None

    def _extract_year_from_fb2(self, path: Path, section: str) -> Optional[int]:
        """Извлечь год из <date> внутри указанной секции FB2."""
        try:
            if not path.exists():
                return None
            raw = path.read_bytes()
            # Минимальное чтение — только первые 8 КБ (метаданные в начале)
            chunk = raw[:8192]
            try:
                text = chunk.decode('utf-8', errors='replace')
            except Exception:
                text = chunk.decode('cp1251', errors='replace')

            # Найти нужную секцию
            sec_m = re.search(
                rf'<(?:fb:)?{re.escape(section)}>(.*?)</(?:fb:)?{re.escape(section)}>',
                text, re.DOTALL | re.IGNORECASE
            )
            if not sec_m:
                return None
            sec_text = sec_m.group(1)

            # <date value="YYYY-..."> или <date>YYYY</date>
            date_m = re.search(
                r'<(?:fb:)?date[^>]*value=["\'](\d{4})', sec_text, re.IGNORECASE
            ) or re.search(
                r'<(?:fb:)?date[^>]*>(\d{4})', sec_text, re.IGNORECASE
            )
            if date_m:
                return int(date_m.group(1))
        except Exception:
            pass
        return None

    @staticmethod
    def _book_eff_pos(book: 'CompilationBook') -> int:
        """Эффективная позиция книги в серии.

        Для подсерий без номера в корне (sk[1]=0) позиция хранится в
        sk[2] или sk[3]. Возвращаем первый ненулевой компонент после sk[1].
        """
        sk = book.sort_key
        if sk[0] != 0:
            return 0
        if sk[1]:
            return sk[1]
        return next((sk[i] for i in range(2, len(sk)) if sk[i] > 0), 0)

    def _split_into_consecutive_runs(
        self,
        books: List[CompilationBook],
    ) -> List[List[CompilationBook]]:
        """Разбить числовые (level-0) книги на непрерывные подгруппы без пропусков.

        На вход ожидаются ТОЛЬКО книги с sort_key[0] == 0 (series_number / filename).
        Предкомпилированные файлы с диапазоном (volume_label="1-3") учитываются по
        верхней границе: следующий файл с lo <= hi+1 считается непрерывным или
        перекрывающимся (и тогда объединяется в один блок для компиляции).
        Пример: [1-42] + [31-45]: lo=31 <= 43 → один блок → итог 1-45.
        Пример: [1-5]  + [6-9]:  lo=6  <= 6  → один блок → итог 1-9.
        Пример: [1-5]  + [7-9]:  lo=7  > 6   → разные блоки (пропуск тома 6).
        """
        if not books:
            return []

        def get_hi(book: CompilationBook) -> int:
            # Если volume_label — диапазон внутри подсерии (sort_key[2] != 0),
            # раскрывать его как верхнеуровневый диапазон нельзя:
            # используем только sort_key[1] (позицию в родительской серии).
            # Исключение: sort_key[1]=0 означает подсерию без номера в корне —
            # тогда sort_key[2] является фактической позицией книги.
            if len(book.sort_key) > 2 and book.sort_key[2] != 0:
                return FB2CompilerService._book_eff_pos(book)
            rng = re.match(r'^(\d+)\s*[-–—]\s*(\d+)$', book.volume_label or '')
            return int(rng.group(2)) if rng else book.sort_key[1]

        runs: List[List[CompilationBook]] = []
        current_run: List[CompilationBook] = [books[0]]
        prev_hi = get_hi(books[0])

        for book in books[1:]:
            # sort_key[1]=0 с sort_key[2]>0 = подсерия без номера в корне
            lo = self._book_eff_pos(book)
            if lo <= prev_hi + 1:  # следующий или перекрывающийся диапазон
                current_run.append(book)
                prev_hi = get_hi(book)
            else:
                runs.append(current_run)
                current_run = [book]
                prev_hi = get_hi(book)
        runs.append(current_run)

        return runs

    def _sort_books(
        self, books: List[CompilationBook]
    ) -> Tuple[List[CompilationBook], bool, bool]:
        """Отсортировать книги и определить, однозначен ли порядок.

        Returns:
            (sorted_books, order_determined, alphabetical_order)
            alphabetical_order=True — у всех книг неизвестная позиция,
            отсортированы по названию как единственный детерминированный вариант.
        """
        all_ambiguous = all(b.order_ambiguous for b in books)

        if all_ambiguous:
            # Нет нумерации — сортируем по названию (алфавитный порядок)
            sorted_books = sorted(
                books,
                key=lambda b: (b.record.file_title or b.abs_path.stem).lower(),
            )
            return sorted_books, True, True

        has_ambiguous = any(b.order_ambiguous for b in books)

        def _eff_sort_key(b: CompilationBook):
            """Эффективный ключ сортировки.

            Для подсерий без числа в корне (sort_key[1]=0, sort_key[2]>0)
            используем sort_key[2] как позицию в основной серии — это позволяет
            корректно строить непрерывные run'ы рядом с arc-файлами.
            Предкомпилированные диапазоны (volume_label="N-M") сортируются перед
            одиночными книгами на той же позиции — так dedup сохраняет диапазон,
            а не одиночную книгу (иначе теряется контент за пределами диапазона).
            """
            eff = FB2CompilerService._book_eff_pos(b)
            _is_frac_vl = bool(re.match(r'^\d+\.\d+$', b.volume_label or ''))
            sk = (0, eff, 0, 0) if b.sort_key[0] == 0 and b.sort_key[1] == 0 and eff > 0 and not _is_frac_vl else b.sort_key
            rng_m = re.match(r'^(\d+)\s*[-–—]\s*(\d+)$', b.volume_label or '')
            range_hi_neg = -int(rng_m.group(2)) if rng_m else 0  # более широкий диапазон → меньше → первым
            return (
                sk,
                range_hi_neg,
                0 if re.match(r'^\d', b.abs_path.stem) else 1,
                (b.record.file_title or b.abs_path.stem).lower(),
                str(b.abs_path),
            )

        sorted_books = sorted(books, key=_eff_sort_key)
        return sorted_books, not has_ambiguous, False

    def _compute_volume_range(self, books: List[CompilationBook]) -> str:
        """Вернуть строку диапазона томов, например '1-7'."""
        nums = []
        for b in books:
            level = b.sort_key[0]
            val = b.sort_key[1]
            if level == 0 and isinstance(val, int):
                nums.append(val)

        # Также из volume_label (для прекомпилированных диапазонов типа "3-5").
        # Раскрываем только когда sort_key[2] == 0: volume_label — верхнеуровневый диапазон.
        # Если sort_key[2] != 0, volume_label — диапазон внутри подсерии (вторичный индекс),
        # его нельзя добавлять как позиции верхнего уровня серии.
        for b in books:
            if len(b.sort_key) > 2 and b.sort_key[2] != 0:
                continue
            vl = (b.volume_label or '').strip()
            rng = re.match(r'^(\d+)\s*[-–—]\s*(\d+)$', vl)
            if rng:
                lo2, hi2 = int(rng.group(1)), int(rng.group(2))
                nums.extend(range(lo2, hi2 + 1))

        # Также из series_number самой записи (может быть уже диапазоном "1-3")
        for b in books:
            sn = (b.record.series_number or '').strip()
            rng = re.match(r'^(\d+)\s*[-–]\s*(\d+)$', sn)
            if rng:
                lo, hi = int(rng.group(1)), int(rng.group(2))
                nums.extend(range(lo, hi + 1))

        if not nums:
            return ''
        lo, hi = min(nums), max(nums)
        if lo == hi:
            return str(lo)
        # Проверяем: все тома от lo до hi реально присутствуют (нет пробелов)?
        present = set(nums)
        if all(v in present for v in range(lo, hi + 1)):
            return f'{lo}-{hi}'
        # Есть пробелы — не создаём ложный диапазон, возвращаем пустую строку
        return ''

    # ------------------------------------------------------------------
    # Компиляция
    # ------------------------------------------------------------------

    def compile_group(
        self,
        group: CompilationGroup,
        output_dir: Optional[Path],
        delete_sources: bool = False,
    ) -> CompilationResult:
        """Скомпилировать группу в один FB2-файл.

        Args:
            group: Группа книг для компиляции.
            output_dir: Папка, куда поместить результирующий файл.
                        None — сохранить рядом с исходными файлами.
            delete_sources: Удалить исходники после успешной компиляции.

        Returns:
            CompilationResult с результатами.
        """
        self._log(f"Компиляция: {group.author} / {group.series} ({len(group.books)} книг)")

        # Cleanup-only: новая компиляция не нужна, только удалить устаревшие файлы
        # + переименовать файл-компиляцию по нашей схеме именования (если нужно).
        if getattr(group, 'cleanup_only', False):
            if group.duplicate_paths:
                self._delete_sources(group.duplicate_paths)
                self._log(f"   ♻ Удалено {len(group.duplicate_paths)} устаревших файлов")

            renamed_path = None
            if group.kept_paths:
                old_path = group.kept_paths[0]
                vol_m = re.match(r'^(\d+)-(\d+)$', group.volume_range or '')
                if vol_m and old_path.exists():
                    lo, hi = int(vol_m.group(1)), int(vol_m.group(2))
                    n_volumes = hi - lo + 1
                    suffix = self._series_suffix(
                        n_volumes, lo, hi, 0,
                        series_complete=getattr(group, 'series_complete', True),
                    )
                    clean_series = self._clean_series_name(group.series)
                    safe_author = re.sub(r'[\\/:*?"<>|]', '_', group.author)
                    safe_series = re.sub(r'[/:*?"<>|]', '_',
                                        self._series_to_display(clean_series))
                    suffix = self._suppress_redundant_suffix(safe_series, suffix)
                    new_name = f"{safe_author} - {safe_series} ({suffix}).fb2" if suffix else f"{safe_author} - {safe_series}.fb2"
                    new_path = old_path.parent / new_name
                    if old_path != new_path:
                        try:
                            old_path.rename(new_path)
                            group.kept_paths[0] = new_path
                            renamed_path = new_path
                            self._log(f"   ✎ Переименован: {old_path.name} → {new_name}")
                        except OSError as e:
                            self._log(f"   ✗ Не удалось переименовать {old_path.name}: {e}")

            return CompilationResult(
                group=group,
                output_path=renamed_path,
                books_compiled=0,
                source_paths=list(group.duplicate_paths),
                success=True,
                error="",
            )

        try:
            # --- Читаем содержимое каждого файла с учётом перекрытий диапазонов ---
            # Если два файла — предкомпиляции с перекрывающимися диапазонами
            # (например [1-42] и [31-45]), берём из второго только те секции,
            # которые не покрыты первым (тома 43-45).
            bodies: List[Tuple[str, str]] = []  # (book_title, body_xml)
            covered_hi = 0  # максимальный номер тома, уже добавленного в bodies
            # Бинари (обложки, иллюстрации) из всех исходников; дедупликация по id
            collected_binaries: List[str] = []
            seen_binary_ids: set = set()

            cover_image_id: Optional[str] = None  # ID бинаря обложки первой книги (для <coverpage>)
            # book_cover_ids[i] — обложка i-го элемента bodies (параллельный список).
            # Заполняется одновременно с bodies, поэтому len == len(bodies) всегда.
            book_cover_ids: List[Optional[str]] = []

            _excluded_set = {p.resolve() for p in (group.excluded_paths or [])}

            for book_idx, book in enumerate(group.books, 1):
                if _excluded_set and book.abs_path.resolve() in _excluded_set:
                    book_cover_ids.append(None)
                    continue
                # Префикс для бинарей этой книги — исключает коллизии ID между томами
                vol_prefix = f'vol{book_idx}_'

                # Собираем бинари: переименовываем id в vol_N_<orig_id>.
                # seen_binary_ids предотвращает дубликаты если один и тот же id
                # встречается дважды внутри одного исходника.
                id_remap: dict = {}  # orig_id -> new_id
                for bin_block in self._extract_binaries(book):
                    id_m = re.search(r'<binary([^>]+)id=["\']([^"\']+)["\']', bin_block, re.IGNORECASE)
                    if not id_m:
                        continue
                    orig_id = id_m.group(2)
                    new_id = vol_prefix + orig_id
                    if new_id in seen_binary_ids:
                        id_remap[orig_id] = new_id  # remap нужен, но бинарь уже есть
                        continue
                    seen_binary_ids.add(new_id)
                    id_remap[orig_id] = new_id
                    # Заменяем id в теге <binary>
                    new_block = re.sub(
                        r'(<binary[^>]+id=["\'])' + re.escape(orig_id) + r'(["\'])',
                        lambda m, nid=new_id: m.group(1) + nid + m.group(2),  # noqa: B023
                        bin_block, count=1, flags=re.IGNORECASE,
                    )
                    collected_binaries.append(new_block)

                # Вычисляем cover_id этой книги — используем ниже при добавлении body.
                book_cover_id: Optional[str] = None
                if id_remap:
                    cover_orig = self._extract_coverpage_id(book)
                    if cover_orig and cover_orig in id_remap:
                        book_cover_id = id_remap[cover_orig]
                if book_idx == 1:
                    cover_image_id = book_cover_id
                # Примечание: book_cover_ids.append вызывается только вместе с bodies.append
                # чтобы len(book_cover_ids) == len(bodies) всегда.

                def _remap_image_refs(xml: str, remap: dict = id_remap) -> str:
                    """Обновить все <image l:href="#orig"> → <image l:href="#new">."""
                    def _sub(m):
                        ref = m.group(1)
                        bare = ref.lstrip('#')
                        new = remap.get(bare)
                        return m.group(0).replace(ref, '#' + new) if new else m.group(0)
                    return re.sub(r'l:href="(#?[^"]+)"', _sub, xml, flags=re.IGNORECASE)

                rng_m = re.match(r'^(\d+)\s*[-–—]\s*(\d+)$', book.volume_label or '')
                if rng_m:
                    # Предкомпиляция с известным диапазоном — разбиваем на секции
                    b_lo, b_hi = int(rng_m.group(1)), int(rng_m.group(2))
                    sections = self._extract_body_sections(book, b_lo, b_hi)
                    if not sections:
                        raise RuntimeError(
                            f"Не удалось извлечь секции из: {book.abs_path.name}"
                        )
                    # Берём только секции, ещё не покрытые предыдущими файлами
                    to_add = [(v, t, bx) for v, t, bx in sections if v > covered_hi]
                    if not to_add:
                        self._log(f"  ℹ Пропуск {book.abs_path.name} — полностью покрыт предыдущим файлом")
                        continue
                    skipped = len(sections) - len(to_add)
                    if skipped:
                        first_new = min(v for v, _, _ in to_add)
                        self._log(
                            f"  ✂ {book.abs_path.name}: пропускаем {skipped} томов "
                            f"(уже покрыты до тома {covered_hi}), "
                            f"берём {len(to_add)} томов начиная с {first_new}"
                        )
                    for i, (_vol, sec_title, sec_body) in enumerate(to_add):
                        remapped_body = _remap_image_refs(sec_body)
                        bodies.append((sec_title, remapped_body))
                        # Обложка секции: ищем dedicated cover-image внутри тела.
                        # Для нашего формата это <section><image/></section> перед контентом.
                        # Для внешних предкомпиляций — первый <image> в секции.
                        sec_cover = self._extract_section_cover_id(sec_body, id_remap)
                        if sec_cover is None and i == 0:
                            # Фоллбек: общая обложка предкомпиляции для первой секции
                            sec_cover = book_cover_id
                        book_cover_ids.append(sec_cover)
                    covered_hi = max(covered_hi, b_hi)
                else:
                    # Обычная книга — берём целиком.
                    # Если позиция уже покрыта предкомпиляцией (covered_hi ≥ sn),
                    # пропускаем: контент этого тома уже есть в ранее добавленном диапазоне.
                    sn = book.sort_key[1] if book.sort_key[0] == 0 else 0
                    if sn and sn <= covered_hi:
                        self._log(f"  ℹ Пропуск {book.abs_path.name} — позиция {sn} покрыта до {covered_hi}")
                        continue
                    title, body_xml = self._extract_body(book)
                    bodies.append((title, _remap_image_refs(body_xml)))
                    book_cover_ids.append(book_cover_id)
                    if sn:
                        covered_hi = max(covered_hi, sn)

            # Если все книги исключены или группа пустая — ничего компилировать не нужно.
            if not group.books or not bodies:
                if group.duplicate_paths:
                    self._delete_sources(group.duplicate_paths)
                return CompilationResult(
                    group=group,
                    output_path=None,
                    books_compiled=0,
                    source_paths=[b.abs_path for b in group.books],
                    success=True,
                    error='',
                )

            # --- Извлекаем метаданные из первой (или лучшей) книги ---
            meta = self._extract_metadata(group.books[0])

            # --- Статистика run'а и именование ---
            clean_series = self._clean_series_name(group.series)
            safe_author = re.sub(r'[\\/:*?"<>|]', '_', group.author)

            part_count = getattr(group, 'part_count', 0)
            top_lo, top_hi, n_volumes, has_subseries, n_top_arcs = self._run_stats(group.books)

            safe_series = re.sub(r'[/:*?"<>|]', '_', self._series_to_display(clean_series))

            # --- Папка назначения: явная или рядом с исходниками ---
            dest_dir = output_dir if output_dir is not None else group.books[0].abs_path.parent

            # --- Суффикс и XML ---
            # Позиция run'а идёт в суффикс: полная серия → слово, частичная → «т. N-M».
            # Если группа содержит подсерии, слово выбирается по числу верхних дуг (n_top_arcs),
            # а не по общему числу книг, чтобы «Пенталогия» (5 дуг) + «в 9 книгах» (9 файлов).
            # Если пользователь исключил книги — серия неполная, всегда т. N-M

            # Для компиляций из arc-позиционных предкомпиляций вычисляем суммарное
            # число томов по сервисным словам (Дилогия→2, Трилогия→3 и т.д.).
            # Пример: Сафари 1 (Дилогия) + Сафари 2 (Трилогия) + Сафари 3 (Трилогия)
            # → arc_count=3, total_books=8 → «Трилогия в 8 книгах».
            _arc_part_count = 0
            # Arc-unit: книга либо является arc-point предкомпиляцией (lo==hi>0),
            # либо занимает ровно одну плоскую arc-позицию (sk=(0,N,0,0)).
            # Второй случай позволяет считать «в N книгах» даже когда одна дуга
            # представлена одиночным файлом без сервисного слова в имени.
            def _is_arc_unit(b: 'CompilationBook') -> bool:
                lo, hi = self._precompiled_range(b, group.series)
                if lo == hi > 0:
                    return True
                return (b.sort_key[0] == 0 and b.sort_key[1] > 0
                        and b.sort_key[2] == 0)

            _all_arc_point = bool(group.books) and all(_is_arc_unit(b) for b in group.books)
            if _all_arc_point:
                _swords_idx = {kw.lower(): idx
                               for idx, kw in enumerate(self._SERIES_WORDS) if kw}
                _swords_pat = re.compile(
                    '|'.join(re.escape(kw) for kw in _swords_idx),
                    re.IGNORECASE | re.UNICODE,
                )
                for b in group.books:
                    lo, hi = self._precompiled_range(b, group.series)
                    if lo == hi > 0:
                        # Arc-point предкомпиляция: считаем по сервисному слову/диапазону
                        _st = (b.abs_path.stem + ' ' + (b.record.file_title or '')).lower()
                        _m = _swords_pat.search(_st)
                        if _m:
                            _arc_part_count += _swords_idx[_m.group(0).lower()]
                        else:
                            _rng_in_stem = re.search(r'(\d+)\s*[-–—]\s*(\d+)', b.abs_path.stem)
                            if _rng_in_stem:
                                _r_lo, _r_hi = int(_rng_in_stem.group(1)), int(_rng_in_stem.group(2))
                                if _r_hi > _r_lo and _r_hi - _r_lo < 50:
                                    _arc_part_count += _r_hi - _r_lo + 1
                                else:
                                    _arc_part_count += 1
                            else:
                                _arc_part_count += 1
                    else:
                        # Одиночная книга на плоской arc-позиции → 1 книга
                        _arc_part_count += 1
                if _arc_part_count <= n_volumes:
                    _arc_part_count = 0  # не имеет смысла если не больше числа arc'ов

            # Проверяем пробелы в top-level arc-позициях.
            # Для предкомпиляций используем volume_label («1-2», «3-4»…): если hi+1 >= lo
            # следующего диапазона — пробела нет. Иначе проверяем по позициям.
            def _vl_hi(b: 'CompilationBook') -> int:
                rng = re.match(r'^(\d+)\s*[-–—]\s*(\d+)$', b.volume_label or '')
                return int(rng.group(2)) if rng else b.sort_key[1]

            _arc_books_sorted = sorted(
                [b for b in group.books if b.sort_key[0] == 0 and b.sort_key[1]],
                key=lambda b: b.sort_key[1],
            )
            if len(_arc_books_sorted) < 2:
                _arc_has_gaps = False
            else:
                _arc_has_gaps = any(
                    _arc_books_sorted[i].sort_key[1] > _vl_hi(_arc_books_sorted[i - 1]) + 1
                    for i in range(1, len(_arc_books_sorted))
                )

            _sc_compile = not (group.excluded_paths or group.auto_excluded_paths) and getattr(group, 'series_complete', True)
            _has_exclusions = bool(group.excluded_paths or group.auto_excluded_paths)
            # Arc-point группы с неполной серией → «ч. N в K книгах»
            _arc_partial = _all_arc_point and _arc_part_count > 0 and not getattr(group, 'series_complete', True)
            if _has_exclusions:
                _lbl = 'ч.' if (has_subseries and n_top_arcs and n_top_arcs >= 2) else 'т.'
                suffix = f'{_lbl} {top_lo}' if top_lo == top_hi else f'{_lbl} {top_lo}-{top_hi}'
            elif _arc_has_gaps:
                _total = _arc_part_count or n_volumes
                suffix = f'в {_total} книгах'
            elif _arc_partial:
                _lbl = 'ч.'
                _base = f'{_lbl} {top_lo}' if top_lo == top_hi else f'{_lbl} {top_lo}-{top_hi}'
                suffix = f'{_base} в {_arc_part_count} книгах'
            elif has_subseries and n_top_arcs and n_top_arcs >= 2:
                suffix = self._series_suffix(n_top_arcs, top_lo, top_hi,
                                             _arc_part_count or n_volumes, use_parts=True)
            elif has_subseries and n_top_arcs == 1 and n_volumes > 1 and top_lo > 1:
                # Одна дуга внутри многодуговой серии, arc-позиция > 1:
                # «ч. 2 в 3 книгах» — показывает и позицию в родителе, и объём.
                suffix = f'ч. {top_lo} в {n_volumes} книгах'
            else:
                suffix = self._series_suffix(n_volumes, top_lo, top_hi,
                                             _arc_part_count or part_count)
            # Реальный диапазон томов для <sequence number> в метаданных.
            # top_lo=0 означает что сортировка через sort_key[2] (подсерии без числа в корне).
            _eff_lo = top_lo if top_lo else 1
            _eff_hi = top_hi if top_hi else _eff_lo
            _vol_range = f'{_eff_lo}' if _eff_lo == _eff_hi else f'{_eff_lo}-{_eff_hi}'
            first_book = group.books[0] if group.books else None
            annotation_xml = self._extract_annotation_xml(first_book) if first_book else ''
            output_xml = self._build_fb2(
                author=group.author,
                series=clean_series,
                suffix=suffix,
                genre=meta.get('genre', ''),
                bodies=bodies,
                binaries=collected_binaries,
                cover_image_id=cover_image_id,
                book_cover_ids=book_cover_ids,
                volume_range=_vol_range,
                annotation=annotation_xml,
            )

            # --- Имя выходного файла ---
            suffix = self._suppress_redundant_suffix(safe_series, suffix)
            fname = f"{safe_author} - {safe_series} ({suffix}).fb2" if suffix else f"{safe_author} - {safe_series}.fb2"

            output_path = dest_dir / fname
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output_xml, encoding='utf-8')

            self._log(f"  ✓ Создан файл: {output_path.name}")

            # --- Удаляем дубликаты (всегда, безусловно) ---
            if group.duplicate_paths:
                self._delete_sources(group.duplicate_paths)
                self._log(f"   ♻ Удалено {len(group.duplicate_paths)} дубликатах")

            # --- Удаляем исходники ---
            # Исключённые вручную файлы не удаляем (excluded_paths)
            source_paths = [b.abs_path for b in group.books
                            if b.abs_path.resolve() not in _excluded_set]
            if delete_sources:
                self._delete_sources(source_paths)

            return CompilationResult(
                group=group,
                output_path=output_path,
                books_compiled=len(bodies),
                source_paths=source_paths,
                success=True,
            )

        except Exception as e:
            self._log(f"  ✗ Ошибка компиляции {group.series}: {e}")
            return CompilationResult(
                group=group,
                output_path=Path(''),
                books_compiled=0,
                source_paths=[b.abs_path for b in group.books],
                success=False,
                error=str(e),
            )

    def _read_file_text(self, path: Path) -> str:
        """Прочитать файл с автоопределением кодировки. Поддерживает zip-упакованные FB2."""
        raw = path.read_bytes()
        # Zip-упакованный FB2 (сигнатура PK): распаковываем первый .fb2-файл внутри
        if raw[:2] == b'PK':
            import zipfile, io as _io
            try:
                with zipfile.ZipFile(_io.BytesIO(raw)) as zf:
                    names = zf.namelist()
                    fb2_name = next((n for n in names if n.lower().endswith('.fb2')), names[0] if names else None)
                    if fb2_name:
                        raw = zf.read(fb2_name)
            except Exception:
                pass
        # Определяем кодировку из XML-декларации
        declared = None
        m = re.search(
            rb'<\?xml[^>]*encoding\s*=\s*["\']([^"\']+)["\']', raw[:256], re.IGNORECASE
        )
        if m:
            declared = m.group(1).decode('ascii', errors='ignore')

        for enc in filter(None, [declared, 'utf-8-sig', 'utf-8', 'cp1251']):
            try:
                return raw.decode(enc, errors='strict')
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode('utf-8', errors='replace')

    def _extract_body(self, book: CompilationBook) -> Tuple[str, Optional[str]]:
        """Извлечь заголовок книги и главный <body> блок (без notes/footnotes)."""
        title = (book.record.file_title or '').strip() or book.abs_path.stem

        if not book.abs_path.exists():
            raise RuntimeError(f"Файл не найден: {book.abs_path.name} (путь: {book.abs_path})")

        try:
            text = self._read_file_text(book.abs_path)
        except Exception as e:
            raise RuntimeError(f"Ошибка чтения {book.abs_path.name}: {e}") from e

        all_bodies = re.findall(
            r'<(?:fb:)?body(?:\s[^>]*)?>.*?</(?:fb:)?body>',
            text, re.DOTALL | re.IGNORECASE
        )
        if not all_bodies:
            raise RuntimeError(f"Тег <body> не найден в {book.abs_path.name}")

        # Берём только главные тела (без name="notes"/"footnotes").
        # <body name="notes"> — сноски; их включение создаёт вложенные <body> в итоговом файле.
        _NOTES_RE = re.compile(r'<(?:fb:)?body[^>]+\bname\s*=\s*["\'](?:notes|footnotes)["\']',
                               re.IGNORECASE)
        main_bodies = [b for b in all_bodies if not _NOTES_RE.match(b[:200])]
        bodies = main_bodies if main_bodies else all_bodies[:1]

        combined = '\n'.join(bodies)
        return title, combined

    # ---- вспомогательные методы для разбора секций предкомпиляций ----

    @staticmethod
    def _split_top_level_sections(body_xml: str) -> List[str]:
        """Извлечь секции первого уровня из тела FB2.

        Использует счётчик глубины вложенности, чтобы корректно обрабатывать
        вложенные <section>.  Возвращает список XML-фрагментов каждой секции.
        """
        sections: List[str] = []
        depth = 0
        start = 0
        tag_re = re.compile(
            r'<(/?)(?:fb:)?section(?:\s[^>]*)?>',
            re.IGNORECASE,
        )
        for m in tag_re.finditer(body_xml):
            is_close = bool(m.group(1))
            if not is_close:
                if depth == 0:
                    start = m.start()
                depth += 1
            else:
                if depth > 0:
                    depth -= 1
                if depth == 0 and start is not None:
                    sections.append(body_xml[start:m.end()])
                    start = None
        return sections

    @staticmethod
    def _detect_section_volume(section_xml: str) -> Optional[int]:
        """Попытаться определить номер тома внутри секции.

        Порядок проверки:
        1. <sequence number="N"> — прописывается нашим компилятором.
        2. Заголовок <title> содержит «Книга N» / «Том N» / «Часть N» /
           «Book N» / «Vol N» / «Part N» / «Tom N» (1–2 варианта).
        3. Римские цифры в заголовке: «Книга II» → 2.
        Возвращает int или None.
        """
        # Способ 1: <sequence number="N">
        m = re.search(
            r'<(?:fb:)?sequence[^>]+number=["\'](\d+)["\']',
            section_xml, re.IGNORECASE,
        )
        if m:
            return int(m.group(1))

        # Способ 2: заголовок секции
        title_m = re.search(
            r'<(?:fb:)?title[^>]*>(.*?)</(?:fb:)?title>',
            section_xml, re.IGNORECASE | re.DOTALL,
        )
        if title_m:
            title_text = re.sub(r'<[^>]+>', '', title_m.group(1))
            # Нормализовать Unicode-символы римских цифр в ASCII: Ⅻ → XII, Ⅰ → I и т.п.
            title_text = unicodedata.normalize('NFKC', title_text)
            # Арабские цифры после ключевых слов
            kw_m = re.search(
                r'(?:книга|том|часть|book|vol(?:ume)?|part|том)\s*[.:\-]?\s*(\d+)',
                title_text, re.IGNORECASE,
            )
            if kw_m:
                return int(kw_m.group(1))
            # Римские цифры после ключевых слов
            # (?=[MDCLXVI]) гарантирует непустое совпадение;
            # V?I{0,3} вместо V?I{1,3} позволяет матчить V, X, XL, XLV и т.п.
            roman_m = re.search(
                r'(?:книга|том|часть|book|vol(?:ume)?|part)\s*[.:\-]?\s*'
                r'((?=[MDCLXVI])M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))',
                title_text, re.IGNORECASE,
            )
            if roman_m and roman_m.group(1):
                n = FB2CompilerService._roman_to_int(roman_m.group(1))
                if n:
                    return n
        return None

    def _extract_body_sections(
        self,
        book: CompilationBook,
        b_lo: int = 1,
        b_hi: int = 0,
    ) -> List[Tuple[int, str, str]]:
        """Разбить предкомпиляцию на секции: [(vol_num, title, body_xml), ...].

        vol_num — реальный номер тома в серии (b_lo … b_hi).

        Алгоритм:
        1. Если <body> блоков столько же, сколько томов (наш формат) —
           каждый <body> = один том, нумеруем с b_lo.
        2. Если один <body> (внешний формат) — пробуем разбить на top-level
           <section> и определить номер тома по <sequence> или заголовку.
           Если номера найдены и покрывают ≥70% секций — используем их.
           Иначе нумеруем секции последовательно с b_lo.
        3. Если секций нет — возвращаем один элемент (весь body).
        """
        stem = book.abs_path.stem
        if not book.abs_path.exists():
            return []
        try:
            text = self._read_file_text(book.abs_path)
        except Exception:
            return []

        all_raw_bodies = re.findall(
            r'<(?:fb:)?body(?:\s[^>]*)?>.*?</(?:fb:)?body>',
            text, re.DOTALL | re.IGNORECASE,
        )
        if not all_raw_bodies:
            return []

        # Отфильтровываем сноски — берём только главные тела.
        _NOTES_RE2 = re.compile(r'<(?:fb:)?body[^>]+\bname\s*=\s*["\'](?:notes|footnotes)["\']',
                                re.IGNORECASE)
        raw_bodies = [b for b in all_raw_bodies if not _NOTES_RE2.match(b[:200])]
        if not raw_bodies:
            raw_bodies = all_raw_bodies[:1]

        _title_re = re.compile(
            r'<(?:fb:)?title[^>]*>\s*<(?:fb:)?p[^>]*>(.*?)</(?:fb:)?p>',
            re.IGNORECASE | re.DOTALL,
        )

        def _body_title(xml: str, idx: int) -> str:
            m = _title_re.search(xml)
            return re.sub(r'<[^>]+>', '', m.group(1)).strip() if m else f'{stem} ({idx})'

        expected = b_hi - b_lo + 1 if b_hi >= b_lo else 0

        # --- Случай 1: наш формат (один <body> на том) ---
        if expected > 0 and len(raw_bodies) == expected:
            return [
                (b_lo + i, _body_title(bx, i + 1), bx)
                for i, bx in enumerate(raw_bodies)
            ]

        # --- Случай 2: один (или нестандартное количество) <body> ---
        # Объединяем все body, ищем top-level <section>
        all_content = '\n'.join(raw_bodies)
        top_sections = self._split_top_level_sections(all_content)

        if not top_sections:
            # Нет секций — возвращаем весь контент как один том
            title = (book.record.file_title or '').strip() or stem
            return [(b_lo, title, all_content)]

        # Regex для вырезания <title> из первой позиции внутри <section>.
        # Пример: <section>\n  <title><p>1. Книга I</p></title>\n  <p>текст...
        # После удаления: <section>\n  <p>текст...
        # Это предотвращает дублирование заголовка: наш <body><title> + оригинальный <section><title>.
        _SEC_TITLE_RE = re.compile(
            r'(<(?:fb:)?section(?:\s[^>]*)?>\s*)<(?:fb:)?title[^>]*>.*?</(?:fb:)?title>',
            re.IGNORECASE | re.DOTALL,
        )

        # Пробуем определить номера томов из содержимого секций
        detected: List[Tuple[Optional[int], str, str]] = []
        for i, sec_xml in enumerate(top_sections, 1):
            vol = self._detect_section_volume(sec_xml)
            raw_title = _body_title(sec_xml, i)
            # Убираем ведущий «N. » из заголовка секции — в _build_fb2 добавим свой индекс.
            # Без этого: "1. 1. Неудержимый. Книга I" вместо "1. Неудержимый. Книга I".
            title = re.sub(r'^\d+\.\s*', '', raw_title).strip() or raw_title
            # Вырезаем <title> из секции — он дублируется как <body><title> в итоговом файле.
            sec_clean = _SEC_TITLE_RE.sub(r'\1', sec_xml, count=1)
            detected.append((vol, title, f'<body>\n{sec_clean}\n</body>'))

        found_vols = [v for v, _, _ in detected if v is not None]
        use_detected = False
        if found_vols and expected > 0:
            in_range = sum(1 for v in found_vols if b_lo <= v <= b_hi)
            use_detected = in_range >= len(found_vols) * 0.7

        if use_detected:
            # Используем найденные номера; секции без номера пропускаем
            result = [
                (v, t, bx)
                for v, t, bx in detected
                if v is not None
            ]
            result.sort(key=lambda x: x[0])
            return result

        # Fallback: нумеруем секции последовательно с b_lo
        return [
            (b_lo + i, t, bx)
            for i, (_, t, bx) in enumerate(detected)
        ]

    def _extract_annotation_text(self, book: CompilationBook) -> str:
        """Извлечь текст <annotation> из FB2 (без тегов, нижний регистр, ё→е)."""
        if not book.abs_path.exists():
            return ''
        try:
            text = self._read_file_text(book.abs_path)
        except Exception:
            return ''
        # Читаем только до <body> — annotation всегда в <description>
        body_pos = text.lower().find('<body')
        head = text[:body_pos] if body_pos >= 0 else text
        m = re.search(r'<annotation\b[^>]*>(.*?)</annotation>', head, re.DOTALL | re.IGNORECASE)
        if not m:
            return ''
        return re.sub(r'<[^>]+>', ' ', m.group(1)).lower().replace('ё', 'е')

    def _extract_annotation_xml(self, book: CompilationBook) -> str:
        """Извлечь сырой XML блок <annotation>...</annotation> из первой книги."""
        if not book.abs_path.exists():
            self._log(f"  ⚠ Аннотация: файл не найден {book.abs_path.name}")
            return ''
        try:
            text = self._read_file_text(book.abs_path)
        except Exception as e:
            self._log(f"  ⚠ Аннотация: ошибка чтения {book.abs_path.name}: {e}")
            return ''
        body_pos = text.lower().find('<body')
        head = text[:body_pos] if body_pos >= 0 else text
        # Атрибуты на теге <annotation> допустимы (напр. xml:lang="ru")
        m = re.search(r'(<annotation\b[^>]*>.*?</annotation>)', head, re.DOTALL | re.IGNORECASE)
        if not m:
            self._log(f"  ℹ Аннотация не найдена в {book.abs_path.name}")
        return m.group(1) if m else ''

    def _extract_coverpage_id(self, book: CompilationBook) -> Optional[str]:
        """Извлечь ID бинаря обложки из <coverpage><image l:href="#id"/>."""
        if not book.abs_path.exists():
            return None
        try:
            text = self._read_file_text(book.abs_path)
        except Exception:
            return None
        m = re.search(
            r'<coverpage>.*?<image[^>]+l:href=["\']#([^"\']+)["\']',
            text, re.DOTALL | re.IGNORECASE,
        )
        return m.group(1) if m else None

    @staticmethod
    def _extract_section_cover_id(body_xml: str, id_remap: dict) -> Optional[str]:
        """Найти обложку конкретной секции body_xml и вернуть переименованный ID.

        Ищет первую `<section>` которая содержит ТОЛЬКО `<image>` (dedicated cover).
        Если такой нет — берёт первый `<image>` в любой секции.
        Возвращает id_remap[orig_id] или None.
        """
        # Паттерн 1: <section[attrs]>\s*<image l:href="#id"/>\s*</section>  (dedicated cover)
        _COVER_SEC = re.compile(
            r'<(?:fb:)?section[^>]*>\s*<(?:fb:)?image[^>]+l:href=["\']#?([^"\']+)["\'][^>]*/?\s*>(?:\s*</(?:fb:)?image>)?\s*</(?:fb:)?section>',
            re.IGNORECASE | re.DOTALL,
        )
        m = _COVER_SEC.search(body_xml)
        if m:
            orig = m.group(1).lstrip('#')
            return id_remap.get(orig)

        # Паттерн 2: первый <image> в теле (менее строгий)
        _ANY_IMG = re.compile(
            r'<(?:fb:)?image[^>]+l:href=["\']#?([^"\']+)["\']',
            re.IGNORECASE,
        )
        m2 = _ANY_IMG.search(body_xml)
        if m2:
            orig = m2.group(1).lstrip('#')
            return id_remap.get(orig)
        return None

    def _extract_binaries(self, book: CompilationBook) -> List[str]:
        """Извлечь все <binary>...</binary> блоки из файла.

        Возвращает список XML-фрагментов, каждый — один <binary> блок.
        """
        if not book.abs_path.exists():
            return []
        try:
            text = self._read_file_text(book.abs_path)
        except Exception:
            return []
        return re.findall(
            r'<binary\b[^>]*>.*?</binary>',
            text, re.DOTALL | re.IGNORECASE,
        )

    def _extract_metadata(self, book: CompilationBook) -> dict:
        """Извлечь жанр из записи."""
        return {
            'genre': (book.record.metadata_genre or '').strip(),
        }

    def _build_fb2(
        self,
        author: str,
        series: str,
        suffix: str,
        genre: str,
        bodies: List[Tuple[str, str]],
        binaries: Optional[List[str]] = None,
        cover_image_id: Optional[str] = None,
        book_cover_ids: Optional[List[Optional[str]]] = None,
        volume_range: Optional[str] = None,
        annotation: str = '',
    ) -> str:
        """Собрать итоговый FB2 XML из компонентов."""
        # Разбиваем автора на фамилию и имя
        parts = author.strip().split()
        last_name = _html.escape(parts[0]) if parts else ''
        first_name = _html.escape(' '.join(parts[1:])) if len(parts) > 1 else ''

        safe_series = _html.escape(series)
        n_books = len(bodies)
        book_title = f"{safe_series} ({suffix})"

        # Жанр — берём первый, если несколько через запятую
        genre_tag = ''
        if genre:
            first_genre = genre.split(',')[0].strip()
            if first_genre:
                genre_tag = f'  <genre>{_html.escape(first_genre)}</genre>\n'
        if not genre_tag:
            genre_tag = '  <genre>other</genre>\n'

        # <sequence number> — реальный диапазон томов серии (top_lo-top_hi).
        # Это важно для повторного сканирования: пайплайн читает number и
        # определяет позицию компиляции в серии. Без реального диапазона
        # "т. 3-4" получает number="1-2" и занимает позицию 1-2 при ресканировании.
        seq_range = volume_range if volume_range else ('1' if n_books == 1 else f'1-{n_books}')
        sequence_attr = f'name="{safe_series}" number="{seq_range}"'

        coverpage_tag = ''
        if cover_image_id:
            safe_cover_id = _html.escape(cover_image_id)
            coverpage_tag = f'<coverpage><image l:href="#{safe_cover_id}"/></coverpage>\n'

        annotation_tag = f'{annotation}\n' if annotation else ''

        # Описание
        description = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"'
            ' xmlns:l="http://www.w3.org/1999/xlink">\n'
            '<description>\n'
            '<title-info>\n'
            f'{genre_tag}'
            f'<author>\n  <last-name>{last_name}</last-name>\n'
            f'  <first-name>{first_name}</first-name>\n</author>\n'
            f'<book-title>{book_title}</book-title>\n'
            f'{annotation_tag}'
            f'{coverpage_tag}'
            f'<sequence {sequence_attr}/>\n'
            '</title-info>\n'
            '</description>\n'
        )

        # Тела книг — каждая книга в отдельном <body id="vol_N"> с <title>
        body_parts = []
        for idx, (title, body_xml) in enumerate(bodies, 1):
            safe_title = _html.escape(title)
            # Снимаем ВСЕ <body>/<body name="..."> и </body> теги.
            # count=1 создавал вложенные <body> если исходник содержал несколько тел.
            body_content = re.sub(r'<(?:fb:)?body(?:\s[^>]*)?>',  '', body_xml, flags=re.IGNORECASE)
            body_content = re.sub(r'</(?:fb:)?body>',              '', body_content, flags=re.IGNORECASE)
            # Убираем <title>...</title> в первой секции (заменим своим)
            body_content = re.sub(
                r'^\s*<title>.*?</title>',
                '',
                body_content,
                count=1,
                flags=re.DOTALL | re.IGNORECASE
            )
            # Убираем явные namespace-префиксы fb: для совместимости
            body_content = re.sub(r'<fb:', '<', body_content)
            body_content = re.sub(r'</fb:', '</', body_content)

            # Обложка тома — центрированная секция перед содержимым
            cover_section = ''
            vol_cover_id = (book_cover_ids[idx - 1] if book_cover_ids and idx - 1 < len(book_cover_ids) else None)
            if vol_cover_id:
                safe_cid = _html.escape(vol_cover_id)
                cover_section = (
                    f'<section>\n'
                    f'<image l:href="#{safe_cid}"/>\n'
                    f'</section>\n'
                )

            body_parts.append(
                f'<body id="vol_{idx}">\n'
                f'<title><p>{idx}. {safe_title}</p></title>\n'
                f'{cover_section}'
                f'{body_content.strip()}\n'
                f'</body>'
            )

        # Страница оглавления — только если томов больше одного
        toc_body = ''
        if n_books > 1:
            toc_lines = []
            for idx, (title, _) in enumerate(bodies, 1):
                safe_title = _html.escape(title)
                toc_lines.append(f'<p><a l:href="#vol_{idx}">{idx}. {safe_title}</a></p>')
            toc_body = (
                '<body>\n'
                '<section>\n'
                '<title><p>Содержание</p></title>\n'
                + '\n'.join(toc_lines) +
                '\n</section>\n'
                '</body>\n'
            )

        binary_section = ('\n' + '\n'.join(binaries)) if binaries else ''
        raw = description + toc_body + '\n'.join(body_parts) + binary_section + '\n</FictionBook>\n'
        return self._pretty_xml(raw)

    @staticmethod
    def _pretty_xml(xml_str: str) -> str:
        """Форматировать XML с отступами через minidom. Сохраняет UTF-8 декларацию."""
        import xml.dom.minidom as _minidom
        try:
            dom = _minidom.parseString(xml_str.encode('utf-8'))
            pretty = dom.toprettyxml(indent='  ', encoding=None)
            # toprettyxml добавляет свою декларацию — убираем её (у нас уже есть нужная)
            lines = pretty.splitlines()
            if lines and lines[0].startswith('<?xml'):
                lines = lines[1:]
            # Убираем пустые строки, которые minidom вставляет между узлами
            lines = [l for l in lines if l.strip()]
            return '<?xml version="1.0" encoding="utf-8"?>\n' + '\n'.join(lines) + '\n'
        except Exception:
            return xml_str

    # ------------------------------------------------------------------
    # Удаление исходников
    # ------------------------------------------------------------------

    def _delete_sources(self, paths: List[Path]) -> None:
        """Удалить исходные файлы после компиляции."""
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    self._log(f"  🗑 Удалён исходник: {path.name}")
                # Удалить папку, если пуста
                parent = path.parent
                if parent.is_dir() and not any(parent.iterdir()):
                    parent.rmdir()
                    self._log(f"  🗑 Удалена пустая папка: {parent.name}")
            except Exception as e:
                self._log(f"  ⚠ Не удалось удалить {path.name}: {e}")

    def delete_sources_for_result(self, result: CompilationResult) -> None:
        """Удалить исходники для уже выполненной компиляции (по подтверждению)."""
        if result.success:
            self._delete_sources(result.source_paths)
