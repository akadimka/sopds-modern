"""Уведомления автосинхронизации в Telegram — на подставном Bot API."""
import io
import json
import urllib.error

import pytest

from fb2parser_core.telegram_notify import (
    ERR_NETWORK,
    ERR_RIGHTS,
    ERR_START,
    ERR_TOKEN,
    TelegramClient,
    TelegramError,
    build_admin_report,
    build_digest,
    error_kind,
    split_message,
)


class FakeAPI:
    """Подменяет urllib-opener: запоминает запросы, отвечает заданным JSON."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = list(responses or [])

    def __call__(self, req, timeout=None):
        method = req.full_url.rsplit("/", 1)[1]
        self.calls.append((method, json.loads(req.data.decode("utf-8"))))
        resp = self.responses.pop(0) if self.responses else {"ok": True, "result": True}
        if isinstance(resp, Exception):
            raise resp
        return io.BytesIO(json.dumps(resp).encode("utf-8"))


def test_send_message_posts_html_to_chat():
    api = FakeAPI()
    TelegramClient("123:ABC", opener=api).send_message("@news", "<b>Привет</b>")
    assert api.calls == [("sendMessage", {"chat_id": "@news", "text": "<b>Привет</b>", "parse_mode": "HTML",
                                          "disable_web_page_preview": True})]


def test_api_error_json_is_raised_with_kind():
    body = io.BytesIO(json.dumps({"ok": False, "error_code": 403,
                                  "description": "Forbidden: bot can't initiate conversation with a user"}).encode())
    api = FakeAPI([urllib.error.HTTPError("u", 403, "Forbidden", {}, body)])
    with pytest.raises(TelegramError) as err:
        TelegramClient("123:ABC", opener=api).send_message("42", "x")
    assert error_kind(err.value) == ERR_START


def test_network_error_kind():
    api = FakeAPI([urllib.error.URLError("timed out")])
    with pytest.raises(TelegramError) as err:
        TelegramClient("123:ABC", opener=api).call("getMe")
    assert error_kind(err.value) == ERR_NETWORK


@pytest.mark.parametrize("code, text, kind", [
    (401, "Unauthorized", ERR_TOKEN),
    (400, "Bad Request: need administrator rights in the channel chat", ERR_RIGHTS),
    (403, "Forbidden: bot is not a member of the channel chat", ERR_RIGHTS),
])
def test_error_kinds(code, text, kind):
    assert error_kind(TelegramError(text, code)) == kind


def test_chats_from_updates():
    api = FakeAPI([{"ok": True, "result": [
        {"message": {"chat": {"id": 42, "type": "private", "first_name": "Дмитрий", "username": "dima"}}},
        {"channel_post": {"chat": {"id": -1001, "type": "channel", "title": "Новинки"}}},
        {"message": {"chat": {"id": 42, "type": "private", "first_name": "Дмитрий"}}},
    ]}])
    chats = TelegramClient("123:ABC", opener=api).chats()
    assert [(c["id"], c["type"]) for c in chats] == [("42", "private"), ("-1001", "channel")]
    assert "@dima" in chats[0]["title"]


def test_long_message_is_split_by_lines():
    text = "\n".join(f"строка {i} " + "x" * 50 for i in range(200))
    parts = split_message(text, limit=1000)
    assert all(len(p) <= 1000 for p in parts) and "\n".join(parts) == text


def test_digest_groups_by_genre_author_series_and_escapes():
    rows = [
        {"genre": "Фантастика", "author": "Сапфир Олег", "series": "Кодекс", "title": "Том 1"},
        {"genre": "Фантастика", "author": "Сапфир Олег", "series": "Кодекс", "title": "Том 2"},
        {"genre": "Детектив", "author": "Петров <Пётр>", "series": "", "title": "Тайна & загадка"},
    ]
    links = {"Кодекс": "https://x/s?id=1"}
    text = build_digest(rows, link=lambda item: links.get(item.get("series")))
    assert "Новые книги в библиотеке: 3" in text
    assert text.index("<b>Детектив</b>") < text.index("<b>Фантастика</b>")
    assert '<a href="https://x/s?id=1">«Кодекс» — 2 кн.</a>' in text
    assert "Петров &lt;Пётр&gt;: Тайна &amp; загадка" in text


def test_admin_report_variants():
    dry = build_admin_report({"status": "done", "mode": "dry_run", "would_move": 7}, [{"batch": "П", "books": 3}],
                             "https://x/fb2parser/inbox/")
    assert "пробный режим" in dry and "Было бы перемещено автоматически: 7" in dry
    assert "Ждут решения: 3 кн." in dry and 'href="https://x/fb2parser/inbox/"' in dry
    err = build_admin_report({"status": "error", "error": "диск <полон>"}, [])
    assert "сбой" in err and "диск &lt;полон&gt;" in err
    notes = build_admin_report({"status": "done", "mode": "auto", "moved": 2,
                                "notes": {"reconciliation_notes": [1, 2]}}, [])
    assert "Перемещено: 2" in notes and "сверка автора: 2" in notes


def test_extract_token_from_botfather_message():
    from fb2parser_core.telegram_notify import extract_token
    msg = "Use this token to access the HTTP API:\n7712345678:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw\nKeep it secure"
    assert extract_token(msg) == "7712345678:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    assert extract_token("admin") == ""
