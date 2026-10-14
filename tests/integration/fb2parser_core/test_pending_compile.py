"""Автокомпиляция, не доделанная прерванной синхронизацией, доделывается
следующей (баг №150).

Сервер 2026-10-13: синхронизация перенесла 1 710 файлов, во время
автокомпиляции gunicorn убил процесс (WORKER TIMEOUT). Список затронутых
папок авторов жил только в памяти процесса — перенесённые тома так и
остались отдельными файлами, следующая синхронизация о них не знала.
"""
import os
import time

import pytest

import fb2parser_core.auto_compile_service as acs
import fb2parser_web.fb2parser_bridge as bridge
import fb2parser_web.views as views
from fb2parser_core.pending_compile import PendingCompile
from fb2parser_core.synchronization import SynchronizationService

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>Фантастика</genre>
<author><first-name>Иван</first-name><last-name>Иванов</last-name></author>
<book-title>Звезды {n}</book-title><sequence name="Звезды" number="{n}"/></title-info></description>
<body><section><p>Текст книги {n}.</p></section></body>
</FictionBook>
"""
OLD = time.time() - 3 * 3600


def _book(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FB2.format(n=n), encoding="utf-8")
    os.utime(path, (OLD, OLD))


def _sync(tmp_path, scan):
    svc = SynchronizationService(bridge._config_path())
    svc.library_path = tmp_path / "lib"
    svc.db_path = tmp_path / "sync.db"
    svc.last_scan_path = scan
    return svc


def _stop_at(prefix):
    def progress(current, total, status=""):
        if status.startswith(prefix):
            raise InterruptedError("Остановлено пользователем")
    return progress


def test_moved_author_dir_is_recorded_even_if_sync_is_stopped(tmp_path):
    scan = tmp_path / "in"
    for n in (1, 2, 3):
        _book(scan / "Иванов Иван" / "Звезды" / f"{n}. Звезды {n}.fb2", n)
    svc = _sync(tmp_path, scan)
    pending = PendingCompile(tmp_path / "pending.json")
    svc.pending_compile = pending

    with pytest.raises(InterruptedError):
        svc.synchronize(progress_callback=_stop_at("Перемещение в библиотеку: 2/"),
                        allowed_folders={str(scan / "Иванов Иван")})

    [author_dir] = pending.load()
    assert author_dir.endswith("Иванов Иван") and author_dir.startswith(str(tmp_path / "lib"))


def test_pending_compile_file_roundtrip(tmp_path):
    pending = PendingCompile.beside(tmp_path / "sync.lock")
    assert pending.load() == set()
    pending.add({"/lib/A", "/lib/B"})
    pending.add({"/lib/B"})
    assert pending.load() == {"/lib/A", "/lib/B"}
    pending.discard({"/lib/A", "/lib/X"})
    assert pending.load() == {"/lib/B"}
    pending.discard({"/lib/B"})
    assert not pending.path.exists()


class _FakeSync:
    def __init__(self, library_path):
        self.library_path = library_path
        self.last_scan_path = None
        self.pending_compile = None

    def synchronize(self, **kwargs):
        return {"files_moved": 0, "touched_author_dirs": set()}


@pytest.fixture
def web_sync(tmp_path, monkeypatch):
    lock = tmp_path / "sync.lock"
    monkeypatch.setattr(bridge, "_sync_lock_path", lambda: str(lock))
    monkeypatch.setattr(bridge, "get_sync_service", lambda: _FakeSync(tmp_path / "lib"))
    compiled = []

    def fake_compile(path, on_group=None, config_path=None, filter_paths=None, progress_callback=None):
        compiled.append(set(filter_paths))
        return {"ok": 1, "fail": 0}

    monkeypatch.setattr(acs, "auto_compile_library", fake_compile)
    views.sync_job.finish()
    views.sync_job.reset()
    yield PendingCompile.beside(lock), compiled
    views.sync_job.finish()
    views.sync_job.reset()


def _run(auto_compile):
    # выбранные папки обязательны (баг №147); _FakeSync их не читает
    assert views.sync_job.try_start(scan_path="", allowed_folders=["/in/Папка"], auto_compile=auto_compile)
    views._run_sync_thread()
    return views.sync_job.get()


def test_next_sync_compiles_leftover_dirs_even_without_moved_files(web_sync):
    pending, compiled = web_sync
    pending.add({"/lib/Фантастика/Emory Faded"})

    state = _run(auto_compile=True)

    assert not state.get("error")
    assert compiled == [{"/lib/Фантастика/Emory Faded"}]
    assert pending.load() == set()
    assert any("прерванной синхронизации" in line for line in state["log"])


def test_leftover_dirs_stay_pending_when_compile_is_stopped(web_sync, monkeypatch):
    pending, compiled = web_sync
    pending.add({"/lib/Фантастика/Emory Faded"})

    def stopped(*args, **kwargs):
        raise InterruptedError("Остановлено пользователем")

    monkeypatch.setattr(acs, "auto_compile_library", stopped)
    _run(auto_compile=True)

    assert pending.load() == {"/lib/Фантастика/Emory Faded"}


def test_sync_without_auto_compile_leaves_pending_dirs_alone(web_sync):
    pending, compiled = web_sync
    pending.add({"/lib/Фантастика/Emory Faded"})

    _run(auto_compile=False)

    assert compiled == []
    assert pending.load() == {"/lib/Фантастика/Emory Faded"}
