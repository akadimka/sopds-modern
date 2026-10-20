"""Пользователь меняет свой пароль «в настройках»: у обычного пользователя
«Настройки» в шапке ведут на его личные настройки (профиль: пароль, вход,
пароль для читалок), у суперпользователя — на настройки сайта, где сверху
ссылка на его личные. Смена пароля не разлогинивает."""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse

OLD, NEW = "Kx7#vQ2!mLp9", "Zr4$wN8@tYq1"


@pytest.fixture(autouse=True)
def plain_static(settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


def _header(html):
    return html[html.index('class="top-bar-right"'):html.index("</ul>", html.index('class="top-bar-right"'))]


@pytest.mark.django_db
def test_regular_user_settings_link_leads_to_own_settings(client):
    client.force_login(User.objects.create_user("reader", password=OLD))
    header = _header(client.get(reverse("web:main")).content.decode("utf-8"))
    assert f'href="{reverse("web:profile")}">Settings<' in header
    assert reverse("web:settings") not in header


@pytest.mark.django_db
def test_superuser_settings_link_and_link_to_own_settings(client):
    client.force_login(User.objects.create_superuser("root", password=OLD))
    header = _header(client.get(reverse("web:main")).content.decode("utf-8"))
    assert f'href="{reverse("web:settings")}">Settings<' in header
    page = client.get(reverse("web:settings")).content.decode("utf-8")
    assert f'href="{reverse("web:profile")}">My settings (password, sign-in)<' in page


@pytest.mark.django_db
def test_user_changes_own_password_and_stays_signed_in(client):
    user = User.objects.create_user("reader", password=OLD)
    client.force_login(user)
    page = client.get(reverse("web:profile")).content.decode("utf-8")
    assert 'name="old_password"' in page and 'name="new_password"' in page
    response = client.post(reverse("web:profile"), {"action": "password", "old_password": OLD,
                                                     "new_password": NEW, "confirm_password": NEW})
    assert response.status_code in (200, 302)
    user.refresh_from_db()
    assert user.check_password(NEW)
    assert client.get(reverse("web:main")).status_code == 200   # сессия жива


@pytest.mark.django_db
def test_wrong_current_password_keeps_old_one(client):
    user = User.objects.create_user("reader", password=OLD)
    client.force_login(user)
    client.post(reverse("web:profile"), {"action": "password", "old_password": "wrong",
                                         "new_password": NEW, "confirm_password": NEW})
    user.refresh_from_db()
    assert user.check_password(OLD)
