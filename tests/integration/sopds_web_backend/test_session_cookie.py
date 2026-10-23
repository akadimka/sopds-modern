"""Разлогин сразу после смены своего пароля (сервер, 2026-10-23).

Смена пароля меняет ключ сессии; фоновый опрос htmx, отправленный ещё со
старой cookie, получал от стандартного SessionMiddleware команду удалить
cookie — и она стирала уже новую. Дальше: «Выход» без сессии вёл на вход с
next=/web/logout/, после входа — 405, повторная отправка старой формы — 403
CSRF. Здесь — каждое звено.
"""
import pytest
from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

pytestmark = pytest.mark.django_db
STRONG = "Kx7#vQ2!mLp9"
NEW = "Zq8!wErT5yUi"


@pytest.fixture(autouse=True)
def plain_static(settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


def _deletes_session_cookie(response):
    cookie = response.cookies.get(settings.SESSION_COOKIE_NAME)
    return cookie is not None and (cookie.value == "" or cookie["max-age"] == 0)


def test_request_with_stale_cookie_does_not_wipe_the_new_one():
    root = User.objects.create_superuser("root", password=STRONG)
    browser = Client()
    browser.force_login(root)
    old_key = browser.cookies[settings.SESSION_COOKIE_NAME].value
    response = browser.post(reverse("web:user_edit", args=(root.pk,)), {"role": "superuser", "password": NEW})
    assert response.status_code == 302
    new_key = browser.cookies[settings.SESSION_COOKIE_NAME].value
    assert new_key != old_key                                       # ключ сменился — старые копии не работают

    late_poll = Client()                                            # тот же браузер, запрос ушёл до смены
    late_poll.cookies[settings.SESSION_COOKIE_NAME] = old_key
    response = late_poll.get(reverse("web:online"), HTTP_HX_REQUEST="true")
    assert response.status_code == 204                              # без входа — пусто, не страница входа
    assert not _deletes_session_cookie(response)                    # и cookie в браузере не трогает
    assert browser.get(reverse("web:users_list")).status_code == 200   # новая сессия жива


def test_logout_still_deletes_the_cookie():
    browser = Client()
    browser.force_login(User.objects.create_user("anna", password=STRONG))
    response = browser.post(reverse("web:logout"))
    assert response.status_code == 302 and _deletes_session_cookie(response)


def test_logout_without_session_does_not_lead_to_405():
    response = Client().post(reverse("web:logout"))
    assert response.status_code == 302 and "next=" not in response["Location"]


def test_sign_in_with_next_logout_goes_home():
    User.objects.create_user("anna", password=STRONG)
    response = Client().post(reverse("web:login") + "?next=" + reverse("web:logout"),
                             {"username": "anna", "password": STRONG})
    assert response.status_code == 302 and response["Location"] == reverse("web:main")


def test_out_of_date_login_form_shows_a_fresh_login_page():
    User.objects.create_user("anna", password=STRONG)
    browser = Client(enforce_csrf_checks=True)
    browser.get(reverse("web:login"))                               # cookie csrftoken
    response = browser.post(reverse("web:login"), {"username": "anna", "password": STRONG,
                                                   "csrfmiddlewaretoken": "x" * 64})   # токен со старой страницы
    html = response.content.decode("utf-8")
    assert response.status_code == 403 and "out of date" in html
    assert 'name="username"' in html and 'name="csrfmiddlewaretoken"' in html          # свежая форма входа


def test_out_of_date_form_elsewhere_explains_and_offers_reload():
    browser = Client(enforce_csrf_checks=True)
    browser.force_login(User.objects.create_superuser("root", password=STRONG))
    response = browser.post(reverse("web:settings"), {"csrfmiddlewaretoken": "x" * 64})
    html = response.content.decode("utf-8")
    assert response.status_code == 403 and "Reload the page" in html
