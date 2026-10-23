"""Требования к паролям и смена пароля при первом входе
(sopds_web_backend/password_rules.py):

- суперпользователь — не меньше 12 символов, пользователь — 8; обязательны
  строчная и заглавная латинская буква, цифра, спецсимвол; буквы — только
  латинские;
- генератор (static/js/password-generator.js) выдаёт пароли по этим правилам;
- созданный суперпользователем пользователь, пароль от суперпользователя или
  вход с паролем не по требованиям — смена пароля, остальной сайт закрыт,
  читалки с основным паролем не пускают.
"""
import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from sopds_web_backend.password_rules import must_change, problems, set_must_change

pytestmark = pytest.mark.django_db
STRONG = "Kx7#vQ2!mLp9"          # 12 символов, все группы — годится и суперпользователю
USER_OK = "Ab3$wxyz"             # 8 символов — только пользователю
JS = Path(__file__).resolve().parents[3] / "src" / "sopds_web_backend" / "static" / "js" / "password-generator.js"


@pytest.fixture(autouse=True)
def plain_static(settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


class _Role:
    def __init__(self, superuser):
        self.is_superuser = superuser


@pytest.mark.parametrize("password, superuser, ok", [
    (USER_OK, False, True),
    (USER_OK, True, False),                 # суперпользователю — 12
    (STRONG, True, True),
    ("ab3$wxyz", False, False),             # нет заглавной
    ("AB3$WXYZ", False, False),             # нет строчной
    ("Abc$wxyz", False, False),             # нет цифры
    ("Ab3dwxyz", False, False),             # нет спецсимвола
    ("Ab3 wxyz", False, False),             # пробел — не спецсимвол
    ("Пароль3$Ab", False, False),           # кириллица — нельзя
    ("Ab3$wxyzé", False, False),            # и другие алфавиты
])
def test_rules(password, superuser, ok):
    assert (problems(password, _Role(superuser)) == []) is ok


def test_cyrillic_is_named_in_the_message():
    assert any("Latin letters only" in p for p in problems("Пароль3$Ab", _Role(False)))


@pytest.mark.skipif(shutil.which("node") is None, reason="нет Node.js")
def test_generator_follows_the_rules():
    script = JS.read_text(encoding="utf-8") + """
        const out = [];
        for (let i = 0; i < 300; i++) out.push(window.sopdsGeneratePassword(16));
        console.log(JSON.stringify(out));"""
    run = subprocess.run(["node", "-e", "globalThis.window = globalThis;" + script],
                         capture_output=True, text=True, encoding="utf-8", check=True)
    passwords = json.loads(run.stdout)
    assert len(set(passwords)) == 300
    for pw in passwords:
        assert len(pw) == 16 and problems(pw, _Role(True)) == [], pw
        assert not set(pw) & set("lI1O0 ")                     # без похожих символов и пробелов


def _root():
    client = Client()
    client.force_login(User.objects.create_superuser("root", password=STRONG))
    return client


def test_create_user_checks_rules_by_role_and_requires_change():
    root = _root()
    response = root.post(reverse("web:user_create"), {"username": "boss", "password": USER_OK, "role": "superuser"})
    assert response.status_code == 422 and not User.objects.filter(username="boss").exists()
    root.post(reverse("web:user_create"), {"username": "boss", "password": STRONG, "role": "superuser"})
    root.post(reverse("web:user_create"), {"username": "anna", "password": USER_OK, "role": "user"})
    assert must_change(User.objects.get(username="boss")) and must_change(User.objects.get(username="anna"))
    page = root.get(reverse("web:users_list")).content.decode("utf-8")
    assert "password-generator.js" in page and 'data-targets="pw-create"' in page


def _signed_in(username, password):
    client = Client()
    response = client.post(reverse("web:login"), {"username": username, "password": password})
    assert response.status_code == 302
    return client


def test_first_sign_in_leads_to_password_change_and_nowhere_else():
    anna = User.objects.create_user("anna", password=USER_OK)
    set_must_change(anna, True)
    client = _signed_in("anna", USER_OK)
    for url in (reverse("web:main"), reverse("web:profile"), reverse("web:searchbooks") + "?searchtype=m&searchterms=x"):
        response = client.get(url)
        assert response.status_code == 302 and response["Location"] == reverse("web:password_change_required")
    htmx = client.get(reverse("web:main"), HTTP_HX_REQUEST="true")
    assert htmx["HX-Redirect"] == reverse("web:password_change_required")
    page = client.get(reverse("web:password_change_required")).content.decode("utf-8")
    assert "Generate a password" in page and "at least 8 characters" in page


def test_password_change_page_checks_and_releases():
    anna = User.objects.create_user("anna", password=USER_OK)
    set_must_change(anna, True)
    client = _signed_in("anna", USER_OK)
    url = reverse("web:password_change_required")
    for data, error in (({"old_password": "wrong", "new_password": "Zq8!wErT", "confirm_password": "Zq8!wErT"},
                         "Current password is incorrect"),
                        ({"old_password": USER_OK, "new_password": USER_OK, "confirm_password": USER_OK},
                         "must differ"),
                        ({"old_password": USER_OK, "new_password": "zq8wert", "confirm_password": "zq8wert"},
                         "The password must contain")):
        assert error in client.post(url, data).content.decode("utf-8")
    response = client.post(url, {"old_password": USER_OK, "new_password": "Zq8!wErT", "confirm_password": "Zq8!wErT"})
    assert response.status_code == 302 and response["Location"] == reverse("web:main")
    assert not must_change(User.objects.get(pk=anna.pk)) and client.get(reverse("web:main")).status_code == 200
    assert client.get(url).status_code == 302                       # больше не нужна — на главную


def test_weak_existing_password_is_changed_at_sign_in():
    User.objects.create_user("old", password="password1")           # заведён до требований
    client = _signed_in("old", "password1")
    assert must_change(User.objects.get(username="old"))
    assert client.get(reverse("web:main"))["Location"] == reverse("web:password_change_required")


def test_promoted_to_superuser_with_short_password_changes_it():
    User.objects.create_superuser("boss", password=USER_OK)         # 8 символов — мало суперпользователю
    _signed_in("boss", USER_OK)
    assert must_change(User.objects.get(username="boss"))


def test_strong_password_signs_in_normally():
    User.objects.create_user("anna", password=STRONG)
    client = _signed_in("anna", STRONG)
    assert client.get(reverse("web:main")).status_code == 200 and not must_change(User.objects.get(username="anna"))


def test_superuser_reset_is_temporary_own_change_is_not():
    root = _root()
    anna = User.objects.create_user("anna", password=STRONG)
    root.post(reverse("web:user_edit", args=(anna.pk,)), {"role": "user", "password": "Zq8!wErT"})
    assert must_change(User.objects.get(pk=anna.pk))
    me = User.objects.get(username="root")
    root.post(reverse("web:user_edit", args=(me.pk,)), {"role": "superuser", "password": "Zq8!wErT5yUi"})
    me = User.objects.get(pk=me.pk)
    assert me.check_password("Zq8!wErT5yUi") and not must_change(me)        # свой пароль — не временный
    assert root.get(reverse("web:users_list")).status_code == 200
    response = root.post(reverse("web:user_edit", args=(anna.pk,)), {"role": "superuser", "password": "Zq8!wErT"})
    assert response.status_code == 422                             # для новой роли — 12 символов


def test_profile_change_releases_the_mark():
    anna = User.objects.create_user("anna", password=STRONG)
    client = Client()
    client.force_login(anna)
    page = client.get(reverse("web:profile")).content.decode("utf-8")
    assert 'data-targets="profile-pw-new profile-pw-confirm"' in page
    set_must_change(anna, True)
    set_must_change(anna, False)                                   # профиль доступен только без отметки
    client.post(reverse("web:profile"), {"action": "password", "old_password": STRONG,
                                         "new_password": "Zq8!wErT5yUi", "confirm_password": "Zq8!wErT5yUi"})
    assert User.objects.get(pk=anna.pk).check_password("Zq8!wErT5yUi")
    client.post(reverse("web:profile"), {"action": "password", "old_password": "Zq8!wErT5yUi",
                                         "new_password": "weakpass", "confirm_password": "weakpass"})
    assert User.objects.get(pk=anna.pk).check_password("Zq8!wErT5yUi")   # слабый — не принят


def test_readers_wait_until_the_password_is_changed(override_config):
    anna = User.objects.create_user("anna", password=STRONG)
    set_must_change(anna, True)
    auth = "Basic " + base64.b64encode(f"anna:{STRONG}".encode()).decode()
    with override_config(SOPDS_AUTH=True):
        assert Client().get(reverse("opds_catalog:main"), HTTP_AUTHORIZATION=auth).status_code == 401
        set_must_change(anna, False)
        assert Client().get(reverse("opds_catalog:main"), HTTP_AUTHORIZATION=auth).status_code == 200
