"""Скачивание книг в Telegram-боте (этап C): те же форматы и тот же код
подготовки файла, что на сайте (opds_catalog.dl), кеш file_id."""
import io
import json
import os
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User

import fb2parser_core.telegram_notify as tn
import opds_catalog.dl as dl
import sopds_web_backend.telegram_users as tu
from opds_catalog.models import Author, Book, Catalog, bauthor, bookshelf
from sopds_web_backend import telegram_search as ts
from sopds_web_backend.models import TelegramFile
from sopds_web_backend.telegram_library import LibraryHandler

pytestmark = pytest.mark.django_db


@pytest.fixture
def converters(monkeypatch, tmp_path):
    cfg = SimpleNamespace(SOPDS_FB2TOEPUB="/usr/bin/ebook-convert", SOPDS_FB2TOMOBI="/usr/bin/ebook-convert",
                          SOPDS_FB2TOAZW3="", SOPDS_TEMP_DIR=str(tmp_path), SOPDS_TITLE_AS_FILENAME=False,
                          SOPDS_ROOT_LIB=str(tmp_path))
    monkeypatch.setattr(dl, "config", cfg)
    return cfg


@pytest.fixture
def book():
    cat = Catalog.objects.create(parent=None, cat_name=".", path=".", cat_type=0)
    b = Book.objects.create(filename="night.fb2", path=".", filesize=1234, format="fb2", cat_type=0,
                            docdate="2020", lang="ru", title="Ночной Дозор", search_title="НОЧНОЙ ДОЗОР",
                            annotation="", avail=2, catalog=cat)
    bauthor.objects.create(book=b, author=Author.objects.create(full_name="Лукьяненко Сергей",
                                                               search_full_name="ЛУКЬЯНЕНКО СЕРГЕЙ"))
    return b


# ---------- форматы и конвертация ----------

def test_available_formats_follow_site_rules(book, converters):
    assert dl.available_formats(book) == ["fb2", "epub", "mobi"]  # AZW3 не настроен
    converters.SOPDS_TEMP_DIR = ""
    assert dl.available_formats(book) == ["fb2"]  # без временной папки сайт тоже не конвертирует
    book.format = "pdf"
    assert dl.available_formats(book) == ["pdf"]


def test_book_card_has_format_buttons(book, converters):
    card = ts.book_screen(book.id)
    first_row = [b["callback_data"] for b in card[1]["inline_keyboard"][0]]
    assert first_row == [f"f:{book.id}:fb2", f"f:{book.id}:epub", f"f:{book.id}:mobi"]


class _FakePopen:
    produce = True

    def __init__(self, args, stdout=None):
        self.args = args
        if _FakePopen.produce:
            with open(args[2], "wb") as f:
                f.write(b"EPUB-DATA")

    def communicate(self, timeout=None):
        return b"", b""


def test_convert_book_returns_file_and_cleans_temp(book, converters, monkeypatch, tmp_path):
    (tmp_path / "night.fb2").write_bytes(b"<FictionBook/>")
    monkeypatch.setattr(dl, "get_fs_book_path", lambda b: str(tmp_path))
    monkeypatch.setattr(dl.subprocess, "Popen", _FakePopen)

    name, data = dl.convert_book(book, "epub")
    assert (name, data) == ("night.epub", b"EPUB-DATA")
    assert not (tmp_path / "night.epub").exists()

    _FakePopen.produce = False
    try:
        with pytest.raises(dl.ConversionFailed):
            dl.convert_book(book, "epub")
    finally:
        _FakePopen.produce = True
    with pytest.raises(dl.ConversionUnavailable):
        dl.convert_book(book, "azw3")


# ---------- отправка multipart ----------

def test_send_document_multipart():
    seen = {}

    def opener(req, timeout=None):
        seen["ctype"] = req.headers["Content-type"]
        seen["body"] = req.data
        return io.BytesIO(json.dumps({"ok": True, "result": {"document": {"file_id": "F1"}}}).encode())

    res = tn.TelegramClient("1:A", opener=opener).send_document(42, 'Ночной "Дозор".fb2', b"\x00BOOK", "cap")
    assert res["document"]["file_id"] == "F1"
    assert seen["ctype"].startswith("multipart/form-data; boundary=")
    body = seen["body"]
    assert b'name="chat_id"\r\n\r\n42\r\n' in body and b"\x00BOOK" in body
    assert "filename=\"Ночной 'Дозор'.fb2\"".encode() in body


# ---------- доставка в боте ----------

class FakeClient:
    def __init__(self, bad_file_id=False):
        self.docs, self.by_id, self.sent, self.answers, self.calls = [], [], [], [], []
        self.bad_file_id = bad_file_id

    def send_document(self, chat_id, filename, data, caption=""):
        self.docs.append((chat_id, filename, data, caption))
        return {"document": {"file_id": f"FID{len(self.docs)}"}}

    def send_document_id(self, chat_id, file_id, caption=""):
        if self.bad_file_id:
            raise tn.TelegramError("Bad Request: wrong file identifier", 400)
        self.by_id.append((chat_id, file_id))

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append(text)

    def answer_callback(self, callback_id, text=""):
        self.answers.append(text)

    def call(self, method, **params):
        self.calls.append(method)


class _Sync:
    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture
def reader():
    user = User.objects.create_user("reader", password="Kx7#vQ2!mLp9")
    tu.link_by_code(tu.new_link_code(user).code, {"id": 777, "first_name": "Ч"})
    return user


def _press(handler, client, data, tid=777):
    handler(None, client, {"callback_query": {"id": "c", "from": {"id": tid}, "data": data,
                                              "message": {"message_id": 1, "chat": {"id": tid}}}})


def test_fb2_upload_then_cached_by_file_id(book, converters, reader, monkeypatch):
    monkeypatch.setattr(dl, "original_file", lambda b: ("night.fb2", b"FB2"))
    client, handler = FakeClient(), LibraryHandler(executor=_Sync())

    _press(handler, client, f"f:{book.id}:fb2")
    assert client.docs[0][:3] == (777, "night.fb2", b"FB2") and "Ночной Дозор" in client.docs[0][3]
    assert TelegramFile.objects.get(book=book, fmt="fb2").file_id == "FID1"
    assert bookshelf.objects.filter(user=reader, book=book).exists()

    _press(handler, client, f"f:{book.id}:fb2")
    assert client.by_id == [(777, "FID1")] and len(client.docs) == 1


def test_stale_file_id_is_reuploaded(book, converters, reader, monkeypatch):
    monkeypatch.setattr(dl, "original_file", lambda b: ("night.fb2", b"FB2"))
    TelegramFile.objects.create(book=book, fmt="fb2", filesize=book.filesize, file_id="OLD")
    client = FakeClient(bad_file_id=True)
    _press(LibraryHandler(executor=_Sync()), client, f"f:{book.id}:fb2")
    assert len(client.docs) == 1 and TelegramFile.objects.get(book=book, fmt="fb2").file_id == "FID1"


def test_epub_is_converted(book, converters, reader, monkeypatch):
    monkeypatch.setattr(dl, "convert_book", lambda b, fmt: ("night.epub", b"EPUB"))
    client = FakeClient()
    _press(LibraryHandler(executor=_Sync()), client, f"f:{book.id}:epub")
    assert client.docs[0][1:3] == ("night.epub", b"EPUB") and "sendChatAction" in client.calls


def test_too_big_and_failed_conversion(book, converters, reader, monkeypatch):
    monkeypatch.setattr(tn, "MAX_UPLOAD", 3)
    monkeypatch.setattr(dl, "original_file", lambda b: ("night.fb2", b"FB2-TOO-BIG"))

    def fail(b, fmt):
        raise dl.ConversionFailed("boom")

    monkeypatch.setattr(dl, "convert_book", fail)
    client, handler = FakeClient(), LibraryHandler(executor=_Sync())
    _press(handler, client, f"f:{book.id}:fb2")
    _press(handler, client, f"f:{book.id}:epub")
    _press(handler, client, f"f:{book.id}:azw3")  # не настроен
    assert not client.docs
    assert "слишком большой" in client.sent[0] and "Не удалось подготовить EPUB" in client.sent[1]
    assert "недоступны" in client.sent[2]


def test_stranger_cannot_download(book, converters):
    client = FakeClient()
    _press(LibraryHandler(executor=_Sync()), client, f"f:{book.id}:fb2", tid=5)
    assert "Нет доступа" in client.answers[0] and not client.docs


def test_original_file_reads_like_download(book, converters, monkeypatch):
    monkeypatch.setattr(dl, "getFileData", lambda b: io.BytesIO(b"DATA"))
    assert dl.original_file(book) == ("night.fb2", b"DATA")
    monkeypatch.setattr(dl, "getFileData", lambda b: None)
    with pytest.raises(dl.ConversionFailed):
        dl.original_file(book)
    assert os.path.basename(dl.converted_filename(book, "mobi")) == "night.mobi"
