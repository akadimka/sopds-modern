"""Регрессия: фетчер рейтингов, которому нужно записать результат во время
ночного sopds-scan (держит одну транзакцию на весь скан, минуты), ждал
SQLite timeout (30 с), получал «database is locked» и завершался — systemd
поднимал его через минуту. Теперь запись повторяется, пока база занята.
"""
import pytest
from django.db import OperationalError

from opds_catalog import ratings_fetchers
from opds_catalog.ratings_fetchers import save_retrying_while_db_busy


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []

    def fake_sleep(source, seconds, chunk=3.0):
        sleeps.append(seconds)
        return False

    monkeypatch.setattr(ratings_fetchers, "sleep_or_stop", fake_sleep)
    return sleeps


class _FlakySave:
    def __init__(self, failures, exc=None):
        self.failures = failures
        self.exc = exc or OperationalError("database is locked")
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise self.exc


def test_retries_until_database_is_free(no_sleep):
    save = _FlakySave(failures=3)
    log = []
    assert save_retrying_while_db_busy("fantlab", save, log.append) is True
    assert save.calls == 4
    assert no_sleep == [ratings_fetchers.DB_BUSY_RETRY_SECONDS] * 3
    assert len(log) == 3


def test_other_operational_errors_are_not_swallowed(no_sleep):
    save = _FlakySave(failures=1, exc=OperationalError("no such table: x"))
    with pytest.raises(OperationalError, match="no such table"):
        save_retrying_while_db_busy("fantlab", save, lambda msg: None)
    assert no_sleep == []


def test_gives_up_after_max_wait(no_sleep):
    save = _FlakySave(failures=10**6)
    with pytest.raises(OperationalError, match="locked"):
        save_retrying_while_db_busy("fantlab", save, lambda msg: None)
    assert sum(no_sleep) >= ratings_fetchers.DB_BUSY_MAX_WAIT_SECONDS


def test_stop_request_abandons_the_save(monkeypatch):
    monkeypatch.setattr(ratings_fetchers, "sleep_or_stop", lambda *a, **k: True)
    monkeypatch.setattr(ratings_fetchers, "stop_requested", lambda source: True)
    save = _FlakySave(failures=10**6)
    assert save_retrying_while_db_busy("fantlab", save, lambda msg: None) is False
    assert save.calls == 1


# --- каждый фетчер пишет результат через помощник -------------------------

_FETCHERS = [
    ("fetch_fantlab_ratings", "_fetch_rating", (7.5, 10, "u", False)),
    ("fetch_authortoday_ratings", "_fetch_rating", (5, 1, 100, "u", False)),
    ("fetch_litmarket_ratings", "_fetch_rating", (5, 100, "u", False)),
    ("fetch_samlib_ratings", "_fetch_single_rating", (7.5, 10, "u", False)),
]


@pytest.mark.django_db
@pytest.mark.parametrize("module, fetch_method, fetched", _FETCHERS)
def test_fetcher_survives_busy_database_without_refetching(
    module, fetch_method, fetched, monkeypatch, no_sleep, book
):
    import importlib

    cmd_module = importlib.import_module(f"opds_catalog.management.commands.{module}")
    cmd = cmd_module.Command()

    fetch_calls = []

    def fake_fetch(*args, **kwargs):
        fetch_calls.append(args)
        return fetched

    monkeypatch.setattr(cmd, fetch_method, fake_fetch)
    if module == "fetch_samlib_ratings":
        monkeypatch.setattr(cmd, "_is_compilation", lambda b: False)

    real_save = cmd._save_rating
    attempts = []

    def flaky_save(*args, **kwargs):
        attempts.append(1)
        if len(attempts) <= 2:
            raise OperationalError("database is locked")
        return real_save(*args, **kwargs)

    monkeypatch.setattr(cmd, "_save_rating", flaky_save)

    if module == "fetch_samlib_ratings":
        cmd._process_book(book, "series")
    else:
        cmd._process_book(book)

    assert len(fetch_calls) == 1  # сайт не дёргаем повторно
    assert len(attempts) == 3
    assert len(no_sleep) == 2
