"""Меню кнопками в Telegram-боте (docs/telegram-library-bot.md, «Меню»).

Раньше бот понимал только текст поиска — пользователю надо было помнить,
что делать. Теперь у привязанного читателя внизу чата кнопки «Поиск»,
«Новинки», «Моя полка», «Помощь», те же действия — в меню команд Telegram.
"""
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

import sopds_web_backend.telegram_users as tu
from fb2parser_core.autosync_service import AutosyncJournal
from opds_catalog.models import Author, Book, Catalog, bauthor, bookshelf
from sopds_web_backend import telegram_library as tl
from sopds_web_backend import telegram_search as ts
from sopds_web_backend.telegram_library import LibraryHandler
from tests.fixtures.fixture_django import publish_books

pytestmark = pytest.mark.django_db
TG = 777


class FakeClient:
    def __init__(self):
        self.sent, self.edits, self.answers, self.calls = [], [], [], []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))

    def edit_message(self, chat_id, message_id, text, reply_markup=None):
        self.edits.append((chat_id, message_id, text, reply_markup))

    def answer_callback(self, callback_id, text=""):
        self.answers.append(text)

    def call(self, method, **params):
        self.calls.append((method, params))
        return {}


@pytest.fixture
def books():
    cat = Catalog.objects.create(parent=None, cat_name=".", path=".", cat_type=0)
    author = Author.objects.create(full_name="Лукьяненко Сергей", search_full_name="ЛУКЬЯНЕНКО СЕРГЕЙ")
    result = []
    for i in range(12):
        b = Book.objects.create(filename=f"Книга {i}.fb2", path=".", filesize=1, format="fb2", cat_type=0,
                                docdate="2020", lang="ru", title=f"Книга {i}", search_title=f"КНИГА {i}",
                                annotation="", avail=2, catalog=cat)
        bauthor.objects.create(book=b, author=author)
        publish_books(b)
        result.append(b)
    return result


@pytest.fixture
def reader():
    user = User.objects.create_user("reader", password="Kx7#vQ2!mLp9")
    tu.link_by_code(tu.new_link_code(user).code, {"id": TG, "first_name": "Ч"})
    return user


@pytest.fixture
def bot(tmp_path):
    service = SimpleNamespace(cfg={"telegram_channel": ""}, journal=AutosyncJournal(tmp_path / "j.db"))
    client, handler = FakeClient(), LibraryHandler()

    def send(text, tid=TG):
        handler(service, client, {"message": {"chat": {"id": tid, "type": "private"}, "from": {"id": tid},
                                              "text": text}})
        return client.sent[-1]

    def press(data, tid=TG):
        handler(service, client, {"callback_query": {"id": "c", "from": {"id": tid}, "data": data,
                                                     "message": {"message_id": 5, "chat": {"id": tid}}}})
        return client.edits[-1] if client.edits else None

    return SimpleNamespace(send=send, press=press, client=client, handler=handler)


def _buttons(markup):
    return [b for row in markup.get("inline_keyboard", []) for b in row]


def test_start_shows_menu_keyboard_for_linked_reader(books, reader, bot):
    _chat, text, markup = bot.send("/start")
    assert markup == tl.MAIN_MENU and tl.BTN_NEW in text
    labels = [b["text"] for row in markup["keyboard"] for b in row]
    assert labels == [tl.BTN_SEARCH, tl.BTN_NEW, tl.BTN_SHELF, tl.BTN_HELP]


def test_stranger_gets_intro_without_menu(books, bot):
    _chat, text, markup = bot.send("/start", tid=555)
    assert text == tl.INTRO and markup == tl.NO_MENU
    _chat, text, markup = bot.send(tl.BTN_NEW, tid=555)
    assert text == tl.INTRO   # кнопки и команды — только привязанным


def test_commands_menu_set_once(books, reader, bot):
    bot.send("/start")
    bot.send("/help")
    sets = [c for c in bot.client.calls if c[0] == "setMyCommands"]
    assert len(sets) == 1 and [c["command"] for c in sets[0][1]["commands"]] == [
        "start", "search", "new", "shelf", "help"]


@pytest.mark.parametrize("text", [tl.BTN_SEARCH, "/search", "/search@sopds_library_news_bot"])
def test_search_button_asks_for_query(books, reader, bot, text):
    _chat, reply, markup = bot.send(text)
    assert reply == tl.SEARCH_PROMPT and markup == tl.MAIN_MENU


@pytest.mark.parametrize("text", [tl.BTN_HELP, "/help", "/неизвестно"])
def test_help_and_unknown_command(books, reader, bot, text):
    _chat, reply, markup = bot.send(text)
    assert reply == tl.HELP and markup == tl.MAIN_MENU


def test_new_books_newest_first_paged_with_back(books, reader, bot):
    _chat, text, markup = bot.send(tl.BTN_NEW)
    assert "Новинки" in text
    buttons = _buttons(markup)
    assert buttons[0]["text"].endswith("Книга 11") and buttons[0]["callback_data"] == f"b:{books[11].id}:~n0"
    assert [b["text"] for b in buttons[-2:]] == ["1/2", "▶"]
    _c, _m, page2, markup2 = bot.press("n:1")
    assert _buttons(markup2)[0]["callback_data"] == f"b:{books[1].id}:~n1"
    _c, _m, card, card_markup = bot.press(f"b:{books[1].id}:~n1")
    assert "Книга 1" in card
    assert {"text": "◀ К новинкам", "callback_data": "n:1"} in _buttons(card_markup)


def test_shelf_lists_downloaded_books(books, reader, bot):
    _chat, text, _markup = bot.send(tl.BTN_SHELF)
    assert "пусто" in text
    bookshelf.objects.create(user=reader, book=books[3])
    _chat, text, markup = bot.send("/shelf")
    assert "Моя полка" in text and _buttons(markup)[0]["callback_data"] == f"b:{books[3].id}:~h0"
    _c, _m, _card, card_markup = bot.press(f"b:{books[3].id}:~h0")
    assert {"text": "◀ К моей полке", "callback_data": "h:0"} in _buttons(card_markup)


def test_plain_text_still_searches(books, reader, bot):
    _chat, text, _markup = bot.send("книга 7")
    assert "🔍" in text and "Книги:" in text


def test_list_back_ignores_garbage(books):
    assert ts._back("~x1") == [] and ts._back("~nX") == []
