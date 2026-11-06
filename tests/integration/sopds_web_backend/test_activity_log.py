"""Журнал действий пользователей (docs/activity-log-design.md): шаг 1 — вход
и безопасность, шаг 2 — чтение и скачивание.

- каждое событие входа и безопасности пишется: кто, канал, устройство, IP;
- в журнал не попадают пароли (ни в подробности, ни в подписи);
- журнал видит только суперпользователь, в профиле — только свои входы;
- сбой записи не ломает вход;
- чистка по сроку: срок 0 — не чистится, чтение — своим сроком;
- открытие книги — одна запись на книгу в день, дочитал — один раз,
  скачивание — каждое, с форматом; канал скачивания с сайта — сайт, хотя
  адрес /opds/download/; «Кто читал» в карточке — только суперпользователю.
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
from opds_catalog import dl
from opds_catalog.models import Author, Book, Catalog, bauthor, bookshelf
from sopds_web_backend import activity, opds_passwords, totp
from sopds_web_backend.models import ActivityEvent, TelegramLink
from tests.fixtures.fixture_django import publish_books

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


# ── Шаг 2: чтение и скачивание ───────────────────────────────────────────────

_FB2 = (b'<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
        b"<description><title-info><book-title>Night</book-title></title-info></description>"
        b"<body><section><p>Text</p></section></body></FictionBook>")


@pytest.fixture
def book(monkeypatch):
    cat = Catalog.objects.create(parent=None, cat_name=".", path=".", cat_type=0)
    b = Book.objects.create(filename="Лукьяненко Сергей - Ночной Дозор.fb2", path=".", filesize=len(_FB2),
                            format="fb2", cat_type=0, docdate="2020", lang="ru", title="Ночной Дозор",
                            search_title="НОЧНОЙ ДОЗОР", annotation="", avail=2, catalog=cat)
    bauthor.objects.create(book=b, author=Author.objects.create(full_name="Лукьяненко Сергей",
                                                               search_full_name="ЛУКЬЯНЕНКО СЕРГЕЙ"))
    publish_books(b)
    monkeypatch.setattr(dl, "getFileData", lambda book: io.BytesIO(_FB2))
    return b


def _progress(client, book, **data):
    return client.post(reverse("opds:save_progress", args=[book.pk]), json.dumps(data),
                       content_type="application/json")


def test_opening_a_book_is_recorded_once_a_day(book):
    anna = User.objects.create_user("anna", password=PASSWORD)
    client = Client()
    client.force_login(anna)
    for _ in range(3):
        assert client.get(reverse("web:read", args=[book.pk])).status_code == 200
    [opened] = ActivityEvent.objects.filter(action="book_opened")
    assert (opened.user, opened.channel, opened.category, opened.ip) == (anna, "web", "reading", None)
    assert (opened.object_type, opened.object_id, opened.object_label) == (
        "book", str(book.pk), "Лукьяненко Сергей - Ночной Дозор")
    ActivityEvent.objects.update(at=timezone.now() - timedelta(days=1))
    client.get(reverse("web:read", args=[book.pk]))
    assert ActivityEvent.objects.filter(action="book_opened").count() == 2     # новый день — новая запись


def test_finished_once_and_offline_saves_keep_the_place(book):
    anna = User.objects.create_user("anna", password=PASSWORD)
    client = Client()
    client.force_login(anna)
    _progress(client, book, anchor_id="p-1", percent=50)
    _progress(client, book, anchor_id="p-9", percent=99)
    _progress(client, book, anchor_id="p-9", percent=100)
    _progress(client, book, offline="saved")
    _progress(client, book, offline="removed")
    assert _progress(client, book, offline="bogus").status_code == 400
    reading = ActivityEvent.objects.filter(user=anna, category="reading").order_by("id")
    assert [e.action for e in reading] == ["book_finished", "book_saved_offline", "book_removed_offline"]
    entry = bookshelf.objects.get(user=anna, book=book)
    assert (entry.anchor_id, entry.progress_percent) == ("p-9", 100.0)       # место чтения не тронуто


def test_downloads_record_format_and_channel(book):
    anna = User.objects.create_user("anna", password=PASSWORD)
    site = Client()
    site.force_login(anna)
    site.get(reverse("opds:download", args=[book.pk, 0]))
    site.get(reverse("opds:download", args=[book.pk, 1]))
    app_password = opds_passwords.generate(anna)
    Client().get(reverse("opds:download", args=[book.pk, 0]), **_basic("anna", app_password))
    downloads = [(e.channel, e.details["format"]) for e in ActivityEvent.objects.filter(action="book_downloaded")
                 .order_by("id")]
    assert downloads == [("web", "fb2"), ("web", "fb2.zip"), ("opds", "fb2")]


def test_reading_without_sign_in_is_not_recorded(book, override_config):
    with override_config(SOPDS_AUTH=False):
        assert Client().get(reverse("opds:download", args=[book.pk, 0])).status_code == 200
    assert not ActivityEvent.objects.exists()


def test_book_card_shows_readers_only_to_superuser(book):
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    anna = User.objects.create_user("anna", password=PASSWORD)
    activity.record(None, "book_downloaded", user=anna, channel="opds", obj=activity.book_obj(book), format="epub")
    card = reverse("web:book_card", args=[book.pk])
    client = Client()
    client.force_login(anna)
    page = client.get(card).content.decode()
    assert "Who read and downloaded" not in page
    client.force_login(admin)
    page = client.get(card).content.decode()
    assert "Who read and downloaded" in page and "anna" in page and "(epub)" in page
    rows = client.get(reverse("web:journal"), {"book": book.pk, "period": "all"}, HTTP_HX_REQUEST="true")
    assert rows.content.decode().count('class="sp-j-row') == 1


# ── Шаг 3: действия суперпользователя ────────────────────────────────────────

@pytest.fixture
def boss(monkeypatch, tmp_path):
    from sopds_web_backend import views
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(views, "_site_config_path", lambda: str(config))
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    client = Client()
    client.force_login(admin)
    return admin, client


def test_settings_changes_hide_secrets():
    diff = activity.settings_changes(
        {"sopds": {"maxitems": 60}, "autosync": {"telegram_token": "1:OLD", "telegram_proxy": "u:p@h"}},
        {"sopds": {"maxitems": 30}, "autosync": {"telegram_token": "1:NEW", "telegram_proxy": "u:q@h"}})
    assert diff == {"changes": ["maxitems: 60 → 30"], "hidden_values": ["telegram_proxy", "telegram_token"]}


def test_settings_retention_and_maintenance_are_recorded(boss, override_config):
    admin, client = boss
    client.post(reverse("web:journal_retention"), {"activity_keep_security_days": "100",
                                                   "activity_keep_reading_days": "90"})
    event = ActivityEvent.objects.get(action="settings_changed")
    assert (event.user, event.category, event.details["tab"]) == (admin, "site", "journal")
    assert event.details["changes"] == ["activity_keep_security_days: 365 → 100"]
    with override_config(SOPDS_MAINTENANCE=False, SOPDS_MAINTENANCE_MESSAGE=""):
        call_command("maintenance", "on", stdout=io.StringIO())
        call_command("maintenance", "off", stdout=io.StringIO())
    on, off = ActivityEvent.objects.filter(action__startswith="maintenance").order_by("id")
    assert (on.action, on.channel, on.actor_name, off.action) == ("maintenance_on", "cli", "manage.py",
                                                                  "maintenance_off")


def test_user_management_names_who_and_whom(boss):
    admin, client = boss
    client.post(reverse("web:user_create"), {"username": "anna", "password": PASSWORD, "role": "user"})
    anna = User.objects.get(username="anna")
    client.post(reverse("web:user_edit", args=[anna.pk]), {"role": "superuser", "max_devices": "3"})
    client.post(reverse("web:user_delete", args=[anna.pk]))
    events = list(ActivityEvent.objects.filter(category="accounts").order_by("id"))
    assert [e.action for e in events] == ["user_created", "device_limit_changed", "role_changed", "user_deleted"]
    assert all(e.actor == admin and e.username == "anna" for e in events)
    assert events[1].details["limit"] == "default → 3" and events[2].details["role"] == "user → superuser"
    assert events[-1].user is None                            # удалён — имя осталось копией


def test_news_actions_are_recorded(boss):
    admin, client = boss
    client.post(reverse("web:news_create"), {"title": "Новые книги", "text": "…"})
    news_id = ActivityEvent.objects.get(action="news_published").object_id
    client.post(reverse("web:news_action", args=[news_id, "pin"]))
    client.post(reverse("web:news_action", args=[news_id, "hide"]))
    client.post(reverse("web:news_action", args=[news_id, "hide"]))
    assert [e.action for e in ActivityEvent.objects.filter(category="site").order_by("id")] == [
        "news_published", "news_pinned", "news_hidden", "news_shown"]
    assert ActivityEvent.objects.filter(object_type="news", object_label="Новые книги").count() == 4


def test_admin_site_changes_are_recorded(boss):
    from django.contrib.admin.models import CHANGE, LogEntry
    from django.contrib.contenttypes.models import ContentType
    admin, _client = boss
    anna = User.objects.create_user("anna", password=PASSWORD)
    LogEntry.objects.create(user=admin, content_type=ContentType.objects.get_for_model(User),
                            object_id=str(anna.pk), object_repr="anna", action_flag=CHANGE,
                            change_message='[{"changed": {"fields": ["Active"]}}]')
    event = ActivityEvent.objects.get(action="admin_user_changed")
    assert (event.user, event.actor, event.details["admin_action"]) == (anna, admin, "changed")
    assert "Active" in event.details["message"]


def test_users_tab_links_each_user_to_the_journal(boss):
    admin, client = boss
    anna = User.objects.create_user("anna", password=PASSWORD)
    page = client.get(reverse("web:users_list")).content.decode()
    assert f'{reverse("web:journal")}?user={anna.pk}&amp;period=all' in page


# ── Шаг 4: fb2parser, система, метка в шапке, CSV ────────────────────────────

def test_fb2parser_operation_records_who_started_it_and_results():
    from fb2parser_web.views import _journal, activity_totals
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    _journal("library_synced", {"started_by": admin.pk}, totals=activity_totals({"moved": 3, "errors": 0}))
    _journal("library_compressed", {}, ok=False, error="disk full")
    synced, compressed = ActivityEvent.objects.order_by("id")
    assert (synced.user, synced.channel, synced.details["totals"]) == (admin, "fb2parser", ["moved: 3"])
    assert (compressed.user, compressed.ok, compressed.details["error"]) == (None, False, "disk full")
    assert activity.decorate(compressed).who == "system" and activity.decorate(synced).who == "admin"


def test_deletions_genre_edits_and_restart_are_recorded(monkeypatch):
    from django.http import JsonResponse
    from django.test import RequestFactory

    from fb2parser_web import views as fv
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    request = RequestFactory().post("/fb2parser/genres/add/", {"name": "Кинороман", "parent": "Проза"})
    request.user, request.session = admin, {}
    fv._journal_deleted(request, "duplicates", ["a.fb2", "b.fb2"], [])
    fv._journal_genres(lambda r: JsonResponse({"ok": True}))(request)
    fv._journal_genres(lambda r: JsonResponse({"ok": False}))(request)          # не удалось — не пишется
    monkeypatch.setattr("pathlib.Path.touch", lambda self: None)
    monkeypatch.setattr("threading.Thread", lambda target=None, daemon=None, **kw: type(
        "T", (), {"start": lambda self: None})())
    fv.server_restart(request)
    events = list(ActivityEvent.objects.order_by("id"))
    assert [e.action for e in events] == ["files_deleted", "genres_edited", "server_restarted"]
    assert events[0].details == {"what": "duplicates", "totals": ["deleted: 2"]}
    assert events[1].details["change"] == "name=Кинороман; parent=Проза"


def test_scans_autosync_and_ratings_outage_are_system_events(monkeypatch):
    from types import SimpleNamespace

    from opds_catalog import autosync_hooks, ratings_fetchers
    from sopds_web_backend.views import _record_scan
    _record_scan(SimpleNamespace(books_added=5, books_deleted=1, bad_books=0), "schedule_run")
    result = SimpleNamespace(status="ok", mode="auto", moved=["x"], pending=2, kept=0, removed=0, error="")
    service = SimpleNamespace(run=lambda: result)
    monkeypatch.setattr("fb2parser_web.fb2parser_bridge.get_autosync_service", lambda: service)
    autosync_hooks.run_autosync()
    result.moved = []
    autosync_hooks.run_autosync()                                   # пустой прогон — не пишется
    monkeypatch.setattr(ratings_fetchers, "sleep_or_stop", lambda source, seconds: None)
    monkeypatch.setattr("opds_catalog.ratings_progress.set_progress", lambda *a, **k: None)
    book = SimpleNamespace(id=1, title="x")
    ratings_fetchers.pause_for_outage("samlib", book, lambda m: None)
    ratings_fetchers.pause_for_outage("samlib", book, lambda m: None)   # раз в сутки на источник
    events = list(ActivityEvent.objects.order_by("id"))
    assert [(e.action, e.category, e.channel) for e in events] == [
        ("scan_finished", "system", "system"), ("autosync_finished", "system", "system"),
        ("ratings_site_down", "system", "system")]
    assert events[0].details["totals"] == ["added: 5", "deleted: 1"]
    assert events[1].details["totals"] == ["moved: 1", "pending: 2"]


def test_header_alert_counts_lockouts_and_unknown_addresses(monkeypatch):
    from django.core.cache import cache
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    anna = User.objects.create_user("anna", password=PASSWORD)
    known = Client(REMOTE_ADDR="10.0.0.1")
    _login(known, "anna")
    _login(known, "anna", password="wrong")                    # адрес знакомый — не тревога
    _login(Client(REMOTE_ADDR="10.9.9.9"), "anna", password="wrong")
    _login(Client(REMOTE_ADDR="10.9.9.9"), "nobody", password="wrong")
    cache.delete(activity.ALERT_CACHE_KEY)
    assert activity.security_alerts() == 2
    user = Client()
    user.force_login(anna)
    assert "Security:" not in user.get(reverse("web:main")).content.decode()
    boss = Client()
    boss.force_login(admin)
    assert "⚠ Security: 2" in boss.get(reverse("web:main")).content.decode()


def test_csv_export_follows_filters_and_is_superuser_only():
    admin = User.objects.create_superuser("admin", password=PASSWORD + "Aa1")
    anna = User.objects.create_user("anna", password=PASSWORD)
    activity.record(None, "login", user=anna, channel="web", method="password")
    activity.record(None, "login_failed", user=anna, ok=False, channel="opds", reason="wrong_password")
    url = reverse("web:journal")
    client = Client()
    client.force_login(anna)
    assert client.get(url, {"format": "csv"}).status_code in (302, 403)
    client.force_login(admin)
    response = client.get(url, {"format": "csv", "failed": "1", "period": "all"})
    assert response["Content-Type"].startswith("text/csv")
    text = b"".join(response.streaming_content).decode("utf-8-sig")
    lines = text.strip().splitlines()
    assert lines[0].startswith("time,user,actor,category,action") and len(lines) == 2
    assert ",anna," in lines[1] and "Failed sign-in" in lines[1] and "wrong_password" in lines[1]


def test_detail_codes_are_shown_as_text_of_their_own_key():
    """«telegram» — и способ входа, и вкладка настроек: у каждого ключа свой текст."""
    from django.utils import translation
    login = activity.record(None, "login", user=None, channel="web", method="telegram")
    saved = activity.record(None, "settings_changed", user=None, channel="web", tab="telegram",
                            changes=["telegram_channel: a → b"])
    with translation.override("ru"):
        assert dict(activity.detail_rows(login))["Как"] == "пароль и код из Telegram"
        rows = {str(k): str(v) for k, v in activity.detail_rows(saved)}
        assert rows["Вкладка"] == "Telegram" and rows["Изменения"] == "telegram_channel: a → b"
