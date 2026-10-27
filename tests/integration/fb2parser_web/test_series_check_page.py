"""Страница «Дубли серий» в fb2parser (/fb2parser/series-check/): только
суперпользователю; отчёт считается в фоне и сохраняется в файл — страница
показывает последний отчёт без нового прогона."""
import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from fb2parser_web import fb2parser_bridge, views
from tests.integration.opds_catalog.test_series_check import (
    library,  # noqa: F401 — фикстура
)

pytestmark = pytest.mark.django_db
STRONG = "Kx7#vQ2!mLp9"


@pytest.fixture
def page(library, tmp_path_factory, monkeypatch, settings):  # noqa: F811
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    report_file = tmp_path_factory.mktemp("report") / "series_check.json"
    monkeypatch.setattr(fb2parser_bridge, "_series_check_path", lambda: str(report_file))
    client = Client()
    client.force_login(User.objects.create_superuser("root", password=STRONG))
    return client, library, report_file


def test_report_is_saved_and_shown(page):
    client, library, report_file = page
    html = client.get(reverse("fb2parser:series_check")).content.decode("utf-8")
    assert "No report yet" in html
    views._run_series_check_thread(str(library))                 # то, что делает фоновый поток
    assert report_file.exists()
    html = client.get(reverse("fb2parser:series_check")).content.decode("utf-8")
    assert "Петров Иван / Хроники" in html and "Без номера" in html
    assert "Third-party omnibus (1)" in html and "Ник [Иван Петров]. Хроники 1-5" in html
    assert "the suffix does not match the number of volumes inside" in html
    status = client.get(reverse("fb2parser:series_check_status"))
    assert status["HX-Refresh"] == "true"                         # готово — страница перезагрузится


def test_book_titles_are_escaped(page):
    from opds_catalog.models import Book
    client, library, _ = page
    Book.objects.filter(title="Без номера").update(title='<img src=x onerror="alert(1)">')
    views._run_series_check_thread(str(library))
    html = client.get(reverse("fb2parser:series_check")).content.decode("utf-8")
    assert '<img src=x onerror="alert(1)">' not in html and "&lt;img src=x" in html


def test_only_superuser(page):
    reader = Client()
    reader.force_login(User.objects.create_user("reader", password=STRONG))
    for name in ("series_check", "series_check_status"):
        assert reader.get(reverse(f"fb2parser:{name}")).status_code == 302
    assert reader.post(reverse("fb2parser:series_check_start")).status_code == 302
