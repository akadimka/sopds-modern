"""Новинки каталога в канал без автосинхронизации — docs/quality-roadmap.md, баг №179.

Сервер-2 получает книги ночным зеркалированием, автосинхронизация там
выключена — её журнал пуст, и канал новинок молчал. Теперь после скана
объявляются книги, добавленные в каталог с прошлого объявления, — только
открытых всем библиотек (канал общий).
"""
from types import SimpleNamespace

import pytest

import opds_catalog.autosync_hooks as hooks
from fb2parser_core import telegram_notify
from opds_catalog.models import Book
from sopds_web_backend.models import UserProfile

pytestmark = pytest.mark.django_db

CFG = {"telegram_token": "1:x", "telegram_channel": "-100", "public_url": ""}


class FakeJournal:
    def __init__(self, state=None):
        self.state = dict(state or {})

    def get_state(self, key):
        return self.state.get(key)

    def set_state(self, key, value):
        self.state[key] = value


class FakeClient:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_message(self, chat_id, text, reply_markup=None):
        if self.fail:
            raise telegram_notify.TelegramError("Bad Gateway", 502)
        self.sent.append((chat_id, text))


@pytest.fixture
def setup(lib, monkeypatch):
    users, books, _ = lib
    client = FakeClient()
    monkeypatch.setattr(telegram_notify, "client_for", lambda cfg: client)
    monkeypatch.setattr(hooks, "_bot_username", lambda service: "")
    UserProfile.objects.filter(user=users["anna"]).update(library_public=True)   # Борис — закрытая
    return users, books, client


def _service(journal, cfg=CFG):
    return SimpleNamespace(cfg=dict(cfg), journal=journal)


OFF = SimpleNamespace(status="off")


def test_first_run_only_sets_starting_point(setup):
    _users, _books, client = setup
    journal = FakeJournal()
    assert hooks.announce_catalog_additions(_service(journal), OFF) == {}
    assert client.sent == []
    assert journal.state[hooks.CATALOG_MARK_KEY] == str(max(Book.objects.values_list("id", flat=True)))


def test_new_public_books_announced_once(setup):
    _users, books, client = setup
    mark = min(b.id for b in books.values()) - 1
    journal = FakeJournal({hooks.CATALOG_MARK_KEY: str(mark)})
    assert hooks.announce_catalog_additions(_service(journal), OFF) == {"catalog": "ok"}
    assert len(client.sent) == 1
    text = client.sent[0][1]
    assert "Новые книги в библиотеке: 1" in text and "Автор Анны" in text and "Бориса" not in text
    assert hooks.announce_catalog_additions(_service(journal), OFF) == {}   # уже объявлено
    assert len(client.sent) == 1


def test_autosync_on_or_no_channel_announces_nothing(setup):
    _users, books, client = setup
    journal = FakeJournal({hooks.CATALOG_MARK_KEY: "0"})
    assert hooks.announce_catalog_additions(_service(journal), SimpleNamespace(status="ok")) == {}
    assert hooks.announce_catalog_additions(_service(journal, {**CFG, "telegram_channel": ""}), OFF) == {}
    assert client.sent == [] and journal.state[hooks.CATALOG_MARK_KEY] == "0"


def test_telegram_failure_keeps_mark(setup, monkeypatch):
    _users, _books, _client = setup
    monkeypatch.setattr(telegram_notify, "client_for", lambda cfg: FakeClient(fail=True))
    journal = FakeJournal({hooks.CATALOG_MARK_KEY: "0"})
    sent = hooks.announce_catalog_additions(_service(journal), OFF)
    assert "catalog" in sent and sent["catalog"] != "ok"
    assert journal.state[hooks.CATALOG_MARK_KEY] == "0"


def test_many_books_genre_counts(setup, monkeypatch):
    _users, _books, client = setup
    monkeypatch.setattr(hooks, "CATALOG_DIGEST_MAX_BOOKS", 0)
    journal = FakeJournal({hooks.CATALOG_MARK_KEY: "0"})
    hooks.announce_catalog_additions(_service(journal), OFF)
    assert "Новые книги в библиотеке: 1" in client.sent[0][1] and "• .: 1" in client.sent[0][1]


def test_notify_autosync_includes_catalog_announcement(setup, monkeypatch):
    _users, _books, client = setup
    import fb2parser_core.autosync_service as svc_mod
    monkeypatch.setattr(svc_mod, "notify", lambda *a, **k: {})
    journal = FakeJournal({hooks.CATALOG_MARK_KEY: "0"})
    assert hooks.notify_autosync((_service(journal), OFF)) == {"catalog": "ok"}
