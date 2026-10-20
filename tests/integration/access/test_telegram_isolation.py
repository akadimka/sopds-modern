"""Изоляция библиотек в Telegram-боте (docs/multiuser-plan.md шаг 1.8): поиск,
экраны автора/серии/книги, выдача файла и ссылки /start b<id>/s<id> — только
книги, видимые привязанному пользователю; в общий канал новинок — только
книги открытых всем библиотек."""
import pytest

import sopds_web_backend.telegram_users as tu
from opds_catalog.autosync_hooks import catalog_link, public_book_filter
from sopds_web_backend import telegram_search as ts
from sopds_web_backend.models import UserProfile
from sopds_web_backend.telegram_library import LibraryHandler

pytestmark = pytest.mark.django_db
ANNA_TG = 777


class FakeClient:
    def __init__(self):
        self.docs, self.sent, self.answers = [], [], []

    def send_document(self, chat_id, filename, data, caption=""):
        self.docs.append(filename)
        return {"document": {"file_id": "F"}}

    def send_document_id(self, chat_id, file_id, caption=""):
        self.docs.append(file_id)

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)

    def answer_callback(self, callback_id, text=""):
        self.answers.append(text)

    def edit_message(self, chat_id, message_id, text, reply_markup=None):
        self.sent.append(text)

    def call(self, method, **params):
        pass


class _Sync:
    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture
def linked(lib):
    users, books, _ = lib
    tu.link_by_code(tu.new_link_code(users["anna"]).code, {"id": ANNA_TG, "first_name": "Анна"})
    return users, books


def _labels(screen):
    return " ".join(b["text"] for row in screen[1]["inline_keyboard"] for b in row) + screen[0]


def test_search_and_screens(linked):
    users, books = linked
    anna, admin, boris_book = users["anna"], users["admin"], books["boris"]
    assert not [i for i in ts.search("СЕКРЕТ", anna)] and ts.search("СЕКРЕТ", admin)
    assert not [i for i in ts.search("АВТОР", anna) if "Бориса" in i[2]]
    author, series = boris_book.authors.get(), boris_book.series.get()
    assert ts.author_screen(author.id, 0, "", anna) is None
    assert ts.series_screen(series.id, 0, "", anna) is None
    assert ts.book_screen(boris_book.id, "", anna) is None
    assert ts.screen_for(f"b:{boris_book.id}:", anna) is None
    assert "Секрет Бориса" in _labels(ts.book_screen(boris_book.id, "", admin))
    assert "Дневник Анны" in _labels(ts.book_screen(books["anna"].id, "", anna))


def _press(client, data):
    LibraryHandler(executor=_Sync())(None, client, {"callback_query": {
        "id": "c", "from": {"id": ANNA_TG}, "data": data, "message": {"message_id": 1, "chat": {"id": ANNA_TG}}}})


def test_foreign_book_is_not_delivered(linked, monkeypatch):
    from opds_catalog import dl
    monkeypatch.setattr(dl, "original_file", lambda b: (b.filename, b"BOOK"))
    _users, books = linked
    client = FakeClient()
    _press(client, f"f:{books['boris'].pk}:fb2")
    assert client.docs == [] and any("недоступны" in t for t in client.sent)
    _press(client, f"b:{books['boris'].pk}:")
    assert client.docs == [] and any("устарело" in a for a in client.answers)


def test_deep_link_to_foreign_book(linked):
    _users, books = linked
    client = FakeClient()
    LibraryHandler()(None, client, {"message": {"chat": {"id": ANNA_TG, "type": "private"},
                                                "from": {"id": ANNA_TG}, "text": f"/start b{books['boris'].pk}"}})
    assert client.sent and "Не нашлось" in client.sent[-1]


def test_channel_digest_only_public_books(linked):
    users, _books = linked
    row = {"library_path": "", "title": "Секрет Бориса", "author": "Автор Бориса"}
    include = public_book_filter()
    assert include(row) is False
    UserProfile.objects.filter(user=users["boris"]).update(library_public=True)
    assert public_book_filter()(row) is True


def test_channel_links_point_only_to_public_books(linked, monkeypatch):
    users, books = linked
    import opds_catalog.autosync_hooks as hooks
    monkeypatch.setattr(hooks, "_bot_username", lambda service: "lib_bot")
    from types import SimpleNamespace
    link = catalog_link(SimpleNamespace(cfg={"public_url": ""}))
    item = {"kind": "book", "title": "Секрет Бориса", "author": "Автор Бориса", "path": ""}
    assert link(item) is None
    UserProfile.objects.filter(user=users["boris"]).update(library_public=True)
    assert link(item) == f"https://t.me/lib_bot?start=b{books['boris'].pk}"
