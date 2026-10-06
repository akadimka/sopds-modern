"""Поиск и навигация в Telegram-боте (sopds_web_backend/telegram_search.py, этап B)."""
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

import sopds_web_backend.telegram_users as tu
from fb2parser_core.autosync_service import AutosyncJournal
from opds_catalog.models import (
    Author,
    Book,
    Catalog,
    Genre,
    Series,
    bauthor,
    bgenre,
    bseries,
)
from sopds_web_backend import telegram_search as ts
from sopds_web_backend.telegram_library import LibraryHandler

pytestmark = pytest.mark.django_db


def _author(name):
    return Author.objects.create(full_name=name, search_full_name=name.upper())


def _book(cat, title, authors, series=None, no=0, avail=2, annotation=""):
    b = Book.objects.create(filename=f"{title}.fb2", path=".", filesize=1, format="fb2", cat_type=0,
                            docdate="2020", lang="ru", title=title, search_title=title.upper(),
                            annotation=annotation, avail=avail, catalog=cat)
    for a in authors:
        bauthor.objects.create(book=b, author=a)
    if series:
        bseries.objects.create(book=b, ser=series, ser_no=no)
    return b


@pytest.fixture
def library():
    cat = Catalog.objects.create(parent=None, cat_name=".", path=".", cat_type=0)
    luk = _author("Лукьяненко Сергей")
    other = _author("Петров Пётр")
    dozory = Series.objects.create(ser="Дозоры", search_ser="ДОЗОРЫ")
    books = {
        "night": _book(cat, "Ночной Дозор", [luk], dozory, 1, annotation="Иные и <тени>."),
        "day": _book(cat, "Дневной Дозор", [luk], dozory, 2),
        "spectr": _book(cat, "Спектр", [luk]),
        "watch": _book(cat, "Дозор у моря", [other]),
        "deleted": _book(cat, "Последний Дозор", [luk], dozory, 3, avail=0),
    }
    genre = Genre.objects.create(genre="sf", section="Фантастика", subsection="Фантастика")
    bgenre.objects.create(book=books["night"], genre=genre)
    return SimpleNamespace(luk=luk, other=other, dozory=dozory, **books)


def _labels(screen):
    return [b["text"] for row in screen[1]["inline_keyboard"] for b in row]


def test_words_any_order_and_title_required(library):
    found = ts.search("сергей лукьяненко")
    assert [k for k, _i, _l in found] == ["a"]  # автор, а не простыня из всех его книг

    found = ts.search("Лукьяненко дозор")
    books = {i for k, i, _l in found if k == "b"}
    assert books == {library.night.id, library.day.id}  # не «Дозор у моря», не удалённый


def test_series_and_author_counts_skip_deleted_books(library):
    labels = [label for _k, _i, label in ts.search("дозор")]
    assert any(label.startswith("📚 Дозоры (2)") for label in labels)
    labels = [label for _k, _i, label in ts.search("лукьяненко")]
    assert labels == ["👤 Лукьяненко Сергей (3)"]


def test_results_paging_and_back_navigation(library, settings):
    for i in range(12):
        _book(Catalog.objects.first(), f"Книга {i:02d}", [library.other])
    qid = ts.store_query("книга")
    first = ts.results_screen(qid, 0)
    assert "Книги: 12" in first[0] and len(first[1]["inline_keyboard"]) == 11  # 10 + навигация
    nav = first[1]["inline_keyboard"][-1]
    assert [b["text"] for b in nav] == ["1/2", "▶"]
    second = ts.screen_for(nav[-1]["callback_data"])
    assert len(second[1]["inline_keyboard"]) == 3  # 2 книги + навигация


def test_author_series_and_book_screens(library):
    qid = ts.store_query("лукьяненко")
    author = ts.screen_for(f"a:{library.luk.id}:0:{qid}")
    assert "книг: 3" in author[0]
    labels = _labels(author)
    assert labels[0] == "📚 Дозоры (2)" and "🔍 К результатам" in labels

    series = ts.screen_for(f"s:{library.dozory.id}:0:{qid}")
    assert _labels(series)[:2] == ["📖 1. Ночной Дозор", "📖 2. Дневной Дозор"]

    card = ts.screen_for(f"b:{library.night.id}:{qid}")
    assert "<b>Ночной Дозор</b>" in card[0] and "Дозоры, № 1" in card[0] and "Фантастика" in card[0]
    assert "&lt;тени&gt;" in card[0]
    assert {"👤 Автор", "📚 Серия", "🔍 К результатам"} <= set(_labels(card))


def test_deleted_or_unknown_is_none(library):
    assert ts.screen_for(f"b:{library.deleted.id}:") is None
    assert ts.screen_for("q:nope:0") is None
    assert ts.screen_for("b:x") is None


# ---------- через обработчик бота ----------

class FakeClient:
    def __init__(self):
        self.sent, self.edits, self.answers = [], [], []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))

    def edit_message(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append((chat_id, message_id, text, reply_markup))

    def answer_callback(self, callback_id, text=""):
        self.answers.append(text)

    def call(self, method, **params):
        return {"invite_link": "https://t.me/+X"}


def _linked(tid=777):
    user = User.objects.create_user(f"u{tid}", password="Kx7#vQ2!mLp9")
    tu.link_by_code(tu.new_link_code(user).code, {"id": tid, "first_name": "Ч"})
    return user


def test_linked_reader_searches_and_navigates(library, tmp_path):
    _linked()
    client = FakeClient()
    service = SimpleNamespace(cfg={"telegram_channel": ""}, journal=AutosyncJournal(tmp_path / "j.db"))
    handler = LibraryHandler()

    handler(service, client, {"message": {"chat": {"id": 777, "type": "private"}, "from": {"id": 777},
                                          "text": "лукьяненко дозор"}})
    text, markup = client.sent[-1][1], client.sent[-1][2]
    assert "Книги: 2" in text
    data = markup["inline_keyboard"][0][0]["callback_data"]

    handled = handler(service, client, {"callback_query": {
        "id": "c1", "from": {"id": 777}, "data": data,
        "message": {"message_id": 5, "chat": {"id": 777}}}})
    assert handled and client.edits[-1][1] == 5 and "📖" in client.edits[-1][2]


def test_stranger_cannot_navigate(library, tmp_path):
    client = FakeClient()
    service = SimpleNamespace(cfg={}, journal=AutosyncJournal(tmp_path / "j.db"))
    LibraryHandler()(service, client, {"callback_query": {
        "id": "c1", "from": {"id": 5}, "data": f"b:{library.night.id}:", "message": {}}})
    assert "Нет доступа" in client.answers[-1] and not client.edits


def test_yo_and_ye_are_the_same(library):
    _book(Catalog.objects.first(), "Ёжик в тумане", [library.other])
    assert any(k == "b" for k, _i, _l in ts.search("ежик"))
    assert any(k == "b" for k, _i, _l in ts.search("ЁЖИК"))
