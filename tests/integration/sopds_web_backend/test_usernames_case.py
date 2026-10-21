"""Имена пользователей без учёта регистра (sopds_web_backend/usernames.py):
`Admin` при входе — это `admin` (сайт — через него и /admin/, читалки OPDS — основной
пароль и пароль для читалок), «Anna» при существующей «anna» не создаётся;
кириллица — тоже («Анна» / «анна»). Пары, заведённые раньше, входят только
точным написанием и видны на странице пользователей."""
import base64

import pytest
from django.contrib.auth.models import User
from django.contrib.sessions.backends.db import SessionStore
from django.test import Client
from django.urls import reverse

from sopds_web_backend import opds_passwords
from sopds_web_backend.usernames import case_conflicts, find_user

PASSWORD = "Kx7#vQ2!mLp9"
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def plain_static(settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


def _login(client, username, password=PASSWORD):
    return client.post(reverse("web:login"), {"username": username, "password": password})


def _signed_in(client):
    return client.get(reverse("web:main")).status_code == 200


@pytest.mark.parametrize("stored, typed", [("reader", "READER"), ("Reader", "reader"), ("Анна", "анна")])
def test_login_ignores_case(stored, typed):
    User.objects.create_user(stored, password=PASSWORD)
    client = Client()
    assert _login(client, typed).status_code == 302 and _signed_in(client)


def test_wrong_password_still_fails():
    User.objects.create_user("reader", password=PASSWORD)
    client = Client()
    assert _login(client, "READER", "wrong-password").status_code != 302 and not _signed_in(client)
    assert _login(client, "nobody", PASSWORD).status_code != 302


def test_old_pairs_sign_in_only_with_exact_spelling():
    User.objects.create_user("anna", password=PASSWORD)
    User.objects.create_user("Anna", password=PASSWORD)
    assert find_user("anna").username == "anna" and find_user("Anna").username == "Anna"
    assert find_user("ANNA") is None                                   # неоднозначно — никто
    assert _login(Client(), "ANNA").status_code != 302
    assert case_conflicts() == [["Anna", "anna"]]
    root = Client()
    root.force_login(User.objects.create_superuser("root", password=PASSWORD))
    page = root.get(reverse("web:users_list")).content.decode("utf-8")
    assert "differ only in letter case" in page and "Anna / anna" in page


def test_cannot_create_user_differing_only_in_case():
    User.objects.create_user("анна", password=PASSWORD)
    root = Client()
    root.force_login(User.objects.create_superuser("root", password=PASSWORD))
    response = root.post(reverse("web:user_create"), {"username": "Анна", "password": "Zq8!wErT5yUi", "role": "user"})
    assert response.status_code == 422 and "already exists" in response.content.decode("utf-8")
    assert not User.objects.filter(username="Анна").exists()
    assert "differ only in letter case" not in root.get(reverse("web:users_list")).content.decode("utf-8")


def _basic(username, password):
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def test_opds_main_and_reader_passwords_ignore_case(override_config):
    user = User.objects.create_user("reader", password=PASSWORD)
    reader_password = opds_passwords.generate(user)
    with override_config(SOPDS_AUTH=True):
        for password in (PASSWORD, reader_password):
            response = Client().get(reverse("opds_catalog:main"), HTTP_AUTHORIZATION=_basic("Reader", password))
            assert response.status_code == 200, password
        response = Client().get(reverse("opds_catalog:main"), HTTP_AUTHORIZATION=_basic("Reader", "wrong"))
        assert response.status_code == 401


def test_sessions_opened_before_keep_working():
    """Путь бэкенда хранится в сессии: сессии с прежним ModelBackend не
    должны пропасть после обновления (иначе — выход у всех)."""
    user = User.objects.create_user("reader", password=PASSWORD)
    session = SessionStore()
    session.update({"_auth_user_id": str(user.pk), "_auth_user_backend": "django.contrib.auth.backends.ModelBackend",
                    "_auth_user_hash": user.get_session_auth_hash()})
    session.save()
    client = Client()
    client.cookies["sessionid"] = session.session_key
    assert _signed_in(client)
