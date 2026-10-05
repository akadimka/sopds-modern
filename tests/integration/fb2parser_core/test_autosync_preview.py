"""Автосинхронизация, этап 1 — отчёт «что бы я сделал» (docs/watch-folder-autosync-design.md).

Порция — подпапка верхнего уровня папки наблюдения. Ещё качающаяся
(недокачанный файл или свежие изменения) ждёт следующего запуска.
`preview` ничего не трогает: проверяем и решения, и неизменность файлов.
"""
import os
import time

from fb2parser_core.autosync_service import (
    STATUS_DOWNLOADING,
    STATUS_EMPTY,
    STATUS_LOOSE,
    STATUS_READY,
    AutosyncService,
)
from fb2parser_core.genres_manager import GenresManager
from fb2parser_core.library_memory import LibraryMemory
from fb2parser_web.fb2parser_bridge import _config_path

_TEXT = "Длинный текст книги, которого достаточно для отпечатка. " * 40

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<genre>{genre}</genre>
<author><first-name>{first}</first-name><last-name>{last}</last-name></author>
<book-title>{title}</book-title>
<sequence name="{series}" number="{n}"/>
</title-info>
</description>
<body><title><p>{title}</p></title><section><p>{text}</p></section></body>
</FictionBook>
"""

OLD = time.time() - 3 * 3600


def _book(path, genre, first, last, series, n, title=None, old=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    title = title or f"{series} {n}"
    path.write_text(_FB2.format(genre=genre, first=first, last=last, title=title, series=series,
                                n=n, text=_TEXT + title), encoding="utf-8")
    if old:
        os.utime(path, (OLD, OLD))
    return path


def _service(tmp_path, monkeypatch, library):
    gm = GenresManager(str(tmp_path / "genres.xml"))
    gm.load()
    gm.add_node("Фантастика")
    gm.add_node("Детектив")
    svc = AutosyncService(_config_path(), LibraryMemory(tmp_path / "mem.db"), gm)
    monkeypatch.setattr(svc.settings, "get_library_path", lambda: str(library))
    svc.cfg.update(quiet_minutes=30, confidence=0.8)
    return svc


def test_batch_statuses(tmp_path, monkeypatch):
    watch = tmp_path / "watch"
    _book(watch / "Готовая" / "1.fb2", "sf_action", "Иван", "Иванов", "Звёзды", 1)
    _book(watch / "Свежая" / "1.fb2", "sf_action", "Иван", "Иванов", "Звёзды", 1, old=False)
    _book(watch / "Качается" / "1.fb2", "sf_action", "Иван", "Иванов", "Звёзды", 1)
    (watch / "Качается" / "2.fb2.part").write_bytes(b"x")
    (watch / "Пустая").mkdir()
    _book(watch / "в корне.fb2", "sf_action", "Иван", "Иванов", "Звёзды", 1)
    svc = _service(tmp_path, monkeypatch, tmp_path / "lib")
    statuses = {r.name: r.status for r in svc.discover_batches(str(watch))}
    assert statuses == {"Готовая": STATUS_READY, "Свежая": STATUS_DOWNLOADING,
                        "Качается": STATUS_DOWNLOADING, "Пустая": STATUS_EMPTY, "": STATUS_LOOSE}


def test_preview_new_volume_follows_library_series_and_touches_nothing(tmp_path, monkeypatch):
    lib = tmp_path / "lib"
    for n in (1, 2, 3):
        _book(lib / "Фантастика" / "Иванов Иван" / "Звёзды" / f"Иванов Иван - Звёзды {n}.fb2",
              "Фантастика", "Иван", "Иванов", "Звёзды", n)
    watch = tmp_path / "watch"
    new = _book(watch / "Иванов Иван" / "Звёзды" / "4. Звёзды 4.fb2", "sf_action", "Иван", "Иванов", "Звёзды", 4)
    stranger = _book(watch / "Чужая порция" / "Петров Пётр - Тайна.fb2", "unknown_code", "Пётр", "Петров",
                     "Тайна", 1)
    before = {p: p.read_bytes() for p in (new, stranger)}

    svc = _service(tmp_path, monkeypatch, lib)
    reports = {r.name: r for r in svc.preview(str(watch))}

    known = reports["Иванов Иван"]
    assert known.status == STATUS_READY and known.auto_books == 1
    d = known.decisions[0]
    assert d.genre == "Фантастика" and ("series", "Фантастика") in d.signals

    unknown = reports["Чужая порция"]
    assert unknown.auto_books == 0 and unknown.pending_books == 1

    assert {p: p.read_bytes() for p in (new, stranger)} == before
