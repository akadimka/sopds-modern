"""Сжатие библиотеки: после «книга.fb2 → книга.fb2.zip» обновить каталоги,
которые помнят путь книги (баг №159).

Каталог OPDS ищет книгу по (имени файла, пути): у плоской книги путь —
папка, у книги в zip — путь самого архива («…/книга.fb2.zip»), имя —
файл внутри. Без правки следующее сканирование сочло бы сжатую книгу новой:
завело бы новую запись (рейтинги samlib/author.today/fantlab/litmarket и
полки привязаны к старой — их пришлось бы скачивать заново), а старую
пометило бы удалённой. Здесь запись книги переводится на архив на месте —
тот же id; сканер видит архив неизменённым (размер каталога совпадает) и
пропускает его.

БД библиотеки fb2parser (.library_cache.db) — путь файла.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Callable, Optional, Tuple


def catalog_updater(library_path: Path, db_path: Optional[Path] = None
                    ) -> Tuple[Callable[[Path, Path], None], Callable[[], None]]:
    """(update(было, стало), close()) для compress_library(on_compressed=...);
    db_path — БД библиотеки fb2parser (по умолчанию — та, что у синхронизации)."""
    from fb2parser_core.synchronization import SynchronizationService
    from opds_catalog import opdsdb
    from opds_catalog.models import SIZE_BOOK_FILENAME, SIZE_BOOK_PATH, Book
    from opds_catalog.sopds_config import sopds_cfg as config

    from .fb2parser_bridge import _config_path

    root = Path(config.SOPDS_ROOT_LIB) if config.SOPDS_ROOT_LIB else None
    if db_path is None:
        db_path = SynchronizationService(_config_path()).db_path
    conn = sqlite3.connect(str(db_path)) if db_path.exists() else None

    def _within(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False

    def update(old: Path, new: Path) -> None:
        if root is not None and _within(old, root):
            rel_dir = os.path.relpath(old.parent, root)
            rel_zip = os.path.relpath(new, root)
            books = Book.objects.filter(filename=old.name[:SIZE_BOOK_FILENAME], path=rel_dir[:SIZE_BOOK_PATH])
            if books.exists():
                cat = opdsdb.addcattree(rel_zip, opdsdb.CAT_ZIP, new.stat().st_size)
                books.update(path=rel_zip[:SIZE_BOOK_PATH], catalog=cat, cat_type=opdsdb.CAT_ZIP)
        if conn is not None and _within(old, library_path):
            rel_old = old.relative_to(library_path)
            rel_new = new.relative_to(library_path)
            conn.execute("UPDATE books SET file_path = ? WHERE file_path IN (?, ?)",
                         (str(rel_new), str(rel_old), rel_old.as_posix()))
            conn.commit()

    def close() -> None:
        if conn is not None:
            conn.close()

    return update, close
