"""Разовая чистка библиотеки (шаг 3 docs/series-duplicates.md).

Три части, у каждой — пробный прогон (отчёт) и выполнение только по явному
«да» (`manage.py library_cleanup --apply`):

- **жанры**: серия, лежащая в нескольких жанровых папках, переносится в
  один жанр — большинства книг; поровну — по выбору суперпользователя
  (решения пользователя 2026-10-27 и 2026-10-29). Переносятся папки серии,
  тег <genre> в файлах не меняется: место в библиотеке задаёт папка;
- **компилятор**: решения автокомпиляции по всей библиотеке — какие файлы
  удаляются как дубли, какие сборники переименовываются, какие тома
  собираются (`auto_compile_service.find_library_groups`, те же решения,
  что у автокомпиляции после синхронизации);
- **книги**: отдельные книги по файлу решений — куда перенести, какие автор,
  название, серия и жанр записать в файл (книги в ошибочных жанровых папках,
  сервер 2026-11-02: «Без жанра», `poetry`, `prose_military`).

Сначала жанры и книги, потом компилятор: после переноса тома серии лежат в
одной папке, и группы компилятора меняются — их отчёт снимается заново.
"""
import os
import re
import shutil
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import ownership_hooks
from .library_memory import is_book_file
from .path_safety import is_within


def _word_key(name: str) -> str:
    return ' '.join(sorted(re.findall(r'\w+', (name or '').lower().replace('ё', 'е'))))


def _book_count(folder: Path) -> int:
    return sum(1 for p in folder.rglob('*') if is_book_file(p.name) and p.is_file())


@dataclass
class SplitSeries:
    """Серия одного автора в нескольких жанровых папках.

    places: {жанр: [папки серии в этом жанре]} — папка автора в разных жанрах
    может быть записана по-разному («Престон Дуглас» / «Дуглас Престон»).
    """
    author: str
    series: str
    places: Dict[str, List[Path]] = field(default_factory=dict)
    books: Dict[str, int] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.author} / {self.series}"

    def majority(self) -> Optional[str]:
        top = max(self.books.values())
        leaders = [g for g, n in self.books.items() if n == top]
        return leaders[0] if len(leaders) == 1 else None


def find_split_series(library: Path) -> List[SplitSeries]:
    """Серии (папки «Жанр/Автор/Серия» с книгами), которые есть у одного
    автора (с точностью до порядка слов) больше чем в одном жанре."""
    found: Dict[tuple, SplitSeries] = {}
    for genre_dir in sorted(p for p in library.iterdir() if p.is_dir()):
        for author_dir in sorted(p for p in genre_dir.iterdir() if p.is_dir()):
            for series_dir in sorted(p for p in author_dir.iterdir() if p.is_dir()):
                books = _book_count(series_dir)
                if not books:
                    continue
                key = (_word_key(author_dir.name), series_dir.name.lower().replace('ё', 'е'))
                item = found.setdefault(key, SplitSeries(author_dir.name, series_dir.name))
                item.places.setdefault(genre_dir.name, []).append(series_dir)
                item.books[genre_dir.name] = item.books.get(genre_dir.name, 0) + books
    split = [s for s in found.values() if len(s.books) > 1]
    for s in split:
        # Название — как в жанре большинства (или первом по алфавиту).
        main = s.majority() or sorted(s.books)[0]
        s.author, s.series = s.places[main][0].parent.name, s.places[main][0].name
    return sorted(split, key=lambda s: s.key.lower())


def read_genre_choices(path: Path) -> Dict[str, str]:
    """Файл «жанр<TAB>автор / серия» (как в отчёте) → {ключ серии: жанр}."""
    choices = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        if '\t' in line and not line.startswith('#'):
            genre, key = line.split('\t', 1)
            choices[key.strip()] = genre.strip()
    return choices


def pick_version(src: Path, dst: Path) -> str:
    """Какой из двух файлов с одним именем оставить (правило 1, решение
    пользователя 2026-10-27): 'dst', если текст `src` есть в `dst` (и `src`
    не больше), 'src' — наоборот; почти одинаковые — с лучшей разметкой глав
    и обложкой; '' — у каждого есть своё, оставить оба. Сервер 2026-10-29:
    9 серий лежали двумя разными сборками одного имени в двух жанрах
    («Корморан Страйк (Окталогия)» — 6,0 и 9,8 МБ)."""
    from .fb2_compiler import FB2CompilerService, _omnibus_quality

    svc = FB2CompilerService()
    a, b = svc._shingle_sets(svc._text_shingles(src), svc._text_shingles(dst))
    if not a or not b:
        return ''
    common = len(a & b)
    src_in, dst_in = common / len(a), common / len(b)
    if src_in >= 0.98 and dst_in >= 0.98:
        return 'src' if _omnibus_quality(src) > _omnibus_quality(dst) else 'dst'
    if src_in >= svc._DUPLICATE_MIN_COVERAGE and (len(a) <= len(b) or src_in >= 0.98):
        return 'dst'
    if dst_in >= svc._DUPLICATE_MIN_COVERAGE and (len(b) <= len(a) or dst_in >= 0.98):
        return 'src'
    return ''


def move_series_to_genre(library: Path, series: SplitSeries, genre: str, db_path: Optional[Path],
                         apply: bool = False, log: Optional[Callable] = None) -> Dict[str, int]:
    """Перенести папки серии из остальных жанров в `genre`.

    Папка автора в целевом жанре — та, где серия уже лежит, иначе папка
    автора с теми же словами, иначе исходное имя. Место занято файлом с тем
    же именем — остаётся одна версия (`pick_version`), владельцы лишней
    переходят к оставленной; у каждой своё — обе на месте (в отчёте).
    Владение — через ownership_hooks, записи БД библиотеки — новый путь и
    жанр. apply=False — только отчёт.
    """
    log = log or (lambda m: None)
    stats = {'files': 0, 'moved': 0, 'replaced': 0, 'dropped': 0, 'conflicts': 0, 'errors': 0, 'db_rows': 0,
             'no_access': 0}
    if genre in series.places:
        author_dir = series.places[genre][0].parent.name
    else:
        variants = [p.name for p in (library / genre).iterdir() if p.is_dir()
                    and _word_key(p.name) == _word_key(series.author)] if (library / genre).is_dir() else []
        author_dir = variants[0] if len(variants) == 1 else series.author
    target_root = library / genre / author_dir
    # Права проверяются до первого изменения: серия переносится целиком или
    # не трогается. Сервер 2026-10-29: библиотека принадлежала другой учётной
    # записи, а команда шла от www-data — 47 файлов «Permission denied».
    denied = _not_writable(library, series, genre, target_root)
    if denied:
        stats['no_access'] = 1
        log(f"  ✗ нет прав на запись: {denied.relative_to(library) if denied != library else denied} "
            f"— серия не переносится")
        return stats
    conn = sqlite3.connect(str(db_path)) if apply and db_path and db_path.exists() else None
    try:
        for src_genre, folders in sorted(series.places.items()):
            if src_genre == genre:
                continue
            for src_dir in folders:
                dst_dir = target_root / src_dir.name
                log(f"  {src_dir.relative_to(library)} → {dst_dir.relative_to(library)}"
                    + (" (слияние)" if dst_dir.exists() else ""))
                for src in sorted(p for p in src_dir.rglob('*') if p.is_file()):
                    stats['files'] += 1
                    dst = dst_dir / src.relative_to(src_dir)
                    keep = None
                    if dst.exists():
                        keep = pick_version(src, dst) if is_book_file(src.name) else ''
                        if not keep:
                            stats['conflicts'] += 1
                            log(f"    ⚠ занято другой версией, оставляю обе: {dst.relative_to(library)}")
                            continue
                        if keep == 'dst':
                            stats['dropped'] += 1
                            log(f"    ≡ на месте версия не хуже, переносимая удаляется: {src.relative_to(library)}")
                        else:
                            stats['replaced'] += 1
                            log(f"    ⇄ переносимая версия полнее, заменяет: {dst.relative_to(library)}")
                    else:
                        stats['moved'] += 1
                    if not apply:
                        continue
                    # Владение и БД — только после удавшейся операции с файлом:
                    # иначе (сервер 2026-10-29) владельцы уходили от файла,
                    # который так и остался на месте.
                    try:
                        if keep == 'dst':
                            src.unlink()
                            ownership_hooks.merge([src], dst)
                            if conn is not None:
                                _db_delete(conn, library, src)
                            continue
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        if keep == 'src':
                            # Атомарная замена: на месте сразу полная версия;
                            # transfer объединяет владельцев обоих путей.
                            os.replace(src, dst)
                            if conn is not None:
                                _db_delete(conn, library, dst)
                        else:
                            shutil.move(str(src), str(dst))
                        ownership_hooks.transfer(src, dst)
                        if conn is not None:
                            old_rel = src.relative_to(library)
                            cur = conn.execute(
                                "UPDATE books SET file_path = ?, genre = ? WHERE file_path IN (?, ?)",
                                (dst.relative_to(library).as_posix(), genre, str(old_rel), old_rel.as_posix()))
                            stats['db_rows'] += cur.rowcount
                    except Exception as exc:  # noqa: BLE001 — один файл не должен остановить остальные
                        stats[{'dst': 'dropped', 'src': 'replaced'}.get(keep, 'moved')] -= 1
                        stats['errors'] += 1
                        log(f"    ✗ {src.name}: {exc}")
                if apply:
                    # Пустые папки серии и автора после переноса.
                    for d in sorted((p for p in src_dir.rglob('*') if p.is_dir()), reverse=True) + [
                            src_dir, src_dir.parent]:
                        try:
                            d.rmdir()
                        except OSError:
                            pass
        if conn is not None:
            conn.commit()
    finally:
        if conn is not None:
            conn.close()
    return stats


def _not_writable(library: Path, series: SplitSeries, genre: str, target_root: Path) -> Optional[Path]:
    """Первая папка, в которую переносу серии нужно писать, а прав нет:
    папки серии в остальных жанрах (файлы уходят, пустые папки удаляются),
    папка автора над ними, ближайшая существующая папка на месте назначения."""
    needed: List[Path] = []
    for src_genre, folders in series.places.items():
        if src_genre == genre:
            continue
        for src_dir in folders:
            needed += [src_dir, *(p for p in src_dir.rglob('*') if p.is_dir()), src_dir.parent]
            dst_dir = target_root / src_dir.name
            if dst_dir.exists():
                needed += [dst_dir, *(p for p in dst_dir.rglob('*') if p.is_dir())]
            else:
                existing = dst_dir
                while not existing.exists() and existing != library:
                    existing = existing.parent
                needed.append(existing)
    return next((p for p in needed if not os.access(p, os.W_OK)), None)


def _db_delete(conn, library: Path, path: Path) -> None:
    rel = path.relative_to(library)
    conn.execute("DELETE FROM books WHERE file_path IN (?, ?)", (str(rel), rel.as_posix()))


@dataclass
class BookFix:
    """Строка файла решений части «книги».

    dst — куда перенести (путь в библиотеке; '' — оставить на месте, '?' —
    ждёт выбора пользователя); series — «Серия» или «Серия|N», '-' — убрать
    ложную серию; пустые поля — не менять.
    """
    src: str
    dst: str = ''
    author: str = ''
    title: str = ''
    series: str = ''
    genre: str = ''

    @property
    def tag_changes(self) -> List[str]:
        names = (('автор', self.author), ('название', self.title), ('серия', self.series), ('жанр', self.genre))
        return [f"{n}: {'убрать' if v == '-' else v}" for n, v in names if v]


def read_book_fixes(path: Path) -> List[BookFix]:
    """Файл «откуда<TAB>куда<TAB>автор<TAB>название<TAB>серия<TAB>жанр» (пути —
    от корня библиотеки, через «/»; строки с «#» — комментарии)."""
    fixes = []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        cols = [c.strip() for c in line.split('\t')] + [''] * 6
        fixes.append(BookFix(*cols[:6]))
    return fixes


def fix_book(library: Path, fix: BookFix, db_path: Optional[Path], patch_tags: Callable, set_genre: Callable,
             apply: bool = False, log: Optional[Callable] = None) -> str:
    """Перенести книгу и/или поправить её теги по строке файла решений.

    patch_tags(path, author, series, title, series_number=, drop_series=) —
    `SynchronizationService._patch_fb2_tags`; set_genre(path, genre) —
    `GenreAssignmentService._assign_genre_to_file`: те же правила записи, что
    у синхронизации (файл с encoding="latin-1" и текстом в UTF-8 пишется в
    UTF-8). Место занято — как при переносе серий: остаётся одна версия
    (`pick_version`) или, если у каждой своё, обе на месте. Владение — через
    ownership_hooks, запись БД библиотеки — новый путь и жанр.

    Возвращает итог: 'missing', 'waiting', 'no_access', 'conflict', 'error',
    'dropped' (лишняя копия), 'replaced', 'moved', 'fixed' (теги на месте).
    """
    log = log or (lambda m: None)
    src = library / fix.src
    if not src.is_file():
        log(f"✗ нет файла: {fix.src}")
        return 'missing'
    if fix.dst == '?':
        log(f"? {fix.src} — ждёт выбора, куда положить")
        return 'waiting'
    dst = library / fix.dst if fix.dst else src
    log(f"→ {fix.src} → {fix.dst}" if dst != src else f"✎ {fix.src} (на месте)")
    if fix.tag_changes:
        log("    " + "; ".join(fix.tag_changes))
    keep = None
    if dst != src and dst.exists():
        keep = pick_version(src, dst) if is_book_file(src.name) else ''
        if not keep:
            log("    ⚠ место занято другой версией, оставляю обе — книга не переносится")
            return 'conflict'
        log("    ≡ на месте версия не хуже, эта копия удаляется" if keep == 'dst'
            else "    ⇄ эта версия полнее, заменяет лежащую")
    if dst != src and _path_too_long(dst):
        # Пределы синхронизации (Windows MAX_PATH, 255 байт имени) — с запасом
        # под «.zip.tmp». Проверка до записи тегов: иначе теги записаны, а
        # перенос падает (копия библиотеки 2026-11-02, «Риордан Рик - Перси
        # Джексон…» в глубокой папке).
        log("    ✗ путь назначения слишком длинный — укоротите имя в файле решений")
        return 'error'
    existing = dst.parent
    while not existing.exists() and existing != library:
        existing = existing.parent
    needed = [src.parent] + ([existing] if dst != src else []) + ([src] if fix.tag_changes and keep != 'dst' else [])
    denied = next((p for p in needed if not os.access(p, os.W_OK)), None)
    if denied:
        log(f"    ✗ нет прав на запись: {denied}")
        return 'no_access'
    outcome = 'dropped' if keep == 'dst' else 'replaced' if keep == 'src' else 'moved' if dst != src else 'fixed'
    if not apply:
        return outcome
    conn = sqlite3.connect(str(db_path)) if db_path and db_path.exists() else None
    try:
        if keep == 'dst':
            src.unlink()
            ownership_hooks.merge([src], dst)
            if conn is not None:
                _db_delete(conn, library, src)
        else:
            if fix.author or fix.title or fix.series:
                name, _, number = fix.series.partition('|')
                if not patch_tags(src, fix.author or None, '' if name == '-' else name, fix.title or None,
                                  series_number=number or None, drop_series=name == '-'):
                    log("    ✗ теги не записаны — книга не переносится")
                    return 'error'
            if fix.genre and not set_genre(src, fix.genre):
                log("    ✗ жанр не записан — книга не переносится")
                return 'error'
            if dst != src:
                dst.parent.mkdir(parents=True, exist_ok=True)
                if keep == 'src':
                    os.replace(src, dst)
                    if conn is not None:
                        _db_delete(conn, library, dst)
                else:
                    shutil.move(str(src), str(dst))
                ownership_hooks.transfer(src, dst)
                if conn is not None:
                    old_rel = src.relative_to(library)
                    conn.execute("UPDATE books SET file_path = ?, genre = ? WHERE file_path IN (?, ?)",
                                 (Path(fix.dst).as_posix(), Path(fix.dst).parts[0], str(old_rel),
                                  old_rel.as_posix()))
        if conn is not None:
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — одна книга не должна остановить остальные
        log(f"    ✗ {src.name}: {exc}")
        return 'error'
    finally:
        if conn is not None:
            conn.close()
    if dst != src:
        # Опустевшие папки серии, автора и жанра («Без жанра»).
        d = src.parent
        while d != library and is_within(d, library):
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent
    return outcome


def remove_empty_dirs(root: Path) -> None:
    """Удалить пустые папки внутри `root` и её саму, если опустела."""
    if not root.is_dir():
        return
    for d in sorted((p for p in root.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True) + [root]:
        try:
            d.rmdir()
        except OSError:
            pass


def _path_too_long(path: Path) -> bool:
    from .synchronization import SynchronizationService as S

    reserve = S._NAME_SUFFIX_RESERVE
    return ((os.name == 'nt' and len(str(path)) + reserve > S._MAX_PATH)
            or len(path.name.encode('utf-8')) + reserve > S._MAX_NAME_BYTES)


def describe_group(compiler, group, library: Path) -> List[str]:
    """Строки отчёта о группе компилятора: что останется, уйдёт, соберётся."""
    def rel(p: Path) -> str:
        try:
            return Path(p).relative_to(library).as_posix()
        except ValueError:
            return str(p)

    kind = 'очистка' if group.cleanup_only else 'компиляция'
    series = compiler._series_to_display(compiler._group_series_for_naming(group))
    lines = [f"{group.author} / {series} [{kind} {group.volume_range}]"]
    if not group.cleanup_only:
        lines += [f"  СОБРАТЬ том {b.volume_label}: {rel(b.abs_path)}" for b in group.books]
    lines += [f"  ОСТАВИТЬ {rel(p)}" for p in group.kept_paths or []]
    lines += [f"  УДАЛИТЬ {rel(p)}" for p in group.duplicate_paths or []]
    lines += [f"  НЕ ТРОГАТЬ {rel(p)}" for p in group.excluded_paths or []]
    new_path = compiler.cleanup_rename_target(group)
    if new_path is not None:
        lines.append(f"  ПЕРЕИМЕНОВАТЬ {rel(group.kept_paths[0])} → {new_path.name}")
    return lines


def read_skip_list(path: Path) -> List[str]:
    """Файл «путь на строку» (от корня библиотеки, через «/»; «#» —
    комментарий). Путь с «/» на конце — папка: всё, что в ней."""
    entries = []
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if line and not line.startswith('#'):
            entries.append(unicodedata.normalize('NFC', line.replace('\\', '/')))
    return entries


def skip_reason(group, library: Path, skip: List[str]) -> Optional[str]:
    """Строка списка пропуска, под которую попал хоть один файл группы, или
    None. Группы со спорными решениями (сервер 2026-10-29: пары `.fb2` +
    `.fb2.zip` с разным текстом, «Сборник» антологий) остаются как есть, пока
    их не разберут вручную."""
    paths = [b.abs_path for b in group.books] + list(group.kept_paths or []) + \
        list(group.duplicate_paths or []) + list(group.excluded_paths or [])
    for p in paths:
        try:
            rel = unicodedata.normalize('NFC', Path(p).relative_to(library).as_posix())
        except ValueError:
            continue
        for entry in skip:
            if rel == entry or (entry.endswith('/') and rel.startswith(entry)):
                return entry
    return None


def group_report_counts(groups) -> Dict[str, int]:
    counts: Dict[str, int] = defaultdict(int)
    for g in groups:
        counts['cleanup' if g.cleanup_only else 'compile'] += 1
        counts['delete'] += len(g.duplicate_paths or [])
        if not g.cleanup_only:
            counts['compiled_books'] += len(g.books)
    return dict(counts)
