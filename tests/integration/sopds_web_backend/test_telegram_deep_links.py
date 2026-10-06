"""Переходы из сводки новинок канала в бота (этап D, docs/telegram-library-bot.md):
t.me/<бот>?start=s<id серии> / b<id книги> открывают у привязанного
читателя серию или книгу с кнопками скачивания."""
import os
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

import sopds_web_backend.telegram_users as tu
from fb2parser_core.autosync_service import AutosyncJournal
from fb2parser_core.telegram_notify import build_digest
from opds_catalog import autosync_hooks as hooks
from opds_catalog.models import Author, Book, Catalog, Series, bauthor, bseries
from sopds_web_backend.telegram_library import INTRO, LibraryHandler

pytestmark = pytest.mark.django_db


def _book(cat, title, author, path, series=None):
    b = Book.objects.create(filename=f"{title}.fb2", path=path, filesize=1, format="fb2", cat_type=0, docdate="2020",
                            lang="ru", title=title, search_title=title.upper(), annotation="", avail=2, catalog=cat)
    bauthor.objects.create(book=b, author=author)
    if series:
        bseries.objects.create(book=b, ser=series, ser_no=1)
    return b


@pytest.fixture
def lib():
    cat = Catalog.objects.create(parent=None, cat_name=".", path=".", cat_type=0)
    sapfir = Author.objects.create(full_name="Сапфир Олег", search_full_name="САПФИР ОЛЕГ")
    other = Author.objects.create(full_name="Другой Автор", search_full_name="ДРУГОЙ АВТОР")
    codex = Series.objects.create(ser="Кодекс", search_ser="КОДЕКС")
    codex_other = Series.objects.create(ser="Кодекс", search_ser="КОДЕКС")  # одноимённая у другого автора
    folder = os.path.join("Фантастика", "Сапфир Олег", "Кодекс")
    return SimpleNamespace(
        codex=codex, codex_other=codex_other, folder=folder,
        vol=_book(cat, "Кодекс 1", sapfir, folder, codex),
        other_vol=_book(cat, "Чужой Кодекс", other, os.path.join("Фантастика", "Другой Автор"), codex_other),
        single=_book(cat, "Одиночка", sapfir, os.path.join("Фантастика", "Сапфир Олег")),
    )


def _service(tmp_path, token="1:A", bot="lib_bot", public_url="", db="j.db"):
    journal = AutosyncJournal(tmp_path / db)
    if bot:
        journal.set_state("tg_bot_username", bot)
    return SimpleNamespace(cfg={"telegram_token": token, "public_url": public_url}, journal=journal)


def test_digest_links_go_to_the_bot(lib, tmp_path):
    link = hooks.catalog_link(_service(tmp_path))
    by_path = link({"kind": "series", "series": "Кодекс", "author": "Сапфир Олег",
                    "path": os.path.join(lib.folder, "Кодекс 1.fb2")})
    assert by_path == f"https://t.me/lib_bot?start=s{lib.codex.id}"
    # автокомпиляция могла собрать тома в другой файл — тогда по имени и автору, не путая одноимённые
    by_name = link({"kind": "series", "series": "Кодекс", "author": "Другой Автор", "path": "нет/такого.fb2"})
    assert by_name == f"https://t.me/lib_bot?start=s{lib.codex_other.id}"
    single = link({"kind": "book", "title": "Одиночка", "author": "Сапфир Олег", "path": ""})
    assert single == f"https://t.me/lib_bot?start=b{lib.single.id}"
    assert link({"kind": "book", "title": "Нет такой", "author": "", "path": ""}) is None


def test_site_links_without_bot_and_none_without_both(lib, tmp_path):
    web = hooks.catalog_link(_service(tmp_path, token="", bot="", public_url="https://books.example.org"))
    url = web({"kind": "series", "series": "Кодекс", "author": "Сапфир Олег", "path": ""})
    assert url.startswith("https://books.example.org/web/search/books/?searchtype=s")
    assert hooks.catalog_link(_service(tmp_path, token="", bot="", db="none.db")) is None


def test_digest_passes_library_path_to_link():
    seen = []

    def link(item):
        seen.append(item)

    build_digest([{"genre": "Ф", "author": "А", "series": "", "title": "Т", "library_path": "Ф/А/Т.fb2"}], link=link)
    assert seen[0]["path"] == "Ф/А/Т.fb2"


class FakeClient:
    def __init__(self):
        self.sent = []

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((text, reply_markup))


def _start(payload, tid=777):
    return {"message": {"chat": {"id": tid, "type": "private"}, "from": {"id": tid}, "text": f"/start {payload}"}}


def test_bot_opens_series_and_book_from_channel_links(lib, tmp_path):
    user = User.objects.create_user("reader", password="Kx7#vQ2!mLp9")
    tu.link_by_code(tu.new_link_code(user).code, {"id": 777, "first_name": "Ч"})
    client, handler = FakeClient(), LibraryHandler()
    service = _service(tmp_path)

    handler(service, client, _start(f"s{lib.codex.id}"))
    assert "<b>Кодекс</b>" in client.sent[-1][0]

    handler(service, client, _start(f"b{lib.single.id}"))
    text, markup = client.sent[-1]
    assert "<b>Одиночка</b>" in text
    assert markup["inline_keyboard"][0][0]["callback_data"] == f"f:{lib.single.id}:fb2"

    handler(service, client, _start("b999999"))
    assert "Не нашлось" in client.sent[-1][0]


def test_stranger_following_channel_link_gets_intro(lib, tmp_path):
    client = FakeClient()
    LibraryHandler()(_service(tmp_path), client, _start(f"s{lib.codex.id}", tid=5))
    assert client.sent[-1][0] == INTRO
