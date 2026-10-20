"""Приложение-аутентификатор (TOTP) — второй фактор входа (sopds_web_backend/totp.py).

Подключается в профиле (QR/ключ, подтверждение первым кодом), после этого
вход на сайт спрашивает код из приложения — у любого пользователя, который
его подключил. Для суперпользователей без приложения запасной вариант —
Telegram-код (test_admin_login_code.py). Тот же код второй раз не
принимается; отключить можно только с текущим кодом; потерян телефон —
`manage.py totp_reset <логин>` на сервере.
"""
import base64
import json

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.urls import reverse

import sopds_web_backend.login_codes as lc
import sopds_web_backend.telegram_users as tu
from sopds_web_backend import totp
from sopds_web_backend.models import TotpDevice

_STRONG = "Kx7#vQ2!mLp9"
_RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()  # RFC 6238, приложение B (SHA-1)


@pytest.mark.parametrize("unix_time, code", [
    (59, "287082"), (1111111109, "081804"), (1234567890, "005924"), (2000000000, "279037"),
])
def test_codes_match_rfc_6238(unix_time, code):
    assert totp.code_at(_RFC_SECRET, totp.current_step(unix_time)) == code


def test_window_and_replay():
    now = 1234567890
    step = totp.current_step(now)
    prev, nxt = totp.code_at(_RFC_SECRET, step - 1), totp.code_at(_RFC_SECRET, step + 1)
    assert totp.matching_step(_RFC_SECRET, prev, now=now) == step - 1   # часы телефона отстают на шаг
    assert totp.matching_step(_RFC_SECRET, nxt, now=now) == step + 1    # спешат на шаг
    assert totp.matching_step(_RFC_SECRET, totp.code_at(_RFC_SECRET, step - 2), now=now) is None
    assert totp.matching_step(_RFC_SECRET, prev, after_step=step - 1, now=now) is None  # уже принят


@pytest.fixture
def env(monkeypatch, settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    cfg = {"telegram_token": "", "telegram_proxy": "", "telegram_admin_chat": "", "admin_login_code": False}
    monkeypatch.setattr(lc, "_settings", lambda: cfg)
    monkeypatch.setattr(tu, "telegram", lambda service=None: (None, None))
    return cfg


def _now_code(user):
    device = TotpDevice.objects.get(user=user)
    return totp.code_at(device.secret, totp.current_step())


def _connect(client, user):
    client.force_login(user)
    page = client.post(reverse("web:profile"), {"action": "totp_start"}).content.decode()
    device = TotpDevice.objects.get(user=user)
    assert device.secret in page and "otpauth://totp/SOPDS:" in page and not device.confirmed
    client.post(reverse("web:profile"), {"action": "totp_confirm", "code": _now_code(user)})
    device.refresh_from_db()
    assert device.confirmed
    client.logout()


@pytest.mark.django_db
def test_connect_then_sign_in_with_app_code(client, env):
    user = User.objects.create_user("reader", password=_STRONG)
    _connect(client, user)

    resp = client.post(reverse("web:login") + "?next=/web/profile/", {"username": "reader", "password": _STRONG})
    assert resp["Location"] == reverse("web:login_code")
    assert "authenticator app" in client.get(reverse("web:login_code")).content.decode()
    assert client.get(reverse("web:profile")).status_code == 302  # без кода — ещё не вошёл

    TotpDevice.objects.filter(user=user).update(last_step=0)  # код подтверждения не мешает входу
    code = _now_code(user)
    resp = client.post(reverse("web:login_code"), {"code": code})
    assert resp["Location"] == "/web/profile/"
    assert client.get(reverse("web:profile")).status_code == 200

    # тот же код второй раз не проходит
    client.logout()
    client.post(reverse("web:login"), {"username": "reader", "password": _STRONG})
    assert "Wrong code" in client.post(reverse("web:login_code"), {"code": code}).content.decode()


@pytest.mark.django_db
def test_wrong_confirmation_code_keeps_it_off(client, env):
    user = User.objects.create_user("reader", password=_STRONG)
    client.force_login(user)
    client.post(reverse("web:profile"), {"action": "totp_start"})
    page = client.post(reverse("web:profile"), {"action": "totp_confirm", "code": "000000"}).content.decode()
    assert "Wrong code" in page and totp.device_for(user) is None
    client.post(reverse("web:profile"), {"action": "totp_cancel"})
    assert not TotpDevice.objects.filter(user=user).exists()


@pytest.mark.django_db
def test_admin_with_app_gets_no_telegram_code(client, env, monkeypatch):
    sent = []
    monkeypatch.setattr(lc, "_deliver", lambda *a: sent.append(a) or 1)
    env.update(admin_login_code=True, telegram_token="1:A", telegram_admin_chat="-1")
    admin = User.objects.create_user("boss", password=_STRONG, is_staff=True)
    _connect(client, admin)
    client.post(reverse("web:login"), {"username": "boss", "password": _STRONG})
    assert client.session[lc.SESSION_KEY]["method"] == lc.METHOD_TOTP
    assert sent == []


@pytest.mark.django_db
def test_turning_off_needs_a_current_code(client, env):
    user = User.objects.create_user("reader", password=_STRONG)
    _connect(client, user)
    client.force_login(user)
    TotpDevice.objects.filter(user=user).update(last_step=0)
    client.post(reverse("web:profile"), {"action": "totp_disable", "code": "000000"})
    assert totp.device_for(user) is not None
    client.post(reverse("web:profile"), {"action": "totp_disable", "code": _now_code(user)})
    assert totp.device_for(user) is None


@pytest.mark.django_db
def test_second_connect_is_refused_while_one_is_on(client, env):
    user = User.objects.create_user("reader", password=_STRONG)
    _connect(client, user)
    client.force_login(user)
    secret = TotpDevice.objects.get(user=user).secret
    page = client.post(reverse("web:profile"), {"action": "totp_start"}).content.decode()
    assert "already connected" in page and TotpDevice.objects.get(user=user).secret == secret


@pytest.mark.django_db
def test_reset_command_after_a_lost_phone(client, env):
    user = User.objects.create_user("reader", password=_STRONG)
    _connect(client, user)
    call_command("totp_reset", "reader")
    assert totp.device_for(user) is None
    resp = client.post(reverse("web:login"), {"username": "reader", "password": _STRONG})
    assert resp["Location"] == "/web/"  # снова только пароль


@pytest.mark.django_db
def test_app_is_enough_to_enable_the_second_factor(client, env, tmp_path, monkeypatch):
    import fb2parser_core.settings_manager as sm_module
    import opds_catalog.ratings_fetchers as fetchers
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"sopds": {"fb2toepub": "", "temp_dir": "", "language": "en-US"}}), encoding="utf-8")
    real = sm_module.SettingsManager
    monkeypatch.setattr(sm_module, "SettingsManager", lambda _path: real(str(path)))
    monkeypatch.setattr(fetchers, "wake_or_start", lambda name: None)
    monkeypatch.setattr(fetchers, "stop_fetcher", lambda name: None)

    root = User.objects.create_superuser("root", password=_STRONG)
    _connect(client, root)
    client.force_login(root)
    resp = client.post(reverse("web:settings"), {"language": "en-US", "maxitems": "20", "fb2toepub": "",
                                                 "temp_dir": "", "autosync_mode": "off", "admin_login_code": "on"})
    assert resp.status_code == 302
    assert json.loads(path.read_text(encoding="utf-8"))["autosync"]["admin_login_code"] is True


@pytest.mark.django_db
@pytest.mark.override_config(SOPDS_AUTH=True)
def test_reader_apps_need_no_code(client, env):
    """Читалки (OPDS, HTTP Basic) кода не вводят: второй фактор — только для
    входа на сайт. Сессия читалки и так действует лишь для /opds/."""
    from sopds_web_backend import opds_passwords
    admin = User.objects.create_user("boss", password=_STRONG, is_staff=True)
    _connect(client, admin)
    env.update(admin_login_code=True)
    for password in (_STRONG, opds_passwords.generate(admin)):
        reader = client.__class__()
        token = base64.b64encode(f"boss:{password}".encode()).decode()
        assert reader.get(reverse("opds:main"), HTTP_AUTHORIZATION=f"Basic {token}").status_code == 200
        assert reader.get(reverse("fb2parser:dashboard")).status_code == 302  # сайт — нет
