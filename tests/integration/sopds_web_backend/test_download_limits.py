"""Лимит книг — защита от выкачивания библиотеки (docs/download-limits-design.md).

- считаются разные книги за месяц, всеми путями выдачи текста: скачивание,
  zip, конвертация, читалка сайта и OPDS, Telegram; та же книга повторно —
  бесплатно;
- лимиты на сутки и на месяц — личные или по умолчанию из настроек, 0 — без
  ограничений; суперпользователь не ограничен;
- не больше RATE_BOOKS новых книг за RATE_WINDOW;
- отказ: сайт — окно (download_check) или страница 429, OPDS — текст 429,
  бот — сообщение; в журнал — раз в день на вид лимита; N дней подряд —
  суперпользователям в Telegram.
"""
import io
from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from opds_catalog import dl
from opds_catalog.models import Book, Catalog
from sopds_web_backend import download_limits as limits
from sopds_web_backend.models import ActivityEvent, BookTake, UserProfile
from tests.fixtures.fixture_django import publish_books

pytestmark = pytest.mark.django_db

_FB2 = (b'<?xml version="1.0" encoding="utf-8"?><FictionBook><description><title-info>'
        b'<book-title>T</book-title></title-info></description><body><section><p>x</p></section></body>'
        b'</FictionBook>')


@pytest.fixture(autouse=True)
def _clean_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def books(monkeypatch):
    cat = Catalog.objects.create(parent=None, cat_name=".", path=".", cat_type=0)
    items = [Book.objects.create(filename=f"b{i}.fb2", path=".", filesize=len(_FB2), format="fb2", cat_type=0,
                                 docdate="2020", lang="ru", title=f"Книга {i}", search_title=f"КНИГА {i}",
                                 annotation="", avail=2, catalog=cat) for i in range(14)]
    publish_books(*items)
    monkeypatch.setattr(dl, "getFileData", lambda b: io.BytesIO(_FB2))
    monkeypatch.setattr(dl, "original_file", lambda b: (b.filename, _FB2))
    return items


def _reader(day=None, month=None, name="reader"):
    user = User.objects.create_user(name)
    UserProfile.objects.update_or_create(user=user, defaults={"downloads_per_day": day,
                                                              "downloads_per_month": month})
    client = Client()
    client.force_login(user)
    return user, client


def _take(client, book, how="download"):
    url = {"download": reverse("opds:download", args=[book.pk, 0]),
           "zip": reverse("opds:download", args=[book.pk, 1]),
           "read": reverse("web:read", args=[book.pk]),
           "view": reverse("opds:view_html", args=[book.pk]),
           "convert": reverse("opds:convert", args=[book.pk, "epub"])}[how]
    return client.get(url)


def _check(client, book_id):
    return client.get(reverse("web:download_check", args=[book_id])).json()


def test_day_limit_counts_different_books_by_every_way(books):
    user, client = _reader(day=3, month=0)
    assert _take(client, books[0]).status_code == 200
    assert _take(client, books[1], "read").status_code == 200
    assert _take(client, books[2], "view").status_code == 200
    for how in ("download", "zip", "read", "view"):                   # уже взятые — без ограничений
        assert _take(client, books[0], how).status_code == 200
    for how in ("download", "zip", "read", "view", "convert"):        # новая — отказ, всеми путями
        response = _take(client, books[3], how)
        assert response.status_code == 429, how
        assert int(response["Retry-After"]) > 0
    assert BookTake.objects.filter(user=user).count() == 3
    assert limits.usage(user) == {"day": 3, "month": 3}


def test_failed_conversion_does_not_spend_the_limit(books):
    user, client = _reader(day=3, month=0)
    assert _take(client, books[0], "convert").status_code == 404     # конвертер не настроен
    assert not BookTake.objects.filter(user=user).exists()


def test_month_limit_and_new_day(books):
    user, client = _reader(day=2, month=3)
    earlier = timezone.now() - timedelta(days=2)
    month = limits._month_start()
    for book in books[:2]:                                            # вчерашние — в лимите месяца
        BookTake.objects.create(user=user, book_id=book.pk, month=month, taken_at=earlier)
    assert _take(client, books[2]).status_code == 200                 # сутки — с нуля
    refused = limits.refusal(user, books[3])
    assert refused.kind == limits.KIND_MONTH and refused.limit == 3
    assert _take(client, books[3]).status_code == 429
    assert _take(client, books[0]).status_code == 200                 # уже взятая в этом месяце


def test_previous_month_does_not_count(books):
    user, client = _reader(day=1, month=1)
    last_month = limits._month_start() - timedelta(days=1)
    BookTake.objects.create(user=user, book_id=books[0].pk, month=last_month.replace(day=1),
                            taken_at=timezone.now() - timedelta(days=40))
    assert _take(client, books[1]).status_code == 200


def test_rate_limit(books):
    user, client = _reader(day=0, month=0)
    for book in books[:limits.RATE_BOOKS]:
        assert _take(client, book).status_code == 200
    refused = limits.refusal(user, books[limits.RATE_BOOKS])
    assert refused.kind == limits.KIND_RATE
    assert _take(client, books[limits.RATE_BOOKS]).status_code == 429
    BookTake.objects.filter(user=user).update(taken_at=timezone.now() - limits.RATE_WINDOW - timedelta(seconds=1))
    assert _take(client, books[limits.RATE_BOOKS]).status_code == 200


def test_defaults_from_settings_and_personal_override(books, override_config):
    with override_config(SOPDS_DOWNLOADS_PER_DAY=1, SOPDS_DOWNLOADS_PER_MONTH=0):
        user, client = _reader()                                      # личных нет — по умолчанию
        assert _take(client, books[0]).status_code == 200
        assert _take(client, books[1]).status_code == 429
        UserProfile.objects.filter(user=user).update(downloads_per_day=0)   # 0 — без ограничений
        assert _take(client, books[1]).status_code == 200


def test_superuser_is_not_limited(books, override_config):
    with override_config(SOPDS_DOWNLOADS_PER_DAY=1, SOPDS_DOWNLOADS_PER_MONTH=1):
        admin = User.objects.create_user("root", is_superuser=True, is_staff=True)
        client = Client()
        client.force_login(admin)
        for book in books[:limits.RATE_BOOKS + 2]:
            assert _take(client, book).status_code == 200
        assert not BookTake.objects.filter(user=admin).exists()


def test_reader_app_gets_text_and_site_gets_page(books):
    import base64
    user, client = _reader(day=1, month=0)
    user.set_password("Reader-Pass-123!")
    user.save()
    client.force_login(user)
    _take(client, books[0])
    page = _take(client, books[1])
    assert page.status_code == 429 and page["Content-Type"].startswith("text/html")
    assert "sopds-limit-modal" in page.content.decode("utf-8")       # страница сайта
    basic = base64.b64encode(b"reader:Reader-Pass-123!").decode()
    app = Client(HTTP_AUTHORIZATION=f"Basic {basic}")                 # читалка OPDS
    text = _take(app, books[1])
    assert text.status_code == 429 and text["Content-Type"].startswith("text/plain")


def test_download_check_asks_without_taking(books):
    user, client = _reader(day=1, month=0)
    assert _check(client, books[0].pk) == {"ok": True}
    assert not BookTake.objects.filter(user=user).exists()            # проверка ничего не тратит
    _take(client, books[0])
    assert _check(client, books[0].pk) == {"ok": True}                # уже взята
    refused = _check(client, books[1].pk)
    assert refused["ok"] is False and refused["message"]
    assert _check(client, 999_999) == {"ok": True}                    # нет такой — решит сам адрес (404)
    assert Client().get(reverse("web:download_check", args=[books[1].pk])).json() == {"ok": True}


def test_refusals_are_journaled_once_a_day_per_kind(books):
    from sopds_web_backend import activity
    user, client = _reader(day=1, month=0)
    _take(client, books[0])
    _take(client, books[1])
    _take(client, books[2], "read")
    _check(client, books[3].pk)
    [event] = ActivityEvent.objects.filter(action="download_limit_reached", user=user)
    assert event.ok is False and event.details["limit_kind"] == "day" and event.details["download_limit"] == 1
    cache.clear()
    assert activity.security_alerts() >= 1


def test_superusers_told_in_telegram_after_days_in_a_row(books, monkeypatch):
    sent = []
    monkeypatch.setattr(limits, "_start_alert", sent.append)
    user, client = _reader(day=1, month=0)
    _take(client, books[0])
    for days_ago in (1, 2):                                           # вчера и позавчера тоже упирался
        ActivityEvent.objects.create(at=timezone.now() - timedelta(days=days_ago), user=user,
                                     username=user.username, category="security",
                                     action="download_limit_reached", ok=False)
    _take(client, books[1])
    assert len(sent) == 1 and user.username in sent[0]
    _take(client, books[2], "read")
    _check(client, books[3].pk)
    assert len(sent) == 1                                             # раз в день


def test_no_telegram_alert_for_a_single_day(books, monkeypatch):
    sent = []
    monkeypatch.setattr(limits, "_start_alert", sent.append)
    _user, client = _reader(day=1, month=0)
    _take(client, books[0])
    _take(client, books[1])
    assert sent == []


def test_alert_goes_to_superusers_and_superuser_chat(monkeypatch):
    from sopds_web_backend.models import TelegramLink
    boss = User.objects.create_user("boss", is_superuser=True, is_staff=True)
    reader = User.objects.create_user("reader")
    TelegramLink.objects.create(user=boss, telegram_id=11)
    TelegramLink.objects.create(user=reader, telegram_id=22)
    assert limits.alert_chats({"telegram_admin_chat": "33"}) == ["11", "33"]
    assert limits.alert_chats({"telegram_admin_chat": "11"}) == ["11"]


def test_telegram_delivery_is_limited(books):
    from sopds_web_backend.telegram_library import LibraryHandler
    from tests.integration.access.test_telegram_isolation import FakeClient, _Sync
    user, _client = _reader(day=1, month=0)
    handler, client = LibraryHandler(executor=_Sync()), FakeClient()
    handler.deliver(client, 1, user, books[0].pk, "fb2")
    assert client.docs == ["b0.fb2"]
    handler.deliver(client, 1, user, books[0].pk, "fb2")              # та же — можно
    handler.deliver(client, 1, user, books[1].pk, "fb2")
    assert len(client.docs) == 2 and "⛔" in client.sent[-1] and "Лимит на сегодня" in client.sent[-1]
    assert ActivityEvent.objects.filter(action="download_limit_reached", user=user, channel="telegram").exists()


def test_personal_limits_in_users_table(books):
    reader = User.objects.create_user("reader")
    boss = User.objects.create_user("boss", is_superuser=True, is_staff=True)
    client = Client()
    client.force_login(boss)
    url = reverse("web:user_edit", args=[reader.pk])
    client.post(url, {"role": "user", "downloads_per_day": "5", "downloads_per_month": ""})
    profile = UserProfile.objects.get(user=reader)
    assert (profile.downloads_per_day, profile.downloads_per_month) == (5, None)
    event = ActivityEvent.objects.get(action="download_limit_changed", user=reader)
    assert event.details["download_limits"] == "default / default → 5 / default"
    assert client.post(url, {"role": "user", "downloads_per_day": "x"}).status_code == 422
    page = client.get(reverse("web:users_list")).content.decode("utf-8")
    assert 'name="downloads_per_day"' in page and 'name="downloads_per_month"' in page


def test_default_limits_in_users_settings(monkeypatch, tmp_path):
    from fb2parser_core.settings_manager import SettingsManager
    from sopds_web_backend import views
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(views, "_site_config_path", lambda: str(config))
    boss = User.objects.create_user("boss", is_superuser=True, is_staff=True)
    client = Client()
    client.force_login(boss)
    client.post(reverse("web:users_settings"), {"devices_per_user": "0", "downloads_per_day": "7",
                                                "downloads_per_month": "70"})
    sopds = SettingsManager(str(config)).settings["sopds"]
    assert (sopds["downloads_per_day"], sopds["downloads_per_month"]) == (7, 70)


def test_profile_shows_usage(books):
    _user, client = _reader(day=5, month=50)
    _take(client, books[0])
    page = client.get(reverse("web:profile")).content.decode("utf-8")
    assert 'id="book-limits"' in page and "1 of 5" in page and "1 of 50" in page
    boss = User.objects.create_user("boss", is_superuser=True, is_staff=True)
    client.force_login(boss)
    assert 'id="book-limits"' not in client.get(reverse("web:profile")).content.decode("utf-8")
