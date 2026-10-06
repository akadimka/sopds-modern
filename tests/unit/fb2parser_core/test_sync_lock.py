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
