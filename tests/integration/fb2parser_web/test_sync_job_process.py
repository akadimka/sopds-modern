"""Синхронизация из окна сайта: поток или отдельный процесс (баги №144, №147).

№144 вынес синхронизацию в отдельный процесс, общающийся с сайтом через
кэш. С кэшем в памяти процесса (LocMemCache — локальная разработка) у
дочернего процесса был СВОЙ пустой кэш: он не видел выбранных папок и
синхронизировал всю исходную папку (весь Test1 ушёл в библиотеку), а сайт
не видел ни прогресса, ни завершения — «идёт» навсегда, тем более что
проверка «процесс жив» на Windows всегда отвечала «да».
"""
import subprocess
import sys
from types import SimpleNamespace

import pytest
from django.core.management import call_command

import fb2parser_web.fb2parser_bridge as bridge
import fb2parser_web.views as views
from fb2parser_core.sync_lock import _pid_alive


@pytest.fixture(autouse=True)
def _clean_job():
    views.sync_job.reset()
    views.sync_job.finish()
    yield
    views.sync_job.reset()
    views.sync_job.finish()


def test_process_local_cache_runs_sync_as_a_thread(monkeypatch):
    assert not views._cache_shared_between_processes()  # тесты — LocMemCache
    started, spawned = [], []
    monkeypatch.setattr(views, "threading", SimpleNamespace(
        Thread=lambda target, daemon: SimpleNamespace(start=lambda: started.append(target))))
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: spawned.append(a))
    views._spawn_sync_process()
    assert started == [views._run_sync_thread] and spawned == []


def test_sync_refuses_to_run_without_selected_folders(monkeypatch, tmp_path):
    calls = []

    class FakeService:
        last_scan_path = None
        library_path = tmp_path

        def synchronize(self, **kw):
            calls.append(kw)
            return {}

    monkeypatch.setattr(bridge, "get_sync_service", lambda: FakeService())
    monkeypatch.setattr(bridge, "_sync_lock_path", lambda: str(tmp_path / "sync.lock"))
    assert views.sync_job.try_start(processed=0, total=0, current="", stats={}, log=[],
                                    scan_path=str(tmp_path), allowed_folders=None)
    views._run_sync_thread()
    state = views.sync_job.get()
    assert calls == []  # synchronize() не вызывался — ничего не перемещено
    assert not state["running"] and "no folders selected" in state["error"]


def test_job_command_without_parameters_does_nothing(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(views, "_run_sync_thread", lambda: ran.append(1))
    call_command("fb2parser_sync_job")  # в кэше нет запущенной задачи с папками
    assert ran == []
    assert "refusing to synchronize" in capsys.readouterr().err


def test_dead_job_process_is_noticed():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    assert not _pid_alive(proc.pid)

    views.sync_job.try_start(processed=0, total=0, current="", allowed_folders={"x"})
    views.sync_job.update(pid=proc.pid)
    views._reap_dead_sync_job()
    state = views.sync_job.get()
    assert not state["running"] and state["error"]
