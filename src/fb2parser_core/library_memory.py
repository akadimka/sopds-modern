"""Память автосинхронизации: что библиотека уже знает о жанрах.

См. docs/watch-folder-autosync-design.md, раздел «Память».

Две таблицы SQLite:

- ``books`` — индекс библиотеки. Жанр/автор/серия берутся из пути
  ``Жанр/Автор/[…/]Серия/файл`` (так раскладывает синхронизация), плюс
  отпечаток текста и издательские серии из заголовка файла. Обновляется
  инкрементально по (размер, mtime).
- ``orig_codes`` — ИСХОДНЫЕ коды ``<genre>`` книги по отпечатку текста.
  Пишутся до того, как тег будет переписан именем жанра (ручное назначение
  жанра, автосинхронизация, разовая загрузка из исходников) — иначе после
  синхронизации в файле остаётся только имя жанра, и выучить «код → жанр»
  уже не из чего.

Отпечаток — SHA1 нормализованного фрагмента текста ``<body>``: синхронизация
переписывает только ``<description>`` и перемещает файл, тело книги не
меняется, поэтому исходник и книга в библиотеке получают один отпечаток.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import sqlite3
import threading
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .fb2_utils import MAX_FB2_UNCOMPRESSED_SIZE

_log = logging.getLogger(__name__)

# Сколько байт заголовка файла читать: хватает на <description> и начало <body>.
HEAD_BYTES = 400_000
# Фрагмент текста <body> для отпечатка: пропускаем начало (там заголовок
# книги, который могли переписать) и берём следующие символы.
_FP_SKIP, _FP_LEN, _FP_MIN = 300, 4000, 500

# Папка жанра, в которую синхронизация кладёт книги без жанра — это не
# решение пользователя, в память не идёт.
NO_GENRE_FOLDER = "Без жанра"

_ENC_RE = re.compile(rb'encoding\s*=\s*["\']([A-Za-z0-9_\-]+)["\']')
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_TITLE_INFO_RE = re.compile(r"<(?:[\w.-]+:)?title-info\b.*?</(?:[\w.-]+:)?title-info\s*>", re.S)
_PUBLISH_INFO_RE = re.compile(r"<(?:[\w.-]+:)?publish-info\b.*?</(?:[\w.-]+:)?publish-info\s*>", re.S)
_GENRE_RE = re.compile(r"<(?:[\w.-]+:)?genre\b[^>]*>(.*?)</(?:[\w.-]+:)?genre\s*>", re.S)
_SEQ_NAME_RE = re.compile(r'<(?:[\w.-]+:)?sequence\b[^>]*\bname\s*=\s*"([^"]+)"')
_BODY_RE = re.compile(r"<(?:[\w.-]+:)?body\b")


def norm_key(s: str) -> str:
    """Ключ сравнения имён: регистр, ё/е, порядок слов и пунктуация не важны."""
    s = (s or "").lower().replace("ё", "е")
    return " ".join(sorted(re.findall(r"\w+", s)))


def author_keys(author: str) -> List[str]:
    """Ключ всего авторского коллектива и (если соавторов несколько) каждого
    соавтора отдельно: серия «Винокуров Юрий, Сапфир Олег / Кодекс Охотника»
    из порции должна найтись в библиотеке под «Сапфир Олег»."""
    full = norm_key(author)
    if not full:
        return []
    parts = [norm_key(p) for p in re.split(r"\s*[,;]\s*", author)]
    return [full] + [p for p in dict.fromkeys(parts) if p and p != full]


@dataclass
class Evidence:
    """Что извлечено из текста одной книги."""
    fingerprint: Optional[str]
    codes: List[str] = field(default_factory=list)
    pubseq: List[str] = field(default_factory=list)


def extract_evidence(text: str) -> Evidence:
    """Отпечаток, коды жанров (из <title-info>) и издательские серии."""
    m = _BODY_RE.search(text)
    fp = None
    head = text[:m.start()] if m else text
    if m:
        body = _WS_RE.sub(" ", _TAG_RE.sub(" ", text[m.start():m.start() + 200_000])).strip()
        frag = body[_FP_SKIP:_FP_SKIP + _FP_LEN]
        if len(frag) >= _FP_MIN:
            fp = hashlib.sha1(frag.lower().encode("utf-8")).hexdigest()
    codes: List[str] = []
    ti = _TITLE_INFO_RE.search(head)
    if ti:
        for g in _GENRE_RE.findall(ti.group(0)):
            g = g.strip()
            if g and g not in codes:
                codes.append(g)
    pubseq: List[str] = []
    pi = _PUBLISH_INFO_RE.search(head)
    if pi:
        for name in _SEQ_NAME_RE.findall(pi.group(0)):
            name = name.strip()
            if name and name not in pubseq:
                pubseq.append(name)
    return Evidence(fp, codes, pubseq)


def _decode(data: bytes) -> str:
    m = _ENC_RE.search(data[:200])
    enc = m.group(1).decode("ascii", "ignore") if m else "utf-8"
    try:
        return data.decode(enc, "ignore")
    except LookupError:
        return data.decode("utf-8", "ignore")


def read_head_text(path: Path) -> Optional[str]:
    """Начало FB2 (или FB2 внутри zip) как текст; None — не читается."""
    try:
        if path.name.lower().endswith(".zip"):
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".fb2")]
                if not names or z.getinfo(names[0]).file_size > MAX_FB2_UNCOMPRESSED_SIZE:
                    return None
                with z.open(names[0]) as f:
                    data = f.read(HEAD_BYTES)
        else:
            with open(path, "rb") as f:
                data = f.read(HEAD_BYTES)
    except (OSError, zipfile.BadZipFile, KeyError) as e:
        _log.warning("library_memory: не прочитан %s: %s", path, e)
        return None
    return _decode(data)


def read_evidence(path: Path) -> Evidence:
    text = read_head_text(path)
    return extract_evidence(text) if text is not None else Evidence(None)


def is_book_file(name: str) -> bool:
    n = name.lower()
    return n.endswith(".fb2") or n.endswith(".fb2.zip")


def parse_library_path(rel: str) -> Tuple[str, str, List[str]]:
    """``Жанр/Автор/[…/]Серия/файл`` → (жанр, автор, [компоненты серии])."""
    parts = Path(rel).parts
    genre = parts[0] if len(parts) > 1 else ""
    author = parts[1] if len(parts) > 2 else ""
    series = list(parts[2:-1]) if len(parts) > 3 else []
    return genre, author, series


@dataclass
class GenreMemory:
    """Словари голосования, собранные из библиотеки (см. genre_voting)."""
    # (автор, компонент серии) -> жанр -> книг
    series: Dict[Tuple[str, str], Counter] = field(default_factory=lambda: defaultdict(Counter))
    # автор -> жанр -> книг
    author: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    # издательская серия -> жанр -> книг
    pub: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    # исходный код -> жанр -> книг
    code: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    # исходный код -> жанр -> число разных порций, где код встретился в этом жанре
    code_batches: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))

    def add_book(self, genre: str, author: str, series: Iterable[str], pubseq: Iterable[str],
                 codes: Iterable[str] = ()) -> None:
        for a in author_keys(author):
            self.author[a][genre] += 1
            for part in series:
                if norm_key(part):
                    self.series[(a, norm_key(part))][genre] += 1
        for p in {norm_key(p) for p in pubseq} - {""}:
            self.pub[p][genre] += 1
        for c in {c.lower() for c in codes}:
            self.code[c][genre] += 1


_SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    path TEXT PRIMARY KEY,
    size INTEGER, mtime REAL,
    fingerprint TEXT,
    genre TEXT, author TEXT, series TEXT,
    pubseq TEXT
);
CREATE INDEX IF NOT EXISTS books_fp ON books(fingerprint);
CREATE TABLE IF NOT EXISTS orig_codes (
    fingerprint TEXT PRIMARY KEY,
    codes TEXT,
    batch TEXT,
    decided_by TEXT,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

_SEP = "\x1f"  # разделитель списков в TEXT-колонках


class LibraryMemory:
    """Хранилище памяти (SQLite) — потокобезопасная запись исходных кодов."""

    _write_lock = threading.Lock()

    def __init__(self, db_path):
        self.db_path = str(db_path)
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30)

    # ---------- исходные коды ----------
    def record_orig_codes(self, fingerprint: Optional[str], codes: Iterable[str], batch: str,
                          decided_by: str) -> bool:
        """Запомнить исходные коды книги. Уже записанные НЕ перезаписываются:
        повторная запись после переписывания тега принесла бы имя жанра
        вместо кодов. Возвращает True, если запись добавлена."""
        codes = [c for c in codes if c]
        if not fingerprint or not codes:
            return False
        with self._write_lock, self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO orig_codes(fingerprint, codes, batch, decided_by) VALUES (?, ?, ?, ?)",
                (fingerprint, _SEP.join(codes), batch, decided_by),
            )
            return cur.rowcount > 0

    def record_from_text(self, text: str, batch: str, decided_by: str) -> bool:
        ev = extract_evidence(text)
        return self.record_orig_codes(ev.fingerprint, ev.codes, batch, decided_by)

    # ---------- индекс библиотеки ----------
    def refresh(self, library_root, progress: Optional[Callable[[int, int], None]] = None) -> Dict[str, int]:
        """Синхронизировать таблицу books с файлами библиотеки."""
        root = Path(library_root)
        files: Dict[str, Tuple[int, float]] = {}
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                if is_book_file(name):
                    full = os.path.join(dirpath, name)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    files[os.path.relpath(full, root).replace(os.sep, "/")] = (st.st_size, st.st_mtime)
        with self._connect() as conn:
            known = {p: (s, m) for p, s, m in conn.execute("SELECT path, size, mtime FROM books")}
        gone = [p for p in known if p not in files]
        todo = [p for p, sm in files.items() if known.get(p) != sm]
        rows = []
        for i, rel in enumerate(todo):
            if progress and i % 200 == 0:
                progress(i, len(todo))
            ev = read_evidence(root / rel)
            genre, author, series = parse_library_path(rel)
            size, mtime = files[rel]
            rows.append((rel, size, mtime, ev.fingerprint, genre, author, _SEP.join(series), _SEP.join(ev.pubseq)))
        with self._write_lock, self._connect() as conn:
            conn.executemany("DELETE FROM books WHERE path = ?", [(p,) for p in gone])
            conn.executemany(
                "INSERT OR REPLACE INTO books(path, size, mtime, fingerprint, genre, author, series, pubseq) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        return {"total": len(files), "updated": len(rows), "removed": len(gone)}

    def library_fingerprints(self) -> Set[str]:
        with self._connect() as conn:
            return {fp for (fp,) in conn.execute("SELECT fingerprint FROM books WHERE fingerprint IS NOT NULL")}

    def build(self) -> GenreMemory:
        """Собрать словари голосования из индекса и исходных кодов."""
        mem = GenreMemory()
        code_batch_pairs: Dict[str, Set[Tuple[str, str]]] = defaultdict(set)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT b.genre, b.author, b.series, b.pubseq, o.codes, o.batch "
                "FROM books b LEFT JOIN orig_codes o ON o.fingerprint = b.fingerprint"
            ).fetchall()
        for genre, author, series, pubseq, codes, batch in rows:
            if not genre or genre == NO_GENRE_FOLDER:
                continue
            code_list = [c for c in (codes or "").split(_SEP) if c]
            mem.add_book(genre, author or "", [s for s in (series or "").split(_SEP) if s],
                         [p for p in (pubseq or "").split(_SEP) if p], code_list)
            for c in code_list:
                code_batch_pairs[c.lower()].add((genre, batch or ""))
        for c, pairs in code_batch_pairs.items():
            for genre, _batch in pairs:
                mem.code_batches[c][genre] += 1
        return mem
