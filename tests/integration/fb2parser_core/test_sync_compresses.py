"""Синхронизация кладёт книги в библиотеку сразу сжатыми (2026-11-05).

Раньше книга ложилась .fb2 и до ручного «Сжать» занимала вдвое больше места,
а ночное зеркало увозило её на сервер-2 несжатой (2 390 книг 2026-11-05).
Теперь — .fb2.zip, в БД синхронизации и в итогах — путь архива. Записи БД
стали живыми (раньше после «Сжать» вчищались по несуществующему .fb2), и
проверка «дубликат — уже в БД» удаляла бы более полную версию книги: она
теперь сравнивает содержимое — точная копия по-прежнему дубликат.
"""
import sqlite3

import pytest

from fb2parser_core.fb2_utils import read_fb2_bytes
from tests.integration.fb2parser_core.test_ownership_in_operations import (  # noqa: F401
    _book,
    _sync,
    lib,
    users,
)

pytestmark = pytest.mark.django_db


def test_book_lands_compressed_and_db_has_the_archive(tmp_path, lib, users):
    scan = tmp_path / "in"
    _book(scan / "Иванов Иван" / "Звезды" / "1. Звезды 1.fb2", 1)
    svc = _sync(tmp_path, lib, scan)
    stats = svc.synchronize(allowed_folders={str(scan / "Иванов Иван")})
    [placed] = lib.rglob("*.fb2.zip")
    assert not list(lib.rglob("*.fb2"))
    rel = placed.relative_to(lib)
    with sqlite3.connect(str(svc.db_path)) as conn:
        assert [r[0].replace("\\", "/") for r in conn.execute("SELECT file_path FROM books")] == [rel.as_posix()]
    assert [m["path"].replace("\\", "/") for m in stats["moved_books"]] == [rel.as_posix()]


def test_exact_copy_is_still_a_duplicate_and_fuller_version_replaces(tmp_path, lib, users):
    scan = tmp_path / "in"
    _book(scan / "Иванов Иван" / "Звезды" / "1. Звезды 1.fb2", 1)
    _sync(tmp_path, lib, scan).synchronize(allowed_folders={str(scan / "Иванов Иван")})
    [placed] = lib.rglob("*.fb2.zip")

    again = tmp_path / "again"                                   # та же книга ещё раз
    copy = _book(again / "Иванов Иван" / "Звезды" / "1. Звезды 1.fb2", 1)
    stats = _sync(tmp_path, lib, again).synchronize(allowed_folders={str(again / "Иванов Иван")})
    assert stats["duplicates_found"] == 1 and not copy.exists()
    assert list(lib.rglob("*.fb2*")) == [placed]

    fuller = tmp_path / "fuller"                                 # та же, но полнее
    _book(fuller / "Иванов Иван" / "Звезды" / "1. Звезды 1.fb2", 1, extra=" Дополнено." * 50)
    _sync(tmp_path, lib, fuller).synchronize(allowed_folders={str(fuller / "Иванов Иван")})
    assert list(lib.rglob("*.fb2*")) == [placed]
    assert "Дополнено." in read_fb2_bytes(placed).decode("utf-8")
