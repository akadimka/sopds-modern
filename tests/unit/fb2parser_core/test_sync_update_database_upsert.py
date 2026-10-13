"""`SynchronizationService._update_database` — конфликт пути (баг №143).

Сервер 2026-10-13: синхронизация перенесла 1 710 файлов, а в БД библиотеки
не записала ни одного — «UNIQUE constraint failed: books.file_path». Файл,
заменивший уже лежащий в библиотеке (тот же путь), конфликтовал со старой
записью, и исключение обрывало executemany целиком. Запись с тем же путём —
это обновление существующей.
"""
import sqlite3
from types import SimpleNamespace

from fb2parser_core.synchronization import SynchronizationService


def _service(tmp_path):
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.db_path = tmp_path / "cache.db"
    svc.library_path = tmp_path / "lib"
    svc.library_path.mkdir()
    svc.stats = {"errors": 0}
    svc._log = lambda msg: None
    return svc


def _record(path, author):
    return SimpleNamespace(
        proposed_author=author, author_source="folder_dataset",
        proposed_series="Серия", series_source="folder_dataset", series_number="1",
        subseries="", file_title="Книга", file_path=path, metadata_genre="Фантастика",
    )


def test_existing_path_is_updated_and_the_rest_inserted(tmp_path):
    svc = _service(tmp_path)
    for name in ("old.fb2", "new.fb2"):
        (svc.library_path / name).write_bytes(b"<FictionBook/>")
    svc._update_database([_record("old.fb2", "Старый Автор")])
    conn = sqlite3.connect(str(svc.db_path))
    added_before = conn.execute("select added_date from books where file_path='old.fb2'").fetchone()[0]
    conn.close()

    svc._update_database([_record("old.fb2", "Новый Автор"), _record("new.fb2", "Другой Автор")])

    conn = sqlite3.connect(str(svc.db_path))
    rows = dict(conn.execute("select file_path, author from books").fetchall())
    added_after = conn.execute("select added_date from books where file_path='old.fb2'").fetchone()[0]
    conn.close()
    assert rows == {"old.fb2": "Новый Автор", "new.fb2": "Другой Автор"}
    assert added_after == added_before
    assert svc.stats["errors"] == 0


_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>
<genre>Фантастика</genre><author><first-name>Владимир</first-name><last-name>Ящерицын</last-name></author>
<book-title>{title}</book-title>{seq}
</title-info></description><body><section><p>Текст</p></section></body></FictionBook>
"""


def _lib_file(lib, rel, title, seq=""):
    path = lib / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FB2.format(title=title, seq=seq), encoding="utf-8")


def test_index_missing_library_files(tmp_path):
    """Восстановление записей для файлов библиотеки без записи в БД — так,
    как их внесла бы синхронизация (поля, по которым ищутся дубли)."""
    svc = _service(tmp_path)
    lib = svc.library_path
    _lib_file(lib, "Фантастика/Ящерицын Владимир/Рассвет Тьмы/"
                   "Ящерицын Владимир - Рассвет Тьмы. Конфронтация т. 2.fb2",
              "Конфронтация", '<sequence name="Рассвет Тьмы" number="5"/>')
    # без серии: синхронизация оставляет в файле исходный <sequence> издательства
    _lib_file(lib, "Фантастика/Ящерицын Владимир/Ящерицын Владимир - Отдельная.fb2",
              "Отдельная", '<sequence name="Современный фантастический боевик"/>')
    _lib_file(lib, "Фантастика/Ящерицын Владимир/Рассвет Тьмы/"
                   "Ящерицын Владимир - Рассвет Тьмы т. 1.fb2",
              "Рассвет Тьмы", '<sequence name="Рассвет Тьмы" number="1"/>')
    svc._update_database([_record(
        "Фантастика/Ящерицын Владимир/Рассвет Тьмы/Ящерицын Владимир - Рассвет Тьмы т. 1.fb2",
        "Ящерицын Владимир")])

    stats = svc.index_missing_library_files()

    assert stats == {"library_files": 3, "already_indexed": 1, "added": 2, "errors": 0}
    conn = sqlite3.connect(str(svc.db_path))
    rows = {r[0].split("/")[-1]: r[1:] for r in conn.execute(
        "select file_path, author, series, series_number, title, genre from books")}
    conn.close()
    assert rows["Ящерицын Владимир - Рассвет Тьмы. Конфронтация т. 2.fb2"] == (
        "Ящерицын Владимир", "Рассвет Тьмы", "2", "Конфронтация", "Фантастика")
    assert rows["Ящерицын Владимир - Отдельная.fb2"] == (
        "Ящерицын Владимир", "", "", "Отдельная", "Фантастика")
    # повторный запуск ничего не добавляет
    assert svc.index_missing_library_files()["added"] == 0
