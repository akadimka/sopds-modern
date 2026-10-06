"""Привязка аккаунтов SOPDS к Telegram и доступ к каналу (sopds_web_backend/telegram_users.py).

Читатели — люди с аккаунтом на сервере; привязка — только для бота и
канала. В канал — по заявке, бот одобряет только привязанных; отвязка,
удаление или блокировка аккаунта удаляют из канала.
"""
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

import sopds_web_backend.telegram_users as tu
from fb2parser_core.autosync_service import AutosyncJournal
from sopds_web_backend.models import TelegramLink
from sopds_web_backend.telegram_library import INTRO, LibraryHandler

_STRONG = "Kx7#vQ2!mLp9"


class FakeClient:
    def __init__(self):
        self.calls, self.sent = [], []

    def call(self, method, **params):
        self.calls.append((method, params))
        return {"getMe": {"username": "lib_bot"},
                "createChatInviteLink": {"invite_link": "https://t.me/+JOIN"}}.get(method, True)

    def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text))


@pytest.fixture
def tg(monkeypatch, tmp_path):
    client = FakeClient()
    service = SimpleNamespace(cfg={"telegram_token": "1:A", "telegram_proxy": "", "telegram_channel": "-1009"},
                              journal=AutosyncJournal(tmp_path / "journal.db"))
    monkeypatch.setattr(tu, "telegram", lambda service_=None: (client, service))
    return SimpleNamespace(client=client, service=service)


def _user(name="reader", **kw):
    return User.objects.create_user(name, password=_STRONG, **kw)


def _tg_user(tid=777, username="reader_tg"):
    return {"id": tid, "first_name": "Иван", "username": username}


# ---------- коды и привязка ----------

@pytest.mark.django_db
def test_link_by_code_from_profile():
    user = _user()
    code = tu.new_link_code(user).code
    assert len(code) == tu.CODE_LENGTH and set(code) <= set(tu.CODE_ALPHABET)

    link, err = tu.link_by_code(f"link_{code.lower()}", _tg_user())

    assert err == "" and link.telegram_id == 777 and "@reader_tg" in link.telegram_name
    assert link.code == "" and tu.linked_user(777) == user


@pytest.mark.django_db
def test_expired_code_inactive_user_and_taken_telegram():
    expired = tu.new_link_code(_user("a"))
    TelegramLink.objects.filter(pk=expired.pk).update(code_expires=timezone.now() - timedelta(minutes=1))
    assert tu.link_by_code(expired.code, _tg_user())[1] == tu.ERR_BAD_CODE

    blocked = _user("b", is_active=False)
    assert tu.link_by_code(tu.new_link_code(blocked).code, _tg_user())[1] == tu.ERR_BAD_CODE

    first = tu.new_link_code(_user("c"))
    tu.link_by_code(first.code, _tg_user(1))
    second = tu.new_link_code(_user("d"))
    assert tu.link_by_code(second.code, _tg_user(1))[1] == tu.ERR_TAKEN


@pytest.mark.django_db
def test_inactive_user_is_not_a_reader():
    user = _user()
    tu.link_by_code(tu.new_link_code(user).code, _tg_user())
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert tu.linked_user(777) is None


# ---------- канал ----------

@pytest.mark.django_db
def test_join_request_approved_only_for_linked(tg):
    user = _user()
    tu.link_by_code(tu.new_link_code(user).code, _tg_user())

    assert tu.handle_join_request(tg.client, tg.service, {"chat": {"id": -1009}, "from": {"id": 777}})
    assert not tu.handle_join_request(tg.client, tg.service, {"chat": {"id": -1009}, "from": {"id": 999}})
    assert not tu.handle_join_request(tg.client, tg.service, {"chat": {"id": -5}, "from": {"id": 777}})

    methods = [m for m, _p in tg.client.calls]
    assert methods == ["approveChatJoinRequest", "declineChatJoinRequest"]
    assert TelegramLink.objects.get(user=user).in_channel


@pytest.mark.django_db
def test_unlink_removes_from_channel(tg):
    user = _user()
    link, _err = tu.link_by_code(tu.new_link_code(user).code, _tg_user())

    assert tu.unlink(link)

    assert [m for m, _p in tg.client.calls] == ["banChatMember", "unbanChatMember"]
    assert tg.client.calls[1][1]["only_if_banned"] is True
    assert tu.linked_user(777) is None


@pytest.mark.django_db
@pytest.mark.parametrize("action", ["deactivate", "delete"])
def test_blocked_or_deleted_account_is_removed_from_channel(tg, action):
    user = _user()
    tu.link_by_code(tu.new_link_code(user).code, _tg_user())
    TelegramLink.objects.filter(user=user).update(in_channel=True)

    if action == "deactivate":
        user.is_active = False
        user.save()
    else:
        user.delete()

    assert ("banChatMember", {"chat_id": "-1009", "user_id": 777}) in tg.client.calls


# ---------- диалог с ботом ----------

def _msg(text, tid=777):
    return {"message": {"chat": {"id": tid, "type": "private"}, "from": _tg_user(tid), "text": text}}


@pytest.mark.django_db
def test_bot_links_by_start_payload_and_sends_channel_link(tg):
    code = tu.new_link_code(_user()).code

    assert LibraryHandler()(tg.service, tg.client, _msg(f"/start link_{code}"))

    reply = tg.client.sent[-1][1]
    assert "Готово" in reply and "https://t.me/+JOIN" in reply
    create = next(p for m, p in tg.client.calls if m == "createChatInviteLink")
    assert create["creates_join_request"] is True


@pytest.mark.django_db
def test_bot_for_strangers_and_typed_code(tg):
    handler = LibraryHandler()
    handler(tg.service, tg.client, _msg("Лукьяненко", tid=5))
    assert tg.client.sent[-1] == (5, INTRO)

    code = tu.new_link_code(_user()).code
    handler(tg.service, tg.client, _msg(code.lower(), tid=5))
    assert "Готово" in tg.client.sent[-1][1] and tu.linked_user(5) is not None

    assert handler(tg.service, tg.client, _msg("/id")) is False  # chat id отвечает AdminBot
    assert handler(tg.service, tg.client, {"chat_join_request": {"chat": {"id": -1009}, "from": {"id": 5}}})


# ---------- сайт ----------

@pytest.fixture
def web(settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


@pytest.mark.django_db
def test_profile_link_code_and_unlink(client, tg, web):
    user = _user()
    client.force_login(user)

    page = client.post(reverse("web:profile"), {"action": "tg_code"}).content.decode("utf-8")
    code = TelegramLink.objects.get(user=user).code
    assert code in page and f"https://t.me/lib_bot?start=link_{code}" in page

    tu.link_by_code(code, _tg_user())
    page = client.get(reverse("web:profile")).content.decode("utf-8")
    assert "@reader_tg" in page

    client.post(reverse("web:profile"), {"action": "tg_unlink"})
    assert tu.linked_user(777) is None


@pytest.mark.django_db
def test_profile_without_bot_token(client, monkeypatch, web):
    monkeypatch.setattr(tu, "telegram", lambda service_=None: (None, None))
    client.force_login(_user())
    page = client.get(reverse("web:profile")).content.decode("utf-8")
    assert 'value="tg_code"' not in page


@pytest.mark.django_db
def test_admin_sees_and_unlinks_telegram(client, tg, web):
    reader = _user()
    tu.link_by_code(tu.new_link_code(reader).code, _tg_user())
    client.force_login(User.objects.create_superuser("root", password=_STRONG))

    assert "@reader_tg" in client.get(reverse("web:users_list")).content.decode("utf-8")
    client.post(reverse("web:user_telegram_unlink", args=[reader.pk]))
    assert tu.linked_user(777) is None
