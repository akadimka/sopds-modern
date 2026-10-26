"""Личные сводки новинок в Telegram (галочка «Присылать мне новые поступления»
в профиле, по умолчанию выключена; docs/multiuser-plan.md):

- после полного скана каждому включившему — книги, видимые именно ему
  (своя, открытая ему, общая), с id больше его отметки;
- отметка ставится при включении (вся библиотека не приходит) и двигается
  после удачной отправки; не отправилось — остаётся;
- не привязан Telegram или галочка выключена — ничего.
"""
from types import SimpleNamespace

import pytest
from django.test import Client
from django.urls import reverse

import opds_catalog.autosync_hooks as hooks
from fb2parser_core import telegram_notify
from sopds_web_backend import telegram_users
from sopds_web_backend.models import TelegramLink, UserProfile

pytestmark = pytest.mark.django_db
CFG = {"telegram_token": "1:x", "telegram_channel": "", "public_url": ""}


class FakeClient:
    def __init__(self):
        self.sent, self.fail_for = [], set()

    def send_message(self, chat_id, text, reply_markup=None):
        if chat_id in self.fail_for:
            raise telegram_notify.TelegramError("Forbidden: bot was blocked by the user", 403)
        self.sent.append((chat_id, text))


@pytest.fixture
def setup(lib, monkeypatch, settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    users, books, _ = lib
    client = FakeClient()
    monkeypatch.setattr(telegram_notify, "client_for", lambda cfg: client)
    monkeypatch.setattr(hooks, "_bot_username", lambda service: "")
    # Бот настроен — явно, а не токеном из локального config.json: без этого
    # профиль показывал галочку только на машине, где бот настроен.
    monkeypatch.setattr(telegram_users, "autosync_service", _service)
    for n, (name, user) in enumerate(users.items(), start=1):
        TelegramLink.objects.create(user=user, telegram_id=1000 + n, telegram_name=f"@{name}")
    before = min(b.id for b in books.values()) - 1
    return users, books, client, before


def _subscribe(user, mark):
    UserProfile.objects.filter(user=user).update(notify_new_books=True, new_books_mark=mark)


def _service():
    return SimpleNamespace(cfg=dict(CFG), journal=None)


def _chat(user):
    return str(user.telegram.telegram_id)


def test_each_subscriber_gets_only_what_they_can_see(setup):
    users, _books, client, before = setup
    for name in ("anna", "boris", "admin"):
        _subscribe(users[name], before)
    hooks.announce_to_subscribers(_service())
    sent = dict(client.sent)
    assert "Дневник Анны" in sent[_chat(users["anna"])] and "Секрет Бориса" not in sent[_chat(users["anna"])]
    assert "Секрет Бориса" in sent[_chat(users["boris"])] and "Дневник Анны" not in sent[_chat(users["boris"])]
    assert "Дневник Анны" in sent[_chat(users["admin"])] and "Секрет Бориса" in sent[_chat(users["admin"])]


def test_off_by_default_and_needs_telegram(setup):
    users, _books, client, before = setup
    assert not UserProfile.objects.get(user=users["anna"]).notify_new_books        # по умолчанию выключено
    _subscribe(users["boris"], before)
    TelegramLink.objects.filter(user=users["boris"]).update(telegram_id=None)       # отвязал Telegram
    assert hooks.announce_to_subscribers(_service()) == {} and client.sent == []


def test_mark_moves_after_sending_and_nothing_twice(setup):
    users, _books, client, before = setup
    _subscribe(users["anna"], before)
    hooks.announce_to_subscribers(_service())
    hooks.announce_to_subscribers(_service())
    assert len(client.sent) == 1
    assert UserProfile.objects.get(user=users["anna"]).new_books_mark == hooks.latest_book_id()


def test_failed_send_keeps_the_mark(setup):
    users, _books, client, before = setup
    _subscribe(users["anna"], before)
    client.fail_for.add(_chat(users["anna"]))
    assert "failed: anna" in hooks.announce_to_subscribers(_service())["personal"]
    assert UserProfile.objects.get(user=users["anna"]).new_books_mark == before
    client.fail_for.clear()
    hooks.announce_to_subscribers(_service())
    assert [chat for chat, _ in client.sent] == [_chat(users["anna"])]               # пришли со следующей сводкой


def test_without_mark_starts_from_now(setup):
    users, _books, client, _before = setup
    UserProfile.objects.filter(user=users["anna"]).update(notify_new_books=True, new_books_mark=None)
    hooks.announce_to_subscribers(_service())
    assert client.sent == [] and UserProfile.objects.get(user=users["anna"]).new_books_mark == hooks.latest_book_id()


def test_notify_after_scan_includes_personal_digests(setup, monkeypatch):
    users, _books, client, before = setup
    _subscribe(users["anna"], before)
    monkeypatch.setattr("fb2parser_core.autosync_service.notify", lambda *a, **kw: {})
    sent = hooks.notify_autosync((_service(), SimpleNamespace(status="ok")))
    assert sent.get("personal") == "1 sent" and client.sent


def test_profile_checkbox_sets_the_starting_point(setup):
    users, _books, _client, _before = setup
    browser = Client()
    browser.force_login(users["anna"])
    page = browser.get(reverse("web:profile")).content.decode("utf-8")
    assert 'name="notify_new_books"' in page and "Send me new books" in page
    browser.post(reverse("web:profile"), {"action": "tg_notify", "notify_new_books": "on"})
    profile = UserProfile.objects.get(user=users["anna"])
    assert profile.notify_new_books and profile.new_books_mark == hooks.latest_book_id()
    browser.post(reverse("web:profile"), {"action": "tg_notify"})
    assert not UserProfile.objects.get(user=users["anna"]).notify_new_books


def test_checkbox_hidden_without_linked_telegram(setup):
    users, _books, _client, _before = setup
    TelegramLink.objects.filter(user=users["boris"]).delete()
    browser = Client()
    browser.force_login(users["boris"])
    assert 'name="notify_new_books"' not in browser.get(reverse("web:profile")).content.decode("utf-8")
