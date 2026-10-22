"""Защита учётной записи (разбор «что сделает злоумышленник с чужим паролем
или сессией», 2026-10-22):

- долговременный доступ — пароль для читалок, привязка Telegram, приложение
  второго фактора — только с паролем: украденной сессии мало, чтобы
  закрепиться в аккаунте;
- смена пароля (своя и суперпользователем) отзывает пароль для читалок и
  удаляет устройства, кроме текущего браузера;
- неверные коды второго фактора считаются на весь аккаунт: повторные входы
  с верным паролем не дают перебирать код;
- fb2parser и /admin/ — только суперпользователь (одного is_staff мало).
"""
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

import sopds_web_backend.login_codes as lc
import sopds_web_backend.telegram_users as tu
from sopds_web_backend import opds_passwords, totp
from sopds_web_backend.models import OpdsPassword, TelegramLink, TotpDevice, UserDevice

PASSWORD = "Kx7#vQ2!mLp9"
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def env(monkeypatch, settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    cfg = {"telegram_token": "", "telegram_proxy": "", "telegram_admin_chat": "", "admin_login_code": False}
    monkeypatch.setattr(lc, "_settings", lambda: cfg)
    monkeypatch.setattr(tu, "telegram", lambda service=None: (object(), None))
    monkeypatch.setattr(tu, "bot_username", lambda client, service: "lib_bot")
    return cfg


def _login(client, user, password=PASSWORD):
    return client.post(reverse("web:login"), {"username": user.username, "password": password})


def _profile(client, **data):
    return client.post(reverse("web:profile"), data).content.decode("utf-8")


@pytest.mark.parametrize("action", ["opds_new", "tg_code", "totp_start"])
def test_long_lived_access_needs_password(action):
    user = User.objects.create_user("reader", password=PASSWORD)
    client = Client()
    client.force_login(user)
    for password in ("", "wrong-password"):
        assert "Password is incorrect" in _profile(client, action=action, confirm_password=password)
    assert not OpdsPassword.objects.filter(user=user).exists()
    assert not TelegramLink.objects.filter(user=user).exclude(code="").exists()
    assert not TotpDevice.objects.filter(user=user).exists()
    _profile(client, action=action, confirm_password=PASSWORD)
    created = {"opds_new": OpdsPassword, "tg_code": TelegramLink, "totp_start": TotpDevice}[action]
    assert created.objects.filter(user=user).exists()


def test_unlinking_telegram_needs_password():
    user = User.objects.create_user("reader", password=PASSWORD)
    TelegramLink.objects.create(user=user, telegram_id=777, telegram_name="@reader_tg")
    client = Client()
    client.force_login(user)
    _profile(client, action="tg_unlink", confirm_password="")
    assert TelegramLink.objects.get(user=user).telegram_id == 777


def test_password_change_revokes_reader_password_and_other_devices():
    user = User.objects.create_user("reader", password=PASSWORD)
    other, mine = Client(), Client()
    _login(other, user)
    _login(mine, user)
    opds_passwords.generate(user)
    assert UserDevice.objects.filter(user=user).count() == 2
    new = "Zq8!wErT5yUi"
    _profile(mine, action="password", old_password=PASSWORD, new_password=new, confirm_password=new)
    assert not OpdsPassword.objects.filter(user=user).exists()
    [device] = UserDevice.objects.filter(user=user)                      # остался этот браузер
    assert mine.get(reverse("web:main")).status_code == 200
    assert other.get(reverse("web:main")).status_code == 302
    assert device.pk == mine.session.get("sopds_device_id")


def test_superuser_password_reset_revokes_everything_of_the_user():
    user = User.objects.create_user("reader", password=PASSWORD)
    _login(Client(), user)
    opds_passwords.generate(user)
    root = Client()
    root.force_login(User.objects.create_superuser("root", password=PASSWORD))
    root.post(reverse("web:user_edit", args=(user.pk,)), {"role": "user", "password": "Zq8!wErT5yUi"})
    assert not OpdsPassword.objects.filter(user=user).exists()
    assert not UserDevice.objects.filter(user=user).exists()
    assert root.get(reverse("web:users_list")).status_code == 200         # себе — не навредил


def test_superuser_changing_own_password_stays_signed_in():
    admin = User.objects.create_superuser("root", password=PASSWORD)
    root = Client()
    _login(root, admin)
    root.post(reverse("web:user_edit", args=(admin.pk,)), {"role": "superuser", "password": "Zq8!wErT5yUi"})
    assert root.get(reverse("web:users_list")).status_code == 200
    assert UserDevice.objects.filter(user=admin).count() == 1


def _with_app(user):
    from django.utils import timezone
    return TotpDevice.objects.create(user=user, secret=totp.new_secret(), confirmed=True, created=timezone.now())


def test_wrong_codes_counted_per_account_across_sign_ins():
    """Знающий пароль входит заново и получает новые 5 попыток — но счётчик
    на аккаунт после ACCOUNT_MAX_FAILURES закрывает ввод кода на час."""
    user = User.objects.create_user("reader", password=PASSWORD)
    device = _with_app(user)
    client = Client()
    wrong = 0
    while wrong < lc.ACCOUNT_MAX_FAILURES:
        assert _login(client, user).status_code == 302                    # на страницу кода
        for _ in range(min(lc.MAX_ATTEMPTS, lc.ACCOUNT_MAX_FAILURES - wrong)):
            client.post(reverse("web:login_code"), {"code": "000000"})
            wrong += 1
    assert lc.account_locked(user)
    page = _login(client, user)
    assert page.status_code == 403 and "Too many wrong codes for this account" in page.content.decode("utf-8")
    right = totp.code_at(device.secret, totp.current_step())
    assert client.post(reverse("web:login_code"), {"code": right}).status_code == 302   # ожидания нет — к паролю
    assert client.get(reverse("web:main")).status_code == 302                         # не вошёл


def test_profile_code_checks_share_the_account_counter():
    user = User.objects.create_user("reader", password=PASSWORD)
    device = _with_app(user)
    client = Client()
    client.force_login(user)
    for _ in range(lc.ACCOUNT_MAX_FAILURES):
        _profile(client, action="totp_disable", code="000000")
    right = totp.code_at(device.secret, totp.current_step())
    assert "Too many wrong codes for this account" in _profile(client, action="totp_disable", code=right)
    assert TotpDevice.objects.filter(user=user).exists()


def test_reader_password_only_for_accounts_with_second_factor(override_config):
    import base64
    user = User.objects.create_user("reader", password=PASSWORD)
    _with_app(user)
    reader_password = opds_passwords.generate(user)
    with override_config(SOPDS_AUTH=True):
        for password, status in ((PASSWORD, 401), (reader_password, 200)):
            auth = "Basic " + base64.b64encode(f"reader:{password}".encode()).decode()
            assert Client().get(reverse("opds_catalog:main"), HTTP_AUTHORIZATION=auth).status_code == status


def test_staff_flag_alone_opens_neither_fb2parser_nor_admin():
    staff = User.objects.create_user("staff", password=PASSWORD, is_staff=True)
    client = Client()
    client.force_login(staff)
    assert client.get(reverse("fb2parser:dashboard")).status_code == 302
    assert client.get("/admin/").status_code == 302
    root = Client()
    root.force_login(User.objects.create_superuser("root", password=PASSWORD))
    assert root.get(reverse("fb2parser:dashboard")).status_code == 200
    assert root.get("/admin/").status_code == 200
