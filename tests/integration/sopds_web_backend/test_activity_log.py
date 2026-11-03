"""Журнал действий пользователей, шаг 1 — вход и безопасность
(docs/activity-log-design.md).

- каждое событие входа и безопасности пишется: кто, канал, устройство, IP;
- в журнал не попадают пароли (ни в подробности, ни в подписи);
- журнал видит только суперпользователь, в профиле — только свои входы;
- сбой записи не ломает вход;
- чистка по сроку: срок 0 — не чистится, чтение — своим сроком.
"""
import base64
import io
import json
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone

import sopds_web_backend.login_codes as lc
import sopds_web_backend.telegram_users as tu
from sopds_web_backend import activity, opds_passwords, totp
from sopds_web_backend.models import ActivityEvent, TelegramLink

PASSWORD = "Kx7#vQ2!mLp9"
NEW_PASSWORD = "Zq4$wRt8!nBv"
pytestmark = [pytest.mark.django_db, pytest.mark.override_config(SOPDS_AUTH=True)]


@pytest.fixture(autouse=True)
def env(monkeypatch, settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    cfg = {"telegram_token": "", "telegram_proxy": "", "telegram_admin_chat": "", "admin_login_code": False}
    monkeypatch.setattr(lc, "_settings", lambda: cfg)
    monkeypatch.setattr(tu, "telegram", lambda service=None: (object(), None))
    monkeypatch.setattr(tu, "bot_username", lambda client, service: "lib_bot")
    monkeypatch.setattr(tu, "kick_from_channel", lambda tg_id: True)


def _actions(user=None):
    qs = ActivityEvent.objects.order_by("id")
    if user is not None:
        qs = qs.filter(user=user)
    return [e.action for e in qs]


def _login(client, username, password=PASSWORD, **extra):
    return client.post(reverse("web:login"), {"username": username, "password": password}, **extra)


def _basic(username, password):
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"HTTP_AUTHORIZATION": f"Basic {token}"}


def test_sign_in_records_channel_device_and_ip():
    user = User.objects.create_user("anna", password=PASSWORD)
    client = Client(REMOTE_ADDR="203.0.113.7", HTTP_USER_AGENT="Mozilla/5.0 (Windows NT 10.0) Chrome/120")
    _login(client, "anna")
    assert _actions(user) == ["device_new", "login"]
    login = ActivityEvent.objects.get(action="login")
    assert (login.channel, login.ip, login.ok, login.username) == ("web", "203.0.113.7", True, "anna")
    assert login.device is not None and login.details == {"method": "password", "device": "Chrome, Windows"}

    client.post(reverse("web:logout"))
    assert _actions(user)[-1] == "logout"


def test_sign_in_from_installed_app_is_marked_as_app():
    User.objects.create_user("anna", password=PASSWORD)
    client = Client()
    client.cookies["sopds_app"] = "1"
    _login(client, "anna")
    assert ActivityEvent.objects.get(action="login").channel == "pwa"


def test_failed_sign_ins_keep_the_entered_name_never_the_password():
    user = User.objects.create_user("anna", password=PASSWORD)
    client = Client(REMOTE_ADDR="198.51.100.4")
    _login(client, "Anna", password="wrong-" + PASSWORD)
    _login(client, "nobody", password=PASSWORD)
    known, unknown = ActivityEvent.objects.order_by("id")
    assert (known.action, known.user, known.ok, known.details["reason"]) == ("login_failed", user, False,
                                                                              "wrong_password")
    assert (unknown.user, unknown.username, unknown.details["reason"]) == (None, "nobody", "unknown_user")
    assert unknown.ip == "198.51.100.4"


def test_lockout_is_recorded_once_per_attempt(settings):
    User.objects.create_user("anna", password=PASSWORD)
    client = Client()
    for _ in range(settings.AXES_FAILURE_LIMIT + 1):
        _login(client, "anna", password="wrong")
    actions = _actions()
    assert actions.count("login_failed") == settings.AXES_FAILURE_LIMIT - 1
    assert actions.count("locked_out") == 2                    # на пятой попытке и на шестой
    assert all(not e.ok for e in ActivityEvent.objects.all())


def test_profile_security_actions_are_recorded():
    user = User.objects.create_user("anna", password=PASSWORD)
    TelegramLink.objects.create(user=user, telegram_id=777, telegram_name="@anna_tg")
    client = Client()
    _login(client, "anna")
    profile = reverse("web:profile")
    client.post(profile, {"action": "opds_new", "confirm_password": PASSWORD})
    client.post(profile, {"action": "opds_revoke"})
    client.post(profile, {"action": "tg_unlink", "confirm_password": PASSWORD})
    client.post(profile, {"action": "totp_start", "confirm_password": PASSWORD})
    client.post(profile, {"action": "totp_confirm", "code": "000000"})
    secret = totp.pending_for(user).secret
    client.post(profile, {"action": "totp_confirm", "code": totp.code_at(secret, totp.current_step())})
    client.post(profile, {"action": "password", "old_password": PASSWORD, "new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD})
    assert _actions(user)[2:] == ["opds_password_created", "opds_password_revoked", "telegram_unlinked",
                                  "code_wrong", "totp_enabled", "password_changed"]
    assert ActivityEvent.objects.get(action="telegram_unlinked").details["telegram_name"] == "@anna_tg"


def test_superuser_actions_name_both_sides():
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    anna = User.objects.create_user("anna", password=PASSWORD)
    opds_passwords.generate(anna)
    client = Client()
    client.force_login(admin)
    client.post(reverse("web:user_edit", args=[anna.pk]), {"role": "user", "password": NEW_PASSWORD})
    events = {e.action: e for e in ActivityEvent.objects.all()}
    assert events["password_set"].user == anna and events["password_set"].actor == admin
    assert events["opds_password_revoked"].details["reason"] == "password_change"


def test_reader_app_sign_ins(client):
    user = User.objects.create_user("anna", password=PASSWORD)
    app_password = opds_passwords.generate(user)
    client.get(reverse("opds:main"), **_basic("anna", app_password))
    reader = Client()
    reader.get(reverse("opds:main"), **_basic("anna", app_password))
    Client().get(reverse("opds:main"), **_basic("anna", "WRONG-WRONG-WRONG-WRONG"))
    assert _actions(user) == ["opds_password_first_use", "login", "login", "login_failed"]
    assert {e.channel for e in ActivityEvent.objects.all()} == {"opds"}
    assert ActivityEvent.objects.filter(action="login").first().details == {"method": "app_password"}


def test_telegram_link_and_totp_reset_are_recorded():
    user = User.objects.create_user("anna", password=PASSWORD)
    link = tu.new_link_code(user)
    tu.link_by_code(link.code, {"id": 42, "username": "anna_tg"})
    totp.begin(user)
    device = totp.pending_for(user)
    totp.confirm(user, totp.code_at(device.secret, totp.current_step()))
    call_command("totp_reset", "anna", stdout=io.StringIO())
    linked, reset = ActivityEvent.objects.order_by("id")
    assert (linked.action, linked.channel) == ("telegram_linked", "telegram")
    assert (reset.action, reset.channel, reset.user) == ("totp_reset", "cli", user)


def test_no_password_or_code_gets_into_the_journal():
    """Все действия выше с известными паролями — ни одного в журнале."""
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    anna = User.objects.create_user("anna", password=PASSWORD)
    client = Client()
    _login(client, "anna", password="typo" + PASSWORD)
    _login(client, "anna")
    profile = reverse("web:profile")
    app_password = client.post(profile, {"action": "opds_new", "confirm_password": PASSWORD}).context[
        "opds_new_password"]
    Client().get(reverse("opds:main"), **_basic("anna", app_password))
    client.post(profile, {"action": "password", "old_password": PASSWORD, "new_password": NEW_PASSWORD,
                          "confirm_password": NEW_PASSWORD})
    boss = Client()
    boss.force_login(admin)
    boss.post(reverse("web:user_edit", args=[anna.pk]), {"role": "user", "password": PASSWORD + "B2"})
    dump = json.dumps([[e.username, e.actor_name, e.object_label, e.details] for e in ActivityEvent.objects.all()])
    for secret in (PASSWORD, NEW_PASSWORD, app_password, "typo"):
        assert secret not in dump
    assert ActivityEvent.objects.count() >= 6


def test_recording_failure_does_not_break_sign_in(monkeypatch):
    User.objects.create_user("anna", password=PASSWORD)

    def _broken(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ActivityEvent.objects, "create", _broken)
    response = _login(Client(), "anna")
    assert response.status_code == 302


def test_journal_is_for_superuser_only():
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    anna = User.objects.create_user("anna", password=PASSWORD)
    activity.record(None, "login_failed", user=anna, ok=False, reason="wrong_password")
    url = reverse("web:journal")
    assert Client().get(url).status_code == 302
    client = Client()
    client.force_login(anna)
    response = client.get(url)
    assert response.status_code in (302, 403) and b"Failed sign-in" not in response.content
    client.force_login(admin)
    page = client.get(url).content.decode()
    assert "Failed sign-in" in page and "anna" in page and 'id="s-pane-journal" class="s-tab-panel active"' in page


def test_journal_filters_and_more():
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    anna = User.objects.create_user("anna", password=PASSWORD)
    for _ in range(activity.PAGE_SIZE + 5):
        activity.record(None, "login", user=anna, channel="web", method="password")
    activity.record(None, "login_failed", user=admin, ok=False, reason="wrong_password")
    client = Client()
    client.force_login(admin)
    url = reverse("web:journal")
    rows = client.get(url, {"failed": "1"}, HTTP_HX_REQUEST="true").content.decode()
    assert rows.count('class="sp-j-row') == 1 and "Show more" not in rows
    first = client.get(url, {"user": anna.pk}, HTTP_HX_REQUEST="true").content.decode()
    assert first.count('class="sp-j-row') == activity.PAGE_SIZE and "Show more" in first
    assert first.count('class="sp-j-day"') == 1
    more = client.get(url, {"user": anna.pk, "page": 2, "after_day": timezone.localdate().isoformat()},
                      HTTP_HX_REQUEST="true").content.decode()
    assert more.count('class="sp-j-row') == 5 and 'class="sp-j-day"' not in more


def test_profile_shows_only_own_sign_ins():
    anna = User.objects.create_user("anna", password=PASSWORD)
    boris = User.objects.create_user("boris", password=PASSWORD)
    activity.record(None, "login", user=boris, channel="web", device=None, method="password")
    activity.record(None, "login_failed", user=anna, ok=False, reason="wrong_password", entered="anna")
    client = Client()
    client.force_login(anna)
    page = client.get(reverse("web:profile")).content.decode()
    assert "Recent sign-ins to your account" in page and "Failed sign-in" in page
    assert "boris" not in page


def test_retention_cleanup(override_config):
    anna = User.objects.create_user("anna", password=PASSWORD)
    old = timezone.now() - timedelta(days=100)
    activity.record(None, "login", user=anna, channel="web")
    ActivityEvent.objects.update(at=old)
    with override_config(SOPDS_ACTIVITY_KEEP_SECURITY_DAYS=0, SOPDS_ACTIVITY_KEEP_READING_DAYS=90):
        assert activity.cleanup() == {"security": 0, "reading": 0}
        assert ActivityEvent.objects.count() == 1                  # 0 — бессрочно
    with override_config(SOPDS_ACTIVITY_KEEP_SECURITY_DAYS=30, SOPDS_ACTIVITY_KEEP_READING_DAYS=90):
        assert activity.cleanup(dry_run=True) == {"security": 1, "reading": 0}
        assert activity.cleanup() == {"security": 1, "reading": 0}
    assert not ActivityEvent.objects.exists()


def test_retention_is_saved_from_the_journal_tab(monkeypatch, tmp_path):
    from sopds_web_backend import views
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(views, "_site_config_path", lambda: str(config))
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    client = Client()
    client.force_login(admin)
    url = reverse("web:journal_retention")
    assert client.post(url, {"activity_keep_security_days": "x", "activity_keep_reading_days": "30"}).status_code == 422
    response = client.post(url, {"activity_keep_security_days": "0", "activity_keep_reading_days": "30"})
    assert response.status_code == 302
    saved = json.loads(config.read_text(encoding="utf-8"))["sopds"]
    assert (saved["activity_keep_security_days"], saved["activity_keep_reading_days"]) == (0, 30)
