"""Синхронизация не кладёт книгу в жанровую папку не из списка жанров.

Сервер 2026-11-02: папки «poetry», «prose_military» и «Без жанра» в
библиотеке. Теперь книга с таким жанром остаётся в исходной папке (не
удаляется) с заметкой «жанр не из списка», а книга в .fb2.zip получает жанр
и название из архива (раньше — «Без жанра» и «[no title]»).
"""
import os
import time
import zipfile

from fb2parser_core.synchronization import SynchronizationService
from fb2parser_web.fb2parser_bridge import _config_path

_TEXT = "Длинный текст книги, которого достаточно для отпечатка. " * 40
_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>{genre}
<author><first-name>Иван</first-name><last-name>Иванов</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><title><p>{title}</p></title><section><p>{text}</p></section></body>
</FictionBook>
"""
OLD = time.time() - 3 * 3600


def _book(path, title, genre, zipped=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    tag = f"<genre>{genre}</genre>" if genre else ""
    data = _FB2.format(genre=tag, title=title, text=_TEXT + title).encode("utf-8")
    if zipped:
        path = path.with_name(path.name + ".zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(path.name[:-4], data)
    else:
        path.write_bytes(data)
    os.utime(path, (OLD, OLD))
    return path


def _sync(tmp_path, scan):
    svc = SynchronizationService(_config_path())
    svc.library_path = tmp_path / "lib"
    svc.db_path = tmp_path / "sync.db"
    svc.last_scan_path = scan
    return svc


def test_book_with_genre_not_in_list_stays_in_source(tmp_path):
    scan = tmp_path / "in"
    folder = scan / "Иванов Иван"
    good = _book(folder / "Иванов Иван - Звезды.fb2", "Звезды", "Фантастика")
    held = [
        _book(folder / "Иванов Иван - Стихи.fb2", "Стихи", "poetry"),
        _book(folder / "Иванов Иван - Безымянная.fb2", "Безымянная", ""),
        _book(folder / "Иванов Иван - Порченая.fb2", "Порченая", "??????????"),
    ]
    svc = _sync(tmp_path, scan)
    stats = svc.synchronize(allowed_folders={str(folder)})

    lib = tmp_path / "lib"
    assert {p.name for p in lib.iterdir()} == {"Фантастика"}
    assert not good.exists()
    assert all(p.exists() for p in held)                        # не перенесены и не удалены
    notes = {n["file_path"]: n["genre"] for n in stats["unknown_genre_notes"]}
    assert {os.path.basename(p) for p in notes} == {p.name for p in held}
    assert sorted(notes.values()) == ["", "??????????", "poetry"]


def test_zipped_book_gets_genre_and_title_from_archive(tmp_path):
    scan = tmp_path / "in"
    folder = scan / "Иванов Иван"
    src = _book(folder / "Иванов Иван - Звезды.fb2", "Звезды над морем", "Фантастика", zipped=True)
    _sync(tmp_path, scan).synchronize(allowed_folders={str(folder)})

    [moved] = list((tmp_path / "lib").rglob("*.fb2*"))
    assert moved.relative_to(tmp_path / "lib").parts[0] == "Фантастика"
    assert "Звезды над морем" in moved.name and "[no title]" not in moved.name
    assert not src.exists()
