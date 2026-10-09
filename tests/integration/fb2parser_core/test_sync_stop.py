"""«Стоп» синхронизации останавливает её на любом этапе, не ломая данные.

Раньше кнопка срабатывала только там, где вызывался колбэк прогресса:
перенос файлов его не вызывал вовсе, запись в БД «проглатывала» остановку
общим except (и оставалась без commit), автокомпиляция после синхронизации
её не проверяла, а веб-обработчик компиляции глушил исключение — синхронизация
продолжалась до конца.

Теперь: перенос прерывается перед следующим файлом, уже перенесённые
вносятся в БД (иначе в библиотеке остались бы книги, о которых база не
знает), затем — «Остановлено пользователем»; автокомпиляция прерывается
между группами (каждая серия компилируется целиком или не трогается).
"""
import os
import sqlite3
import time

import pytest

from fb2parser_core.auto_compile_service import auto_compile_library
from fb2parser_core.synchronization import SynchronizationService
from fb2parser_web.fb2parser_bridge import _config_path

_TEXT = "Длинный текст книги, которого достаточно для отпечатка. " * 40
_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>Фантастика</genre>
<author><first-name>Иван</first-name><last-name>Иванов</last-name></author>
<book-title>{title}</book-title><sequence name="Звезды" number="{n}"/></title-info></description>
<body><title><p>{title}</p></title><section><p>{text}</p></section></body>
</FictionBook>
"""
OLD = time.time() - 3 * 3600


def _book(path, n):
    path.parent.mkdir(parents=True, exist_ok=True)
    title = f"Звезды {n}"
    path.write_text(_FB2.format(title=title, n=n, text=_TEXT + title), encoding="utf-8")
    os.utime(path, (OLD, OLD))
    return path


def _sync(tmp_path, scan):
    svc = SynchronizationService(_config_path())
    svc.library_path = tmp_path / "lib"
    svc.db_path = tmp_path / "sync.db"
    svc.last_scan_path = scan
    return svc


def _stop_at(prefix):
    def progress(current, total, status=""):
        if status.startswith(prefix):
            raise InterruptedError("Остановлено пользователем")
    return progress


def test_stop_during_move_keeps_library_and_db_consistent(tmp_path):
    scan = tmp_path / "in"
    sources = [_book(scan / "Иванов Иван" / "Звезды" / f"{n}. Звезды {n}.fb2", n) for n in (1, 2, 3)]
    svc = _sync(tmp_path, scan)

    with pytest.raises(InterruptedError):
        svc.synchronize(progress_callback=_stop_at("Перемещение в библиотеку: 2/"),
                        allowed_folders={str(scan / "Иванов Иван")})

    in_library = [p for p in (tmp_path / "lib").rglob("*.fb2")]
    assert len(in_library) == 1                       # перенесён ровно один файл
    assert sum(1 for p in sources if p.exists()) == 2  # остальные остались в исходной папке
    with sqlite3.connect(svc.db_path) as conn:
        rows = conn.execute("SELECT file_path FROM books").fetchall()
    assert len(rows) == 1                             # и он записан в базу


def test_stop_before_move_moves_nothing(tmp_path):
    scan = tmp_path / "in"
    sources = [_book(scan / "Иванов Иван" / "Звезды" / f"{n}. Звезды {n}.fb2", n) for n in (1, 2)]
    with pytest.raises(InterruptedError):
        _sync(tmp_path, scan).synchronize(progress_callback=_stop_at("Перемещение в библиотеку: 1/"),
                                          allowed_folders={str(scan / "Иванов Иван")})
    assert all(p.exists() for p in sources) and not list((tmp_path / "lib").rglob("*.fb2"))


def test_stop_in_auto_compile_leaves_series_untouched(tmp_path):
    lib = tmp_path / "lib"
    vols = [_book(lib / "Фантастика" / "Иванов Иван" / "Звезды" / f"Иванов Иван - Звезды {n}.fb2", n)
            for n in (1, 2, 3)]
    with pytest.raises(InterruptedError):
        auto_compile_library(str(lib), config_path=_config_path(), progress_callback=_stop_at("Компиляция:"))
    assert all(p.exists() for p in vols)
    assert len(list(lib.rglob("*.fb2"))) == 3


def test_web_compile_pass_reports_stop_instead_of_hiding_it(monkeypatch, tmp_path):
    import fb2parser_core.auto_compile_service as acs
    import fb2parser_web.views as views

    def fake_compile(path, on_group=None, config_path=None, filter_paths=None, progress_callback=None):
        progress_callback(0, 1, "Компиляция: А — С")
        raise AssertionError("должно было остановиться")

    monkeypatch.setattr(acs, "auto_compile_library", fake_compile)
    views.sync_stop_flag.set()
    log = []
    try:
        with pytest.raises(InterruptedError):
            views._run_compile_pass(tmp_path, log.append, "тест")
    finally:
        views.sync_stop_flag.clear()
    assert any("остановлена" in line for line in log)
