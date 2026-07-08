import sqlite3
import json
import hashlib
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from datetime import datetime

_CONTENT_HASH_BYTES = 256 * 1024  # 256 KB — совпадает с gui_duplicate_finder._file_hash


# Файлы парсера, от которых зависит качество извлечения метаданных.
# При изменении любого из них весь кэш автоматически сбрасывается.
_PARSER_SOURCE_FILES = [
    'fb2_sax_extractor.py',
    'fb2_author_extractor.py',
    'passes/pass1_read_files.py',
]


def _compute_parser_version() -> str:
    """Вычислить хэш исходников парсера.

    Если код парсера изменился — хэш изменится → кэш будет сброшен
    при следующем запуске пайплайна.
    """
    h = hashlib.md5()
    base = Path(__file__).parent
    for rel in _PARSER_SOURCE_FILES:
        p = base / rel
        try:
            h.update(p.read_bytes())
        except OSError:
            h.update(rel.encode())
    return h.hexdigest()


class MetadataCache:
    """SQLite-based cache for FB2 file metadata to avoid re-parsing unchanged files.

    Автоматически сбрасывается при изменении исходников парсера —
    таким образом фиксы в логике всегда отражаются в результатах
    следующего запуска пайплайна без ручного вмешательства.
    """

    def __init__(self, cache_path: Path = Path("metadata_cache.db")):
        self.cache_path = cache_path
        self._parser_version = _compute_parser_version()
        self._init_db()
        self._check_parser_version()

    def _connect(self) -> sqlite3.Connection:
        """Открыть соединение с БД с таймаутом и WAL-режимом.

        WAL (Write-Ahead Logging) позволяет одновременные READ + один WRITE
        без блокировок. Timeout=30 — ждать освобождения блокировки вместо
        немедленной ошибки при конкуренции процессов ProcessPoolExecutor.
        """
        conn = sqlite3.connect(self.cache_path, timeout=30)
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA synchronous=NORMAL')
        return conn

    def _init_db(self):
        """Initialize the cache database."""
        with self._connect() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS file_metadata (
                    file_path TEXT PRIMARY KEY,
                    file_hash TEXT,
                    content_hash TEXT,
                    mtime REAL,
                    metadata TEXT,
                    cached_at REAL
                )
            ''')
            # Миграция: добавить content_hash если его нет (старая БД)
            try:
                conn.execute("ALTER TABLE file_metadata ADD COLUMN content_hash TEXT")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # Колонка уже существует
            conn.execute('''
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            conn.commit()

    def _check_parser_version(self):
        """Сбросить кэш если исходники парсера изменились."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT value FROM cache_meta WHERE key = 'parser_version'"
            ).fetchone()
            stored_version = row[0] if row else None

            if stored_version != self._parser_version:
                conn.execute("DELETE FROM file_metadata")
                conn.execute(
                    "INSERT OR REPLACE INTO cache_meta (key, value) VALUES ('parser_version', ?)",
                    (self._parser_version,)
                )
                conn.commit()
                if stored_version is not None:
                    # Не первый запуск — сообщаем о сбросе
                    print(f"[CACHE] Парсер обновлён — кэш метаданных сброшен")

    def get_cached_metadata(self, file_path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
        """Get cached metadata if file hasn't changed.

        Returns (metadata_dict, content_hash) or (None, '') on miss/error.
        content_hash — SHA-256 первых 256 КБ содержимого (для поиска дубликатов).
        """
        try:
            stat = file_path.stat()
            current_mtime = stat.st_mtime

            with self._connect() as conn:
                row = conn.execute(
                    "SELECT metadata, file_hash, content_hash FROM file_metadata WHERE file_path = ? AND mtime = ?",
                    (str(file_path), current_mtime)
                ).fetchone()

                if row:
                    metadata_json, cached_hash, content_hash = row
                    if self._calculate_hash(file_path) == cached_hash:
                        return json.loads(metadata_json), (content_hash or '')
        except (OSError, json.JSONDecodeError):
            pass
        return None, ''

    def cache_metadata(self, file_path: Path, metadata: Dict[str, Any]) -> str:
        """Store metadata in cache.

        Returns content_hash (SHA-256 первых 256 КБ) для записи в BookRecord.
        Вычисляет MD5 и SHA-256 за один read_fb2_bytes вызов.
        """
        content_hash = ''
        try:
            try:
                from fb2_utils import read_fb2_bytes
            except ImportError:
                from .fb2_utils import read_fb2_bytes
            stat = file_path.stat()
            raw = read_fb2_bytes(file_path)
            file_hash = hashlib.md5(raw).hexdigest()
            content_hash = hashlib.sha256(raw[:_CONTENT_HASH_BYTES]).hexdigest()

            with self._connect() as conn:
                conn.execute(
                    """INSERT OR REPLACE INTO file_metadata
                       (file_path, file_hash, content_hash, mtime, metadata, cached_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (str(file_path), file_hash, content_hash, stat.st_mtime,
                     json.dumps(metadata), datetime.now().timestamp())
                )
                conn.commit()
        except (OSError, sqlite3.OperationalError):
            # Блокировка БД при параллельной записи — некритично,
            # файл будет перечитан при следующем запуске.
            pass
        return content_hash

    def _calculate_hash(self, file_path: Path) -> str:
        """Calculate MD5 hash of raw file bytes for cache validity check."""
        hash_obj = hashlib.md5()
        try:
            with open(file_path, 'rb') as f:
                for chunk in iter(lambda: f.read(4096), b""):
                    hash_obj.update(chunk)
            return hash_obj.hexdigest()
        except OSError:
            return ""

    def clear_cache(self):
        """Clear all cached metadata."""
        with self._connect() as conn:
            conn.execute("DELETE FROM file_metadata")
            conn.commit()

    def get_cache_stats(self) -> Dict[str, int]:
        """Get cache statistics."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM file_metadata").fetchone()[0]
            recent = conn.execute(
                "SELECT COUNT(*) FROM file_metadata WHERE cached_at > ?",
                (datetime.now().timestamp() - 86400,)
            ).fetchone()[0]
            return {"total_cached": total, "recently_cached": recent}

    def cleanup_old_entries(self, max_age_days: int = 30):
        """Remove cache entries older than max_age_days."""
        cutoff = datetime.now().timestamp() - (max_age_days * 86400)
        with self._connect() as conn:
            conn.execute("DELETE FROM file_metadata WHERE cached_at < ?", (cutoff,))
            conn.commit()
