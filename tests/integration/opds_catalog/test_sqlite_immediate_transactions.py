import sqlite3

import pytest
from django.conf import settings
from django.db import connections, transaction

ALIAS = "sqlite_lock_probe"


@pytest.fixture
def probe_db(tmp_path, django_db_blocker):
    """Файловая WAL-база (как в проде) и отдельное Django-соединение к ней
    с OPTIONS из настроек проекта — тестовая БД in-memory и одна на процесс,
    конкурентную запись на ней не воспроизвести."""
    path = tmp_path / "probe.sqlite3"
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v INTEGER)")
    raw.execute("INSERT INTO t VALUES (1, 0)")
    raw.commit()
    raw.close()

    conf = dict(connections.settings["default"])
    conf.update(NAME=str(path), OPTIONS=dict(settings.SQLITE_OPTIONS))
    connections.settings[ALIAS] = conf
    with django_db_blocker.unblock():
        try:
            yield path
        finally:
            connections[ALIAS].close()
            del connections[ALIAS]
            del connections.settings[ALIAS]


def test_read_then_write_transaction_survives_concurrent_writer(probe_db):
    """Регрессия: фетчеры рейтингов (update_or_create = SELECT, затем
    UPDATE/INSERT в одной транзакции) падали с «database is locked», если
    другой процесс успевал записать между их чтением и записью — SQLite в
    WAL-режиме не может повысить такую отложенную транзакцию до записи и
    отказывает сразу, без ожидания timeout. С transaction_mode=IMMEDIATE
    atomic берёт блокировку записи в начале, и конкурент ждёт его, а не
    наоборот."""
    other = sqlite3.connect(probe_db, timeout=0)
    other_blocked = False
    with transaction.atomic(using=ALIAS):
        cur = connections[ALIAS].cursor()
        cur.execute("SELECT v FROM t WHERE id = 1")
        assert cur.fetchone() == (0,)
        try:
            other.execute("UPDATE t SET v = v + 10 WHERE id = 1")
            other.commit()
        except sqlite3.OperationalError:
            other_blocked = True
        cur.execute("UPDATE t SET v = v + 1 WHERE id = 1")
    other.close()

    assert other_blocked
    check = sqlite3.connect(probe_db)
    assert check.execute("SELECT v FROM t WHERE id = 1").fetchone() == (1,)
    check.close()
