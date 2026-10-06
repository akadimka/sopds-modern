"""Автосинхронизация, этап 2 — запуск по режиму (docs/watch-folder-autosync-design.md).

Уверенная книга получает жанр в <genre>, уезжает в библиотеку обычной
синхронизацией, исходник из папки наблюдения исчезает. Спорная остаётся
нетронутой и попадает в журнал как «ждёт решения». Исходные коды уехавшей
книги запоминаются (решено автоматически). Пробный режим ничего не трогает;
занятая ручной синхронизацией блокировка — запуск пропускается.
"""
import os
import sqlite3
import time
import zipfile

from fb2parser_core.autosync_service import (
    MODE_AUTO,
    MODE_DRY_RUN,
    OUTCOME_MOVED,
    OUTCOME_PENDING,
    OUTCOME_WOULD_MOVE,
    RUN_BUSY,
    RUN_DONE,
    AutosyncService,
)
from fb2parser_core.genres_manager import GenresManager
from fb2parser_core.library_memory import LibraryMemory
from fb2parser_core.synchronization import SynchronizationService
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


def _book(path, genre, first, last, series, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    title = f"{series} {n}"
    path.write_text(_FB2.format(genre=genre, first=first, last=last, title=title, series=series,
                                n=n, text=_TEXT + title), encoding="utf-8")
    os.utime(path, (OLD, OLD))
    return path


def _setup(tmp_path, monkeypatch, mode):
    lib = tmp_path / "lib"
    for n in (1, 2, 3):
        _book(lib / "Фантастика" / "Иванов Иван" / "Звезды" / f"Иванов Иван - Звезды {n}.fb2",
              "Фантастика", "Иван", "Иванов", "Звезды", n)
    watch = tmp_path / "watch"
    sure = _book(watch / "Иванов Иван" / "Звезды" / "4. Звезды 4.fb2", "sf_action", "Иван", "Иванов", "Звезды", 4)
    doubtful = _book(watch / "Чужая порция" / "Петров Пётр - Тайна.fb2", "unknown_code", "Пётр", "Петров",
                     "Тайна", 1)

    gm = GenresManager(str(tmp_path / "genres.xml"))
    gm.load()
    gm.add_node("Фантастика")
    gm.add_node("Детектив")
    compiled = []

    def fake_compile(*_args, **kwargs):
        compiled.append(kwargs.get("filter_paths"))
        return {"ok": 0, "fail": 0}

    def make_sync():
        sync = SynchronizationService(_config_path())
        sync.library_path = lib
        sync.db_path = tmp_path / "sync_cache.db"
        return sync

    svc = AutosyncService(
        _config_path(), LibraryMemory(tmp_path / "mem.db"), gm,
        lock_path=str(tmp_path / "sync.lock"), sync_factory=make_sync,
        compile_fn=fake_compile,
    )
    monkeypatch.setattr(svc.settings, "get_library_path", lambda: str(lib))
    svc.cfg.update(mode=mode, watch_folder=str(watch), quiet_minutes=30, confidence=0.8)
    return svc, lib, watch, sure, doubtful, compiled


def _genre_of(path):
    data = path.read_bytes()
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            data = z.read(z.namelist()[0])
    text = data.decode("utf-8")
    return text[text.index("<genre>") + 7:text.index("</genre>")]


def test_auto_moves_sure_book_and_keeps_doubtful(tmp_path, monkeypatch):
    svc, lib, watch, sure, doubtful, compiled = _setup(tmp_path, monkeypatch, MODE_AUTO)
    doubtful_before = doubtful.read_bytes()

    result = svc.run()

    assert result.status == RUN_DONE, result.error
    assert len(result.moved) == 1 and result.pending == 1
    moved_path = lib / result.moved[0]["library_path"]
    assert moved_path.exists() and moved_path.relative_to(lib).parts[:3] == ("Фантастика", "Иванов Иван", "Звезды")
    assert _genre_of(moved_path) == "Фантастика"
    assert not sure.exists()
    assert doubtful.read_bytes() == doubtful_before
    assert compiled, "автокомпиляция по затронутым авторам должна запускаться"

    books = {b["source"]: b for b in svc.journal.books(result.run_id)}
    assert {b["outcome"] for b in books.values()} == {OUTCOME_MOVED, OUTCOME_PENDING}
    assert svc.journal.last_run()["status"] == RUN_DONE

    with sqlite3.connect(svc.memory.db_path) as conn:
        assert conn.execute("SELECT codes, batch, decided_by FROM orig_codes").fetchall() == [
            ("sf_action", "Иванов Иван", "auto")]


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    svc, lib, watch, sure, doubtful, _compiled = _setup(tmp_path, monkeypatch, MODE_DRY_RUN)
    before = {p: p.read_bytes() for p in (sure, doubtful)}

    result = svc.run()

    assert result.status == RUN_DONE and not result.moved
    assert {p: p.read_bytes() for p in (sure, doubtful)} == before
    outcomes = sorted(b["outcome"] for b in svc.journal.books(result.run_id))
    assert outcomes == sorted([OUTCOME_WOULD_MOVE, OUTCOME_PENDING])


def test_run_is_skipped_while_manual_sync_holds_the_lock(tmp_path, monkeypatch):
    svc, lib, watch, sure, doubtful, _compiled = _setup(tmp_path, monkeypatch, MODE_AUTO)
    (tmp_path / "sync.lock").write_text('{"owner": "web", "pid": %d, "since": %f}' % (os.getpid(), time.time()),
                                        encoding="utf-8")

    result = svc.run()

    assert result.status == RUN_BUSY and "web" in result.error
    assert sure.exists()
