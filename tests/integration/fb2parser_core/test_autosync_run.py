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
from types import SimpleNamespace

import pytest

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


# ---------- «Входящие» (этап 3) ----------

def test_inbox_lists_doubtful_book_and_decision_syncs_it(tmp_path, monkeypatch):
    svc, lib, watch, sure, doubtful, _compiled = _setup(tmp_path, monkeypatch, MODE_AUTO)
    svc.run()

    batches = svc.inbox()
    assert [b.name for b in batches] == ["Чужая порция"]
    unit = batches[0].units[0]
    assert unit.files == [str(doubtful.relative_to(watch))] and unit.blocker

    result = svc.decide("Чужая порция", {unit.uid: "Детектив"})

    assert result.status == RUN_DONE, result.error
    assert len(result.moved) == 1 and not doubtful.exists()
    moved = lib / result.moved[0]["library_path"]
    assert moved.relative_to(lib).parts[0] == "Детектив" and _genre_of(moved) == "Детектив"
    assert svc.inbox() == []
    with sqlite3.connect(svc.memory.db_path) as conn:
        assert ("unknown_code", "Чужая порция", "user") in conn.execute(
            "SELECT codes, batch, decided_by FROM orig_codes").fetchall()


def test_decision_keeps_unchosen_books_of_the_batch(tmp_path, monkeypatch):
    svc, lib, watch, sure, doubtful, _compiled = _setup(tmp_path, monkeypatch, MODE_AUTO)
    second = _book(watch / "Чужая порция" / "Сидоров Сидор - Другое.fb2", "unknown_code", "Сидор", "Сидоров",
                   "Другое", 1)
    svc.run()
    units = {u.files[0]: u for u in svc.inbox()[0].units}
    chosen = units[str(doubtful.relative_to(watch))]

    svc.decide("Чужая порция", {chosen.uid: "Детектив"})

    assert not doubtful.exists() and second.exists()
    assert [u.files for b in svc.inbox() for u in b.units] == [[str(second.relative_to(watch))]]


def test_decision_for_unknown_batch_is_an_error(tmp_path, monkeypatch):
    svc, *_rest = _setup(tmp_path, monkeypatch, MODE_AUTO)
    assert svc.decide("Нет такой", {"x": "Детектив"}).status == "error"


@pytest.mark.django_db
def test_inbox_page_and_decide_view(tmp_path, monkeypatch, client, settings):
    from django.contrib.auth.models import User
    from django.urls import reverse

    import fb2parser_web.fb2parser_bridge as bridge
    import fb2parser_web.views as views

    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    svc, lib, watch, sure, doubtful, _compiled = _setup(tmp_path, monkeypatch, MODE_AUTO)
    svc.run()
    monkeypatch.setattr(bridge, "get_autosync_service", lambda: svc)
    client.force_login(User.objects.create_user("staff", password="Kx7#vQ2!mLp9", is_staff=True))

    page = client.get(reverse("fb2parser:inbox")).content.decode("utf-8")
    unit = svc.inbox()[0].units[0]
    assert "Чужая порция" in page and f'name="unit_{unit.uid}"' in page

    empty = client.post(reverse("fb2parser:inbox_decide"), {"batch": "Чужая порция"}).content.decode("utf-8")
    assert "callout alert" in empty and doubtful.exists()

    # фоновую задачу выполняем синхронно — проверяем её связку с ядром
    class _SyncThread:
        def __init__(self, target, args=(), daemon=None):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr(views, "threading", SimpleNamespace(Thread=_SyncThread))
    client.post(reverse("fb2parser:inbox_decide"), {"batch": "Чужая порция", f"unit_{unit.uid}": "Детектив"})
    assert not doubtful.exists()
    assert views.inbox_job.get()["moved"] == 1


# ---------- Telegram (этап 4) ----------

class _FakeTelegram:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    def send_message(self, chat, text):
        if self.fail:
            from fb2parser_core.telegram_notify import TelegramError
            raise TelegramError("network: timed out")
        self.sent.append((chat, text))


def _with_telegram(svc):
    svc.cfg.update(telegram_token="1:A", telegram_channel="@news", telegram_admin_chat="42",
                   public_url="https://books.example.org")
    return svc


def test_notify_digest_once_and_admin_only_on_changes(tmp_path, monkeypatch):
    from fb2parser_core.autosync_service import notify
    svc, *_rest = _setup(tmp_path, monkeypatch, MODE_AUTO)
    _with_telegram(svc)
    tg = _FakeTelegram()

    sent = notify(svc, svc.run(), client=tg)

    assert sent == {"channel": "ok", "admin": "ok"}
    channel = [t for c, t in tg.sent if c == "@news"]
    admin = [t for c, t in tg.sent if c == "42"]
    assert len(channel) == 1 and "Иванов Иван" in channel[0] and "Звезды" in channel[0]
    assert "Ждут решения: 1" in admin[0] and "https://books.example.org/fb2parser/inbox/" in admin[0]

    tg.sent.clear()
    assert notify(svc, svc.run(), client=tg) == {}  # ничего нового — тишина
    assert tg.sent == []


def test_notify_announces_inbox_decisions_in_next_digest(tmp_path, monkeypatch):
    from fb2parser_core.autosync_service import notify
    svc, *_rest = _setup(tmp_path, monkeypatch, MODE_AUTO)
    _with_telegram(svc)
    notify(svc, svc.run(), client=_FakeTelegram())
    unit = svc.inbox()[0].units[0]
    svc.decide("Чужая порция", {unit.uid: "Детектив"})

    tg = _FakeTelegram()
    notify(svc, svc.run(), client=tg)

    channel = [t for c, t in tg.sent if c == "@news"]
    assert len(channel) == 1 and "Детектив" in channel[0] and "Петров" in channel[0]
    assert "Иванов Иван" not in channel[0]  # уже объявлено в прошлый раз


def test_telegram_failure_does_not_break_and_retries_later(tmp_path, monkeypatch):
    from fb2parser_core.autosync_service import notify
    svc, *_rest = _setup(tmp_path, monkeypatch, MODE_AUTO)
    _with_telegram(svc)

    sent = notify(svc, svc.run(), client=_FakeTelegram(fail=True))
    assert "нет связи" in sent["channel"]

    tg = _FakeTelegram()
    notify(svc, svc.run(), client=tg)
    assert any(c == "@news" and "Иванов Иван" in t for c, t in tg.sent)


def test_notify_does_nothing_without_token(tmp_path, monkeypatch):
    from fb2parser_core.autosync_service import notify
    svc, *_rest = _setup(tmp_path, monkeypatch, MODE_AUTO)
    assert notify(svc, svc.run(), client=_FakeTelegram()) == {}
