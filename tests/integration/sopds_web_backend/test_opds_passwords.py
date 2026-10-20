"""Пароли для читалок (sopds_web_backend/opds_passwords.py) и сессии «только OPDS».

Читалка хранит пароль и шлёт его с каждым запросом (HTTP Basic). Пароль
для читалок подходит только к /opds/; сессия, созданная входом читалки (по
любому паролю), на сайте и в /fb2parser/ не действует — иначе вход через
читалку обходил бы код входа суперпользователя. Отзыв пароля гасит и его
сессии. Опция SOPDS_OPDS_APP_PASSWORDS_ONLY запрещает читалкам основной пароль.
"""
import base64
import re

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from sopds_web_backend import opds_passwords

_STRONG = "Kx7#vQ2!mLp9"
pytestmark = [pytest.mark.django_db, pytest.mark.override_config(SOPDS_AUTH=True)]


def _basic(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"HTTP_AUTHORIZATION": f"Basic {token}"}


def _opds(client, username=None, password=None):
    return client.get(reverse("opds:main"), **(_basic(username, password) if username else {}))


def test_generated_password_works_in_any_spelling():
    user = User.objects.create_user("reader", password=_STRONG)
    password = opds_passwords.generate(user)
    assert len(password) == 23 and password.count("-") == 3
    assert opds_passwords.authenticate("reader", password).user == user
    assert opds_passwords.authenticate("reader", password.lower().replace("-", " ")) is not None
    assert opds_passwords.authenticate("reader", _STRONG) is None
    assert opds_passwords.authenticate("someone", password) is None

    opds_passwords.revoke(user)
    assert opds_passwords.authenticate("reader", password) is None


def test_reader_password_opens_opds_but_not_the_site(client):
    user = User.objects.create_user("reader", password=_STRONG)
    password = opds_passwords.generate(user)
    assert _opds(client, "reader", password).status_code == 200
    assert _opds(client).status_code == 200  # дальше — по сессии, без пароля

    resp = client.get(reverse("web:profile"))
    assert resp.status_code == 302 and resp["Location"].startswith(reverse("web:login"))


def test_admin_session_from_reader_app_does_not_open_fb2parser(client):
    User.objects.create_user("boss", password=_STRONG, is_staff=True)
    assert _opds(client, "boss", _STRONG).status_code == 200  # основной пароль в читалке
    resp = client.get(reverse("fb2parser:dashboard"))
    assert resp.status_code == 302 and "login" in resp["Location"]


def test_revoked_password_ends_its_sessions(client):
    user = User.objects.create_user("reader", password=_STRONG)
    password = opds_passwords.generate(user)
    assert _opds(client, "reader", password).status_code == 200
    opds_passwords.revoke(user)
    assert _opds(client).status_code == 401
    opds_passwords.generate(user)  # новый пароль — старый и его сессии не оживают
    assert _opds(client, "reader", password).status_code == 401


def test_site_password_can_be_forbidden_for_readers(client, override_config):
    user = User.objects.create_user("reader", password=_STRONG)
    password = opds_passwords.generate(user)
    with override_config(SOPDS_OPDS_APP_PASSWORDS_ONLY=True):
        assert _opds(client, "reader", _STRONG).status_code == 401
        assert _opds(client, "reader", password).status_code == 200


def test_signing_in_on_the_site_lifts_the_opds_only_mark(client):
    User.objects.create_user("reader", password=_STRONG)
    assert _opds(client, "reader", _STRONG).status_code == 200
    client.post(reverse("web:login"), {"username": "reader", "password": _STRONG})
    assert client.get(reverse("web:profile")).status_code == 200


def test_profile_creates_shows_once_and_revokes(client, monkeypatch, settings):
    import sopds_web_backend.telegram_users as tu
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    monkeypatch.setattr(tu, "telegram", lambda service=None: (None, None))
    user = User.objects.create_user("reader", password=_STRONG)
    client.force_login(user)

    page = client.post(reverse("web:profile"), {"action": "opds_new"}).content.decode()
    shown = re.findall(r"<code>([A-Z0-9-]{23})</code>", page)
    assert len(shown) == 1 and opds_passwords.authenticate("reader", shown[0]) is not None
    assert shown[0] not in client.get(reverse("web:profile")).content.decode()  # только один раз

    client.post(reverse("web:profile"), {"action": "opds_revoke"})
    assert opds_passwords.authenticate("reader", shown[0]) is None
