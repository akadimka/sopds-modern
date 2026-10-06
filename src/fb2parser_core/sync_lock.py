"""Блокировка синхронизации библиотеки между процессами.

Ручная синхронизация идёт в процессе веб-сервера, автосинхронизация — в
процессе планового скана (docs/watch-folder-autosync-design.md). Обе
перемещают файлы в одну библиотеку, поэтому одновременно работать им
нельзя. Блокировка — файл, созданный атомарно (O_EXCL); он хранит, кто и
когда её взял. Брошенный упавшим процессом файл считается устаревшим, если
процесса больше нет (POSIX) или он старше stale_after.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

_log = logging.getLogger(__name__)

DEFAULT_STALE_AFTER = 6 * 3600


class SyncBusy(RuntimeError):
    """Синхронизацию уже выполняет кто-то другой (owner — кто именно)."""

    def __init__(self, owner: str):
        super().__init__(owner)
        self.owner = owner


def _read(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":
        # os.kill(pid, 0) на Windows не проверяет, а завершает процесс.
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _is_stale(path: Path, info: dict, stale_after: float) -> bool:
    since = info.get("since")
    if since is None:
        try:
            since = path.stat().st_mtime
        except OSError:
            return True
    if time.time() - float(since) > stale_after:
        return True
    pid = info.get("pid")
    return isinstance(pid, int) and not _pid_alive(pid)


@contextmanager
def sync_lock(lock_path, owner: str, stale_after: float = DEFAULT_STALE_AFTER):
    path = Path(lock_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in (1, 2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            info = _read(path)
            if attempt == 1 and _is_stale(path, info, stale_after):
                _log.warning("sync_lock: снимаю устаревшую блокировку %s", info or path)
                path.unlink(missing_ok=True)
                continue
            raise SyncBusy(str(info.get("owner", "?"))) from None
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"owner": owner, "pid": os.getpid(), "since": time.time()}, f)
        break
    try:
        yield
    finally:
        path.unlink(missing_ok=True)
