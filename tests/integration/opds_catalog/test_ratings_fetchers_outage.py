"""Сбой сайта рейтингов (баг №167).

litmarket.ru отвечал HTTP 500 и на поиск, и на главную — фетчер на сервере-2
пометил ошибкой 3 271 книгу из 3 355 и пошёл дальше. Повторить их он должен
был только через 14 дней (вместе с устаревшими). Ветка «HTTP 429/503 —
пауза» была мёртвой: _process_book никогда не возвращал эти коды.

Теперь: ошибка при лежащей главной (или 10 ошибок подряд) — сбой сайта:
книге ничего не пишем, долгая пауза, затем та же книга. Книги с ошибкой
повторяются через сутки.
"""
import importlib
import urllib.error
from datetime import timedelta

import pytest
from django.utils import timezone

from opds_catalog import ratings_fetchers
from opds_catalog.models import (
    AuthorTodayRating,
    Book,
    FantlabRating,
    LitmarketRating,
    SamlibRating,
)
from opds_catalog.ratings_fetchers import OutageDetector, next_book_to_fetch, site_is_down

# модуль, метод получения, ответ «ошибка», модель, связь Book → рейтинг, срок устаревания
_FETCHERS = [
    ("fetch_fantlab_ratings", "_fetch_rating", (None, 0, "u", True), FantlabRating, "fantlab_rating", 14),
    ("fetch_authortoday_ratings", "_fetch_rating", (None, None, None, "u", True), AuthorTodayRating,
     "authortoday_rating", 14),
    ("fetch_litmarket_ratings", "_fetch_rating", (None, None, "u", True), LitmarketRating, "litmarket_rating", 14),
    ("fetch_samlib_ratings", "_fetch_single_rating", (None, 0, "u", True), SamlibRating, "samlib_rating", 7),
]


def _make_book(catalog, name):
    return Book.objects.create(filename=f"{name}.fb2", path=".", catalog=catalog, format="fb2", cat_type=0,
                               docdate="", lang="ru", title=name, search_title=name.upper(), annotation="")


def _command(module, fetch_method, fetched, monkeypatch):
    cmd = importlib.import_module(f"opds_catalog.management.commands.{module}").Command()
    monkeypatch.setattr(cmd, fetch_method, lambda *a, **k: fetched)
    if module == "fetch_samlib_ratings":
        monkeypatch.setattr(cmd, "_is_compilation", lambda b: False)
    return cmd


def _process(cmd, module, book):
    return cmd._process_book(book, "series") if module == "fetch_samlib_ratings" else cmd._process_book(book)


# --- сбой сайта ----------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("module, fetch_method, error, model, rel, stale_days", _FETCHERS)
def test_error_while_site_is_down_is_not_saved(module, fetch_method, error, model, rel, stale_days,
                                               monkeypatch, book):
    probes = []
    monkeypatch.setattr(ratings_fetchers, "site_is_down", lambda url, ua: probes.append(url) or True)
    cmd = _command(module, fetch_method, error, monkeypatch)

    assert _process(cmd, module, book) == ("outage", "error")
    assert not model.objects.filter(book=book).exists()
    assert probes == [cmd._ROOT_URL]
    assert next_book_to_fetch(rel, stale_days) == book       # после паузы — та же книга


@pytest.mark.django_db
@pytest.mark.parametrize("module, fetch_method, error, model, rel, stale_days", _FETCHERS)
def test_error_on_one_book_with_site_up_is_saved(module, fetch_method, error, model, rel, stale_days,
                                                 monkeypatch, book):
    monkeypatch.setattr(ratings_fetchers, "site_is_down", lambda url, ua: False)
    cmd = _command(module, fetch_method, error, monkeypatch)

    assert _process(cmd, module, book) == (200, "error")
    assert model.objects.get(book=book).fetch_error is True


def test_streak_of_errors_is_an_outage_even_if_home_page_is_up(monkeypatch):
    """Главная жива, а поиск лежит — каждая книга с ошибкой."""
    monkeypatch.setattr(ratings_fetchers, "site_is_down", lambda url, ua: False)
    detector = OutageDetector("https://x/", "ua", streak=3)
    assert [detector.is_outage(True) for _ in range(4)] == [False, False, True, True]
    assert detector.is_outage(False) is False                 # удача сбрасывает счёт
    assert detector.is_outage(True) is False


def test_success_does_not_probe_the_site(monkeypatch):
    monkeypatch.setattr(ratings_fetchers, "site_is_down", lambda url, ua: pytest.fail("probe on success"))
    assert OutageDetector("https://x/", "ua").is_outage(False) is False


@pytest.mark.parametrize("raised, down", [
    (urllib.error.HTTPError("u", 500, "err", {}, None), True),
    (urllib.error.HTTPError("u", 503, "err", {}, None), True),
    (urllib.error.HTTPError("u", 429, "err", {}, None), True),
    (urllib.error.HTTPError("u", 404, "err", {}, None), False),
    (urllib.error.URLError("timed out"), True),
    (None, False),
])
def test_site_is_down(monkeypatch, raised, down):
    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        if raised:
            raise raised
        return _Resp()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert site_is_down("https://x/", "ua") is down


def test_pause_for_outage_reports_site_down(monkeypatch, db, book):
    from opds_catalog.ratings_progress import get_progress

    sleeps = []
    monkeypatch.setattr(ratings_fetchers, "sleep_or_stop", lambda src, sec: sleeps.append(sec))
    ratings_fetchers.pause_for_outage("litmarket", book, lambda msg: None)
    assert sleeps == [ratings_fetchers.SLEEP_OUTAGE]
    assert get_progress("litmarket")["status"] == "site_down"


# --- повтор книг с ошибкой через сутки -------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize("module, fetch_method, error, model, rel, stale_days", _FETCHERS)
def test_errored_books_are_retried_after_a_day(module, fetch_method, error, model, rel, stale_days, catalog):
    now = timezone.now()
    fresh_error, old_error, stale_ok, fresh_ok = (_make_book(catalog, n) for n in ("a", "b", "c", "d"))
    for b, fetch_error, age in ((fresh_error, True, timedelta(hours=2)),
                                (old_error, True, timedelta(hours=25)),
                                (stale_ok, False, timedelta(days=stale_days + 1)),
                                (fresh_ok, False, timedelta(hours=25))):
        model.objects.create(book=b, fetch_error=fetch_error)
        model.objects.filter(book=b).update(fetched_at=now - age)

    assert next_book_to_fetch(rel, stale_days) == old_error      # ошибка суточной давности — раньше устаревших
    model.objects.filter(book=old_error).update(fetch_error=False, fetched_at=now)
    assert next_book_to_fetch(rel, stale_days) == stale_ok
    model.objects.filter(book=stale_ok).update(fetched_at=now)
    assert next_book_to_fetch(rel, stale_days) is None           # свежая ошибка и свежий результат ждут

    new = _make_book(catalog, "e")
    assert next_book_to_fetch(rel, stale_days) == new             # книга без результата — первой
