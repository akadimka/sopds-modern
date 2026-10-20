"""Код входа для суперпользователей через Telegram (sopds_web_backend/login_codes.py).

После пароля суперпользователь вводит код, который бот присылает
по двум каналам: в личный чат с ботом (привязанный Telegram) и в чат
суперпользователя из настроек. Не доставлен никуда — вход не выполняется.
Обычные пользователи входят как раньше; вход в /admin/ — только через
общую форму (у стандартной формы Django второго шага нет).
"""
import json
import re

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

import fb2parser_core.telegram_notify as tn
import sopds_web_backend.login_codes as lc
from sopds_web_backend.models import TelegramLink

_STRONG = "Kx7#vQ2!mLp9"


@pytest.fixture
def tg(monkeypatch):
    """Telegram-клиент без сети; cfg — настройки кода входа."""
    sent, failing = [], set()

    class FakeClient:
        def __init__(self, token, proxy="", timeout=20.0):
            pass

        def send_message(self, chat_id, text, reply_markup=None):
            if str(chat_id) in failing:
                raise tn.TelegramError("Bad Request: chat not found", 400)
            sent.append((str(chat_id), text))

    cfg = {"telegram_token": "1:A", "telegram_proxy": "", "telegram_admin_chat": "-100500",
           "admin_login_code": True}
    monkeypatch.setattr(tn, "TelegramClient", FakeClient)
    monkeypatch.setattr(lc, "_settings", lambda: cfg)
    return type("Tg", (), {"sent": sent, "failing": failing, "cfg": cfg})


def _admin(name="boss", tg_id=4242):
    user = User.objects.create_user(name, password=_STRONG, is_staff=True, is_superuser=True)
    if tg_id:
        TelegramLink.objects.create(user=user, telegram_id=tg_id)
    return user


def _code(text):
    return re.search(r"<b>(\d{6})</b>", text).group(1)


def _signed_in(client):
    return client.get(reverse("web:profile")).status_code == 200


@pytest.mark.django_db
def test_admin_gets_code_in_both_channels_and_signs_in_with_it(client, tg):
    _admin()
    resp = client.post(reverse("web:login") + "?next=/web/profile/", {"username": "boss", "password": _STRONG})
    assert resp.status_code == 302 and resp["Location"] == reverse("web:login_code")
    assert not _signed_in(client)  # пароль верный, но без кода — ещё не вошёл
    assert sorted(chat for chat, _t in tg.sent) == ["-100500", "4242"]
    code = _code(tg.sent[0][1])
    assert all(code in text and "boss" in text for _c, text in tg.sent)

    resp = client.post(reverse("web:login_code"), {"code": code})
    assert resp.status_code == 302 and resp["Location"] == "/web/profile/"
    assert _signed_in(client)


@pytest.mark.django_db
def test_one_channel_is_enough(client, tg):
    _admin()
    tg.failing.add("4242")  # личный чат не принял сообщение
    resp = client.post(reverse("web:login"), {"username": "boss", "password": _STRONG})
    assert resp["Location"] == reverse("web:login_code")
    assert [chat for chat, _t in tg.sent] == ["-100500"]


@pytest.mark.django_db
def test_no_channel_means_no_sign_in(client, tg):
    _admin(tg_id=None)
    tg.cfg["telegram_admin_chat"] = ""
    resp = client.post(reverse("web:login"), {"username": "boss", "password": _STRONG})
    assert resp.status_code == 403
    assert "admin_login_code off" in resp.content.decode()
    assert not _signed_in(client)
    assert client.get(reverse("web:login_code"))["Location"] == reverse("web:login")


@pytest.mark.django_db
def test_wrong_codes_then_sign_in_again(client, tg):
    _admin()
    client.post(reverse("web:login"), {"username": "boss", "password": _STRONG})
    code = _code(tg.sent[0][1])
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(lc.MAX_ATTEMPTS - 1):
        assert "Wrong code" in client.post(reverse("web:login_code"), {"code": wrong}).content.decode()
    resp = client.post(reverse("web:login_code"), {"code": wrong})
    assert resp.status_code == 403
    # попытки кончились — даже верный код больше не принимается
    assert client.post(reverse("web:login_code"), {"code": code})["Location"] == reverse("web:login")
    assert not _signed_in(client)


@pytest.mark.django_db
def test_expired_code(client, tg, monkeypatch):
    _admin()
    client.post(reverse("web:login"), {"username": "boss", "password": _STRONG})
    code = _code(tg.sent[0][1])
    now = lc.time.time()
    monkeypatch.setattr(lc.time, "time", lambda: now + lc.CODE_TTL_SECONDS + 1)
    assert client.post(reverse("web:login_code"), {"code": code}).status_code == 403
    assert not _signed_in(client)


@pytest.mark.django_db
def test_readers_and_disabled_setting_skip_the_code(client, tg):
    User.objects.create_user("reader", password=_STRONG)
    assert client.post(reverse("web:login"), {"username": "reader", "password": _STRONG})["Location"] == "/web/"
    client.logout()

    tg.cfg["admin_login_code"] = False
    _admin()
    assert client.post(reverse("web:login"), {"username": "boss", "password": _STRONG})["Location"] == "/web/"
    assert tg.sent == []


def test_django_admin_login_goes_through_the_site_form(client):
    resp = client.get("/admin/login/?next=/admin/")
    assert resp.status_code == 302
    assert resp["Location"] == reverse("web:login") + "?next=/admin/"


# ---------- включение в настройках ----------

@pytest.fixture
def config(tmp_path, monkeypatch, settings):
    import fb2parser_core.settings_manager as sm_module
    import opds_catalog.ratings_fetchers as fetchers

    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"sopds": {"fb2toepub": "", "temp_dir": "", "language": "en-US"},
                                "autosync": {"telegram_token": "1:A"}}), encoding="utf-8")
    real = sm_module.SettingsManager
    monkeypatch.setattr(sm_module, "SettingsManager", lambda _path: real(str(path)))
    monkeypatch.setattr(fetchers, "wake_or_start", lambda name: None)
    monkeypatch.setattr(fetchers, "stop_fetcher", lambda name: None)
    return lambda: json.loads(path.read_text(encoding="utf-8"))


def _save_settings(client, **fields):
    data = {"language": "en-US", "maxitems": "20", "fb2toepub": "", "temp_dir": "", "autosync_mode": "off"}
    data.update(fields)
    return client.post(reverse("web:settings"), data)


@pytest.mark.django_db
def test_cannot_enable_without_a_channel_for_yourself(client, config):
    root = User.objects.create_superuser("root", password=_STRONG)
    client.force_login(root)
    resp = _save_settings(client, admin_login_code="on")
    assert resp.status_code == 200 and "link your Telegram" in resp.content.decode()
    assert not config()["autosync"].get("admin_login_code")

    TelegramLink.objects.create(user=root, telegram_id=99)
    assert _save_settings(client, admin_login_code="on").status_code == 302
    assert config()["autosync"]["admin_login_code"] is True


@pytest.mark.django_db
def test_only_superuser_changes_login_security(client, config):
    client.force_login(User.objects.create_user("staff", password=_STRONG, is_staff=True))
    _save_settings(client, admin_login_code="on", opds_app_passwords_only="on")
    saved = config()
    assert not saved["autosync"].get("admin_login_code")
    assert not saved["sopds"].get("opds_app_passwords_only")


def test_management_command_turns_the_code_off(tmp_path, monkeypatch):
    from django.core.management import call_command

    import fb2parser_core.settings_manager as sm_module
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"autosync": {"admin_login_code": True}}), encoding="utf-8")
    real = sm_module.SettingsManager
    monkeypatch.setattr("opds_catalog.management.commands.admin_login_code.SettingsManager",
                        lambda _path: real(str(path)))
    call_command("admin_login_code", "off")
    assert json.loads(path.read_text(encoding="utf-8"))["autosync"]["admin_login_code"] is False
