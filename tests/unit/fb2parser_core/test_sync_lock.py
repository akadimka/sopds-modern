"""Блокировка синхронизации между веб-процессом и плановой автосинхронизацией."""
import json
import os
import time

import pytest

from fb2parser_core.sync_lock import SyncBusy, sync_lock


def test_lock_is_released_after_use(tmp_path):
    lock = tmp_path / "sync.lock"
    with sync_lock(lock, "autosync"):
        assert json.loads(lock.read_text(encoding="utf-8"))["owner"] == "autosync"
    assert not lock.exists()


def test_second_owner_gets_busy_with_holder_name(tmp_path):
    lock = tmp_path / "sync.lock"
    with sync_lock(lock, "web"):
        with pytest.raises(SyncBusy) as busy:
            with sync_lock(lock, "autosync"):
                pass
        assert busy.value.owner == "web"
    assert not lock.exists()


def test_stale_lock_left_by_crashed_process_is_taken_over(tmp_path):
    lock = tmp_path / "sync.lock"
    lock.write_text(json.dumps({"owner": "web", "pid": os.getpid(), "since": time.time() - 7 * 3600}),
                    encoding="utf-8")
    with sync_lock(lock, "autosync"):
        assert json.loads(lock.read_text(encoding="utf-8"))["owner"] == "autosync"


# --- Общая блокировка в корне библиотеки (2026-11-05) ---------------------
# 2026-11-04 синхронизация с ПК (библиотека по SMB) шла одновременно с чисткой
# на сервере: у каждой машины была своя блокировка, они друг друга не видели.

from fb2parser_core import sync_lock as sl  # noqa: E402


def _other_host_lock(library, beat_age, owner="library_cleanup"):
    now = time.time()
    (library / sl.LIBRARY_LOCK_NAME).write_text(json.dumps({
        "owner": owner, "host": "other-machine", "pid": 999999,
        "since": now - 6 * 3600, "beat": now - beat_age}), encoding="utf-8")


def test_library_lock_lives_in_the_library_and_is_released(tmp_path):
    library = tmp_path / "lib"
    library.mkdir()
    with sync_lock(tmp_path / "sync.lock", "web", library=library):
        info = json.loads((library / sl.LIBRARY_LOCK_NAME).read_text(encoding="utf-8"))
        assert info["owner"] == "web" and info["host"] == sl.HOST and "beat" in info
        assert sl.library_lock_holder(library)["owner"] == "web"
    assert not (library / sl.LIBRARY_LOCK_NAME).exists()
    assert sl.library_lock_holder(library) is None


def test_lock_of_another_machine_blocks_even_with_unknown_pid(tmp_path):
    library = tmp_path / "lib"
    library.mkdir()
    _other_host_lock(library, beat_age=60)
    with pytest.raises(SyncBusy) as busy:
        with sync_lock(tmp_path / "sync.lock", "web", library=library):
            pass
    assert busy.value.owner == "library_cleanup (other-machine)"
    assert not (tmp_path / "sync.lock").exists()          # своя снята, раз общую не взяли


def test_silent_lock_of_another_machine_is_taken_over(tmp_path):
    library = tmp_path / "lib"
    library.mkdir()
    _other_host_lock(library, beat_age=sl.BEAT_STALE + 60)
    with sync_lock(tmp_path / "sync.lock", "web", library=library):
        assert json.loads((library / sl.LIBRARY_LOCK_NAME).read_text(encoding="utf-8"))["owner"] == "web"


def test_long_operation_keeps_its_lock_while_beating(tmp_path):
    """Шаг 3 чистки идёт ~6 ч: живая отметка — блокировка не устарела, хотя
    взята давно (раньше снималась через 6 ч)."""
    lock = tmp_path / "sync.lock"
    now = time.time()
    lock.write_text(json.dumps({"owner": "library_cleanup", "host": sl.HOST, "pid": os.getpid(),
                                "since": now - 7 * 3600, "beat": now - 30}), encoding="utf-8")
    with pytest.raises(SyncBusy):
        with sync_lock(lock, "web"):
            pass


def test_heartbeat_refreshes_the_mark(tmp_path, monkeypatch):
    monkeypatch.setattr(sl, "BEAT_EVERY", 0.05)
    library = tmp_path / "lib"
    library.mkdir()
    with sync_lock(tmp_path / "sync.lock", "web", library=library):
        first = json.loads((library / sl.LIBRARY_LOCK_NAME).read_text(encoding="utf-8"))["beat"]
        time.sleep(0.3)
        later = json.loads((library / sl.LIBRARY_LOCK_NAME).read_text(encoding="utf-8"))["beat"]
    assert later > first


def test_missing_library_folder_is_not_created(tmp_path):
    with sync_lock(tmp_path / "sync.lock", "web", library=tmp_path / "нет"):
        pass
    assert not (tmp_path / "нет").exists()


def test_watcher_ignores_the_library_lock(tmp_path):
    from opds_catalog.management.commands.sopds_watch import LibraryDirtyTracker

    tracker = LibraryDirtyTracker()
    tracker.mark(str(tmp_path / sl.LIBRARY_LOCK_NAME), is_directory=False)
    assert tracker.take_ready(0) is None
    tracker.mark(str(tmp_path / "Фантастика" / "книга.fb2.zip"), is_directory=False)
    assert tracker.take_ready(0) == {str(tmp_path / "Фантастика")}
