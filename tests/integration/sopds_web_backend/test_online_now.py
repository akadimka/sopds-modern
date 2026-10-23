"""«Сейчас на сайте» в шапке (sopds_web_backend/online.py): число видят все
вошедшие, имена — только суперпользователь (они помогают подбирать пароли);
считаются люди за последние 5 минут — через сайт и читалки (OPDS), не
Telegram; опрос самой шапки активностью не считается. Время сканов в той же
строке — только суперпользователю."""
import base64
import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from sopds_web_backend import online

PASSWORD = "Kx7#vQ2!mLp9"
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def plain_static(settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


def _client(user):
    client = Client()
    client.force_login(user)
    return client


def _count(html):
    return int(re.search(r'id="sp-online".*?<strong>\((\d+)\)</strong>', html, re.S).group(1))


def _page(client):
    return client.get(reverse("web:main")).content.decode("utf-8")


def test_everyone_sees_who_is_online_scans_only_superuser():
    anna = User.objects.create_user("anna", password=PASSWORD)
    root = User.objects.create_superuser("root", password=PASSWORD)
    _page(_client(root))
    html = _page(_client(anna))
    assert "Online now:" in html and _count(html) == 2
    assert "<strong>root</strong>" not in html and "sp-online-list" not in html   # имена — не ей
    assert "Last manual scan" not in html and "Last automatic scan" not in html
    html = _page(_client(root))
    assert "Last manual scan" in html and "Last automatic scan" in html and _count(html) == 2
    assert "<strong>root</strong>" in html and "<strong>anna</strong>" in html


def test_people_not_devices():
    anna = User.objects.create_user("anna", password=PASSWORD)
    _page(_client(anna))
    assert _count(_page(_client(anna))) == 1


def test_reader_counts_with_mark(override_config):
    anna = User.objects.create_user("anna", password=PASSWORD)
    auth = "Basic " + base64.b64encode(f"anna:{PASSWORD}".encode()).decode()
    with override_config(SOPDS_AUTH=True):
        assert Client().get(reverse("opds_catalog:main"), HTTP_AUTHORIZATION=auth).status_code == 200
    assert [u["channels"] for u in online.online_users()] == [["opds"]]
    _page(_client(anna))
    assert [u["channels"] for u in online.online_users()] == [["web", "opds"]]
    root = _client(User.objects.create_superuser("root", password=PASSWORD))
    assert "site, reader" in root.get(reverse("web:online")).content.decode("utf-8")


def test_header_poll_is_not_activity():
    anna = User.objects.create_user("anna", password=PASSWORD)
    response = _client(anna).get(reverse("web:online"))
    assert response.status_code == 200 and 'id="sp-online"' in response.content.decode("utf-8")
    assert online.online_users() == []


def test_marks_expire_after_window(monkeypatch):
    anna = User.objects.create_user("anna", password=PASSWORD)
    _page(_client(anna))
    now = online.time.time()
    monkeypatch.setattr(online.time, "time", lambda: now + 3 * 60)
    assert online.online_users()[0]["minutes_ago"] == 3
    monkeypatch.setattr(online.time, "time", lambda: now + online.WINDOW + 1)
    assert online.online_users() == []


def test_inactive_and_anonymous_are_not_listed():
    anna = User.objects.create_user("anna", password=PASSWORD)
    online.mark(anna.pk, "web")
    User.objects.filter(pk=anna.pk).update(is_active=False)
    assert online.online_users() == []
    assert Client().get(reverse("web:online")).status_code == 204        # без входа — пусто (не страница входа в шапке)


def test_logout_leaves_list_at_once():
    anna = User.objects.create_user("anna", password=PASSWORD)
    client = _client(anna)
    _page(client)
    assert len(online.online_users()) == 1
    client.post(reverse("web:logout"))
    assert online.online_users() == []


def test_site_pages_using_opds_addresses_are_site(override_config):
    """Обложки и скачивание на страницах сайта идут с /opds/ — это сайт, не
    читалка: канал определяется способом входа."""
    anna = User.objects.create_user("anna", password=PASSWORD)
    client = _client(anna)
    with override_config(SOPDS_AUTH=True):
        client.get(reverse("opds_catalog:main"))
    assert [u["channels"] for u in online.online_users()] == [["web"]]
