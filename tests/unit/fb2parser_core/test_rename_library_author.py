"""`SynchronizationService.rename_library_author` — переименование автора,
уже лежащего в библиотеке (баг №158): папка, имена файлов, <author>, БД."""
import sqlite3

from fb2parser_core.fb2_utils import read_fb2_bytes
from fb2parser_core.synchronization import SynchronizationService

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>
<genre>sf</genre><author><last-name>Призывающий</last-name><first-name>Оро</first-name></author>
<book-title>Книга</book-title></title-info></description><body><section><p>Текст</p></section></body></FictionBook>
"""


def _service(tmp_path):
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.db_path = tmp_path / "cache.db"
    svc.library_path = tmp_path / "lib"
    svc.stats = {"errors": 0}
    svc._log = lambda msg: None
    return svc


def _library(svc):
    old = svc.library_path / "Фантастика" / "Призывающий Оро" / "Серия"
    old.mkdir(parents=True)
    for n in (1, 2):
        (old / f"Призывающий Оро - Серия т. {n}.fb2").write_text(_FB2, encoding="utf-8")
    conn = sqlite3.connect(str(svc.db_path))
    conn.execute("CREATE TABLE books (file_path TEXT UNIQUE, author TEXT)")
    for n in (1, 2):
        conn.execute("INSERT INTO books VALUES (?, ?)",
                     (f"Фантастика/Призывающий Оро/Серия/Призывающий Оро - Серия т. {n}.fb2", "Призывающий Оро"))
    conn.commit()
    conn.close()


def test_dry_run_changes_nothing(tmp_path):
    svc = _service(tmp_path)
    _library(svc)
    stats = svc.rename_library_author("Призывающий Оро", "Оро Призывающий")
    assert stats["moved"] == 2
    assert (svc.library_path / "Фантастика" / "Призывающий Оро").is_dir()
    assert not (svc.library_path / "Фантастика" / "Оро Призывающий").exists()


def test_apply_renames_folder_files_tags_and_db(tmp_path):
    svc = _service(tmp_path)
    _library(svc)
    # уже есть новая папка (книга, синхронизированная после смены правила) — слияние
    newer = svc.library_path / "Фантастика" / "Оро Призывающий" / "Серия"
    newer.mkdir(parents=True)
    (newer / "Оро Призывающий - Серия т. 2.fb2").write_text(_FB2, encoding="utf-8")

    stats = svc.rename_library_author("Призывающий Оро", "Оро Призывающий", apply=True)

    assert stats == {"files": 2, "moved": 1, "conflicts": 1, "errors": 0, "db_rows": 1}
    moved = newer / "Оро Призывающий - Серия т. 1.fb2"
    assert moved.exists()
    text = read_fb2_bytes(moved).decode("utf-8")
    assert "<last-name>Оро</last-name>" in text and "<first-name>Призывающий</first-name>" in text
    # занятый файл не тронут — старая папка остаётся с ним
    assert (svc.library_path / "Фантастика" / "Призывающий Оро" / "Серия" / "Призывающий Оро - Серия т. 2.fb2").exists()
    conn = sqlite3.connect(str(svc.db_path))
    rows = dict(conn.execute("select file_path, author from books"))
    conn.close()
    assert rows[str(moved.relative_to(svc.library_path))] == "Оро Призывающий"
