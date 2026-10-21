"""Устройства учётной записи и их лимит (docs/devices-design.md, этап 1):
устройство — браузер (метка в cookie `sopds_device`), лимит — личный или по
умолчанию из настроек (0 — без ограничений), на суперпользователя не
действует; лимит исчерпан — вход с нового браузера отклоняется; удалённое
устройство — выход; OPDS устройств не заводит."""
import base64

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from sopds_web_backend import devices
from sopds_web_backend.models import UserDevice, UserProfile

PASSWORD = "Kx7#vQ2!mLp9"
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def plain_static(settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


def _user(name="reader", limit=None, **flags):
    user = User.objects.create_user(name, password=PASSWORD, **flags)
    UserProfile.objects.filter(user=user).update(max_devices=limit)
    return user


def _login(client, user):
    return client.post(reverse("web:login"), {"username": user.username, "password": PASSWORD})


def _signed_in(client):
    return client.get(reverse("web:main")).status_code == 200


def test_login_creates_device_and_reuses_it():
    user = _user()
    client = Client(HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36")
    assert _login(client, user).status_code == 302 and _signed_in(client)
    cookie = client.cookies[devices.COOKIE]
    assert cookie["httponly"] and len(cookie.value) >= 40
    [device] = UserDevice.objects.filter(user=user)
    assert device.label == "Chrome, Windows" and device.token_hash != cookie.value   # в базе — хеш
    client.post(reverse("web:logout"))
    assert _login(client, user).status_code == 302
    assert UserDevice.objects.filter(user=user).count() == 1


def test_limit_reached_refuses_new_browser_with_message():
    user = _user(limit=1)
    first, second = Client(), Client()
    assert _login(first, user).status_code == 302
    response = _login(second, user)
    assert response.status_code == 403
    assert "limit of connected devices" in response.content.decode("utf-8")
    assert not _signed_in(second) and UserDevice.objects.filter(user=user).count() == 1
    assert _login(first, user).status_code == 302          # известное устройство — можно


def test_default_limit_from_settings_and_personal_override(override_config):
    with override_config(SOPDS_DEVICES_PER_USER=1):
        user = _user()                                       # личного лимита нет — из настроек
        assert _login(Client(), user).status_code == 302
        assert _login(Client(), user).status_code == 403
        UserProfile.objects.filter(user=user).update(max_devices=0)   # 0 — без ограничений
        assert _login(Client(), user).status_code == 302


def test_superuser_has_no_limit(override_config):
    with override_config(SOPDS_DEVICES_PER_USER=1):
        root = User.objects.create_superuser("root", password=PASSWORD)
        for _ in range(3):
            assert _login(Client(), root).status_code == 302
        assert UserDevice.objects.filter(user=root).count() == 3   # устройства видны и у него


def test_removed_device_signs_out_with_notice():
    user = _user()
    client = Client()
    _login(client, user)
    UserDevice.objects.filter(user=user).delete()
    response = client.get(reverse("web:main"))
    assert response.status_code == 302 and reverse("web:login") in response["Location"]
    page = client.get(reverse("web:login")).content.decode("utf-8")
    assert "This device was removed from your devices" in page


def test_session_without_device_is_bound_or_signed_out():
    """Сессии, открытые до появления функции, привязываются к устройству;
    при исчерпанном лимите — выход с сообщением о лимите."""
    user = _user(limit=1)
    old = Client()
    old.force_login(user)
    assert _signed_in(old) and UserDevice.objects.filter(user=user).count() == 1
    another = Client()
    another.force_login(user)
    assert not _signed_in(another)
    assert "limit of connected devices" in another.get(reverse("web:login")).content.decode("utf-8")


def test_session_cookie_moved_to_another_browser_counts_as_new_device():
    user = _user(limit=1)
    owner = Client()
    _login(owner, user)
    stranger = Client()
    stranger.cookies["sessionid"] = owner.cookies["sessionid"].value   # без метки устройства
    assert not _signed_in(stranger)


def test_opds_does_not_create_devices(override_config):
    user = _user(limit=1)
    with override_config(SOPDS_AUTH=True):
        auth = "Basic " + base64.b64encode(f"{user.username}:{PASSWORD}".encode()).decode()
        response = Client().get(reverse("opds_catalog:main"), HTTP_AUTHORIZATION=auth)
    assert response.status_code == 200 and not UserDevice.objects.filter(user=user).exists()


@pytest.mark.parametrize("ua, label", [
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit Version/17.0 Mobile Safari/604.1",
     "Safari, iPhone"),
    ("Mozilla/5.0 (Linux; Android 14) Chrome/120.0 Mobile Safari/537.36", "Chrome, Android"),
    ("Mozilla/5.0 (Windows NT 10.0) Chrome/120.0 Safari/537.36 Edg/120.0", "Edge, Windows"),
    ("Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0", "Firefox, Linux"),
    ("", ""),
])
def test_label_for(ua, label):
    assert devices.label_for(ua) == label


def test_parallel_requests_of_unbound_session_make_one_device():
    """Браузер шлёт несколько запросов сразу (страница + htmx), все — без метки
    устройства: раньше каждый заводил своё устройство (встроенный браузер VS
    Code — 4 устройства за миллисекунду). Метка таких запросов вычисляется из
    ключа сессии — устройство одно."""
    user = _user(limit=1)
    first = Client()
    first.force_login(user)
    parallel = Client()
    parallel.cookies["sessionid"] = first.cookies["sessionid"].value
    assert _signed_in(first) and _signed_in(parallel)          # второй — тоже без метки устройства
    assert UserDevice.objects.filter(user=user).count() == 1
