"""Cross-process background-job state, backed by Django's cache (memcached).

gunicorn runs multiple worker *processes* (see sopds.settings.gunicorn) — a
plain module-level dict is only visible within the process that wrote it, so
a status-poll request routed to a different worker sees a stale/default
state ("not started") even while the job is genuinely still running in
another worker. Every field needs to live somewhere all workers can see —
here, that's the shared cache.

Usage mirrors the old "module-level dict + threading.Lock" pattern closely
to keep call sites simple:

    scan_job = JobState("fb2parser:scan", {"running": False, "processed": 0, ...})

    scan_job.update(processed=scan_job["processed"] + 1, current=name)
    state = scan_job.get()
    if not scan_job.try_start(root=root):
        return already_running_response
    ...
    scan_job.finish()
"""
from django.core.cache import cache

# Comfortably longer than any realistic scan/sync/normalize/compile run.
_TIMEOUT = 6 * 3600


class JobState:
    """Cache-backed replacement for a `dict` + `threading.Lock` pair."""

    def __init__(self, key: str, default: dict):
        self._key = key
        self._default = default

    def get(self) -> dict:
        """Return a copy of the current state (falls back to defaults)."""
        return dict(cache.get(self._key, self._default))

    def __getitem__(self, k):
        return self.get()[k]

    def __setitem__(self, k, v):
        state = self.get()
        state[k] = v
        cache.set(self._key, state, _TIMEOUT)

    def update(self, *args, **kwargs) -> dict:
        """Merge fields into the state, same shape as dict.update()."""
        state = self.get()
        if args:
            state.update(args[0])
        state.update(kwargs)
        cache.set(self._key, state, _TIMEOUT)
        return state

    def try_start(self, **fields) -> bool:
        """Atomically claim the job slot if nothing is already running.

        Uses cache.add() (atomic set-if-absent in memcached) on a separate
        lock key, so two "start" requests hitting two different gunicorn
        workers at the same instant can't both start the job — the old
        in-process threading.Lock never actually protected against that
        either, since each worker had its own Lock.

        On success, resets the state to defaults merged with `fields` (with
        running=True) and returns True. On failure (already running
        somewhere), leaves the state untouched and returns False — the
        caller should render the existing (running) state back to the user.
        """
        if not cache.add(self._lock_key, 1, _TIMEOUT):
            return False
        state = dict(self._default)
        state.update(fields)
        state["running"] = True
        cache.set(self._key, state, _TIMEOUT)
        return True

    def finish(self):
        """Release the start-lock once the job has finished/errored."""
        cache.delete(self._lock_key)

    def reset(self) -> dict:
        """Clear a finished/idle job back to its defaults.

        Call this when re-opening a job's panel/menu so a previous run's
        leftover results (done=True, stats, log) don't flash back up as if
        they belonged to the run about to start. Never call this while the
        job is actually running — that would wipe live progress out from
        under a concurrent status poll.
        """
        state = dict(self._default)
        cache.set(self._key, state, _TIMEOUT)
        return state

    @property
    def _lock_key(self):
        return self._key + ":lock"


class JobFlag:
    """Cache-backed boolean flag — replacement for a `threading.Event` used
    for cross-process cancel/stop signaling (Events are per-process too)."""

    def __init__(self, key: str):
        self._key = key

    def set(self):
        cache.set(self._key, True, _TIMEOUT)

    def clear(self):
        cache.delete(self._key)

    def is_set(self) -> bool:
        return bool(cache.get(self._key, False))


class SharedDict:
    """Cache-backed replacement for a plain shared `dict` (no job/running
    semantics — just key-value data multiple workers must agree on)."""

    def __init__(self, key: str):
        self._key = key

    def get(self) -> dict:
        return dict(cache.get(self._key, {}))

    def set(self, value: dict):
        cache.set(self._key, dict(value), _TIMEOUT)

    def clear(self):
        cache.delete(self._key)
