"""Вход продлевается, пока им пользуются (PWA, 2026-11-02): срок сессии
отсчитывался от входа, и человек, открывающий приложение каждый день, всё
равно выходил через 2 недели. Теперь раз в сутки срок и cookie продлеваются
(sopds/sessions.py, SessionRefreshMiddleware); чаще — без записи в базу."""
from datetime import timedelta

import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.sessions.models import Session
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from sopds import sessions

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def plain_static(settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


def _session(browser):
    return Session.objects.get(session_key=browser.cookies[settings.SESSION_COOKIE_NAME].value)


def _age_session(browser, days):
    """Сессия, как будто вход и последнее продление были `days` дней назад."""
    row = _session(browser)
    data = row.get_decoded()
    data[sessions.REFRESH_KEY] = data.get(sessions.REFRESH_KEY, 0) - days * 24 * 3600
    row.session_data = Session.objects.encode(data)
    row.expire_date = timezone.now() + timedelta(seconds=settings.SESSION_COOKIE_AGE) - timedelta(days=days)
    row.save()


def test_daily_use_extends_the_session():
    user = User.objects.create_user("reader", password="Kx7#vQ2!mLp9")
    browser = Client()
    browser.force_login(user)
    browser.get(reverse("web:main"))
    _age_session(browser, 10)                       # вход 10 дней назад: осталось 4 дня
    old_expiry = _session(browser).expire_date

    response = browser.get(reverse("web:main"))
    assert response.status_code == 200
    expiry = _session(browser).expire_date
    assert expiry - old_expiry > timedelta(days=9)  # снова 2 недели от сегодня
    assert response.cookies[settings.SESSION_COOKIE_NAME]["max-age"] == settings.SESSION_COOKIE_AGE


def test_no_write_more_than_once_a_day():
    user = User.objects.create_user("reader", password="Kx7#vQ2!mLp9")
    browser = Client()
    browser.force_login(user)
    browser.get(reverse("web:main"))                # первая отметка продления
    stamp = _session(browser).get_decoded()[sessions.REFRESH_KEY]
    response = browser.get(reverse("web:main"))
    assert _session(browser).get_decoded()[sessions.REFRESH_KEY] == stamp
    assert settings.SESSION_COOKIE_NAME not in response.cookies   # сессия не пересохранялась


def test_anonymous_session_is_not_touched():
    browser = Client()
    browser.get(reverse("web:login"))
    assert not Session.objects.exists() or all(
        sessions.REFRESH_KEY not in s.get_decoded() for s in Session.objects.all())
