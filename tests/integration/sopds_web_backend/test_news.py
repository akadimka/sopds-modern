"""«Новости» на главной (docs/news-design.md, sopds_web_backend/news.py).

Поступления — одна новость на день добавления (день — по TIME_ZONE): до 10
книг — список «Автор - Название» с жанром (папка верхнего уровня), больше —
число книг по жанрам со ссылками и «показать все». Новости суперпользователя
идут вперемешку с ними по времени, закреплённые — первыми; на главной — 5,
остальные — на «Все новости». Править — только последнюю и только 24 ч;
вместо удаления — скрыть. Изоляция чужих книг — tests/integration/access/
test_web_isolation.py."""
import json
import os
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from opds_catalog.models import Book, Catalog
from sopds_web_backend import news
from sopds_web_backend.models import News

pytestmark = pytest.mark.django_db
STRONG = "Kx7#vQ2!mLp9"
DAY1 = datetime(2026, 10, 20, 12, 0, tzinfo=dt_timezone.utc)
DAY2 = datetime(2026, 10, 21, 12, 0, tzinfo=dt_timezone.utc)


@pytest.fixture
def site(tmp_path, monkeypatch, settings, override_config):
    """Суперпользователь (видит всё — без владельцев книг) и config.json во
    временной папке: Telegram настроен, отправка — в подделку."""
    import fb2parser_core.settings_manager as sm_module
    import fb2parser_core.telegram_notify as tn

    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    settings.TIME_ZONE = "Europe/Moscow"
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sopds": {}, "autosync": {"telegram_token": "1:x", "telegram_channel": "@lib"}}),
                      encoding="utf-8")
    real = sm_module.SettingsManager
    monkeypatch.setattr(sm_module, "SettingsManager", lambda _path: real(str(config)))
    sent = []

    class FakeClient:
        def send_message(self, chat, text, reply_markup=None):
            sent.append((chat, text))

    monkeypatch.setattr(tn, "client_for", lambda cfg, timeout=20.0: FakeClient())
    with override_config(SOPDS_AUTH=True):
        root = User.objects.create_superuser("root", password=STRONG)
        client = Client()
        client.force_login(root)
        yield client, root, sent


def _book(title, when, folder="Фантастика"):
    cat = Catalog.objects.get_or_create(cat_name=".", path=".")[0]
    filename = f"Автор Тестов - {title}.fb2"
    return Book.objects.create(filename=filename, path=os.path.join(folder, "Автор Тестов"), catalog=cat, avail=2,
                               format="fb2", docdate="", lang="ru", title=title, search_title=title.upper(),
                               annotation="", lang_code=1, registerdate=when)


def _main(client):
    response = client.get(reverse("web:main"))
    assert response.status_code == 200
    return response.context["news_items"], response.content.decode("utf-8")


def test_one_news_per_day_with_title_and_genre(site):
    client, _, _ = site
    _book("Первая", DAY1)
    _book("Вторая", DAY1 + timedelta(hours=2), folder="Детектив")
    _book("Третья", DAY2)
    items, html = _main(client)
    assert [(i.kind, i.day.isoformat(), i.total) for i in items] == [("books", "2026-10-21", 1),
                                                                     ("books", "2026-10-20", 2)]
    assert [(b.title, b.genre) for b in items[1].books] == [("Автор Тестов - Вторая", "Детектив"),
                                                            ("Автор Тестов - Первая", "Фантастика")]
    assert "Автор Тестов - Вторая" in html and "Recently added" not in html


def test_day_follows_time_zone(site):
    client, _, _ = site
    _book("Ночная", datetime(2026, 10, 20, 22, 30, tzinfo=dt_timezone.utc))   # 01:30 21-го по Москве
    items, _ = _main(client)
    assert items[0].day.isoformat() == "2026-10-21"


def test_more_than_ten_books_counts_by_genre_with_links(site):
    client, _, _ = site
    for i in range(8):
        _book(f"Фант {i}", DAY1 + timedelta(minutes=i))
    for i in range(3):
        _book(f"Детектив {i}", DAY1 + timedelta(minutes=20 + i), folder="Детектив")
    _book("Другой день", DAY2, folder="Детектив")
    items, html = _main(client)
    day1 = items[1]
    assert day1.total == 11 and not hasattr(day1, "books")
    assert [(g.name, g.cnt) for g in day1.genres] == [("Фантастика", 8), ("Детектив", 3)]
    genre_page = client.get(day1.genres[1].url).content.decode("utf-8")      # жанр за этот день — не за другой
    assert "Детектив 2" in genre_page and "Другой день" not in genre_page and "Фант 1" not in genre_page
    all_page = client.get(day1.all_url).content.decode("utf-8")
    assert "Фант 0" in all_page and "Детектив 0" in all_page and "Другой день" not in all_page   # по 10 на страницу


def test_manual_news_mixed_by_time_pinned_first_five_on_main(site):
    client, root, _ = site
    _book("Книга", DAY2)
    pinned = News.objects.create(title="Закреплённая", created=DAY1 - timedelta(days=30), pinned=True, author=root)
    for i in range(6):
        News.objects.create(title=f"Новость {i}", created=DAY2 + timedelta(hours=i + 1), author=root)
    News.objects.create(title="Скрытая", created=DAY2 + timedelta(days=1), hidden=True, author=root)
    items, html = _main(client)
    assert len(items) == news.MAIN_PAGE_ITEMS and items[0].news == pinned
    assert [i.news.title for i in items[1:]] == ["Новость 5", "Новость 4", "Новость 3", "Новость 2"]
    assert "Скрытая" not in html and reverse("web:news") in html
    page = client.get(reverse("web:news"))
    titles = [getattr(i, "news", None) and i.news.title for i in page.context["news_page"].object_list]
    assert titles[-1] is None and "Новость 0" in titles and "Скрытая" not in titles    # книги дня — последними


def test_new_mark_since_last_visit(site):
    client, root, _ = site
    News.objects.create(title="Старая", created=timezone.now() - timedelta(days=1), author=root)
    items, _ = _main(client)
    assert not any(i.is_new for i in items)                     # первый визит — без меток
    from sopds_web_backend.models import UserProfile
    UserProfile.objects.filter(user=root).update(news_seen=timezone.now() - timedelta(minutes=1))
    News.objects.create(title="Свежая", created=timezone.now() - timedelta(seconds=30), author=root)
    items, html = _main(client)
    assert [(i.news.title, i.is_new) for i in items] == [("Свежая", True), ("Старая", False)]
    items, _ = _main(client)
    assert not any(i.is_new for i in items)                     # уже видел


def test_publish_with_telegram_and_escaping(site):
    client, _, sent = site
    response = client.post(reverse("web:news_create"),
                           {"title": "<script>x</script> & ко", "text": "строка 1\nстрока <b>2</b>",
                            "pinned": "on", "telegram": "on"})
    assert response.status_code == 302 and "news_sent=1" in response["Location"]
    item = News.objects.get()
    assert item.pinned and item.telegram_sent is not None
    assert sent == [("@lib", "📰 <b>&lt;script&gt;x&lt;/script&gt; &amp; ко</b>\n\nстрока 1\nстрока &lt;b&gt;2&lt;/b&gt;")]
    _, html = _main(client)
    assert "<script>x</script>" not in html and "&lt;script&gt;x&lt;/script&gt;" in html
    assert "строка 1<br>строка &lt;b&gt;2&lt;/b&gt;" in html


def test_edit_only_latest_within_24_hours(site):
    client, root, _ = site
    older = News.objects.create(title="Старая", created=timezone.now() - timedelta(hours=2), author=root)
    latest = News.objects.create(title="Последняя", created=timezone.now() - timedelta(hours=1), author=root)
    assert client.post(reverse("web:news_edit", args=[older.pk]), {"title": "X"}).status_code == 422
    assert client.post(reverse("web:news_edit", args=[latest.pk]), {"title": "Исправлено", "text": "т"}).status_code == 302
    latest.refresh_from_db()
    assert (latest.title, latest.text) == ("Исправлено", "т") and latest.edited is not None
    News.objects.filter(pk=latest.pk).update(created=timezone.now() - timedelta(hours=25))
    assert client.post(reverse("web:news_edit", args=[latest.pk]), {"title": "Поздно"}).status_code == 422
    settings_page = client.get(reverse("web:settings") + "?tab=news").content.decode("utf-8")
    assert reverse("web:news_edit", args=[latest.pk]) not in settings_page


def test_hide_and_pin_toggle_and_archive_keeps_hidden(site):
    client, root, _ = site
    item = News.objects.create(title="Объявление", created=timezone.now(), author=root)
    client.post(reverse("web:news_action", args=[item.pk, "hide"]))
    client.post(reverse("web:news_action", args=[item.pk, "pin"]))
    item.refresh_from_db()
    assert item.hidden and item.pinned
    assert "Объявление" not in _main(client)[1]
    archive = client.get(reverse("web:settings") + "?tab=news").content.decode("utf-8")
    assert "Объявление" in archive and "root" in archive          # автор — суперпользователю
    client.post(reverse("web:news_action", args=[item.pk, "hide"]))
    assert "Объявление" in _main(client)[1]


def test_users_cannot_manage_news(site):
    client, root, _ = site
    item = News.objects.create(title="Объявление", created=timezone.now(), author=root)
    reader = Client()
    reader.force_login(User.objects.create_user("reader", password=STRONG))
    for url, data in ((reverse("web:news_create"), {"title": "Взлом"}),
                      (reverse("web:news_edit", args=[item.pk]), {"title": "Взлом"}),
                      (reverse("web:news_action", args=[item.pk, "hide"]), {})):
        assert reader.post(url, data).status_code == 302              # на вход — не суперпользователь
    item.refresh_from_db()
    assert News.objects.count() == 1 and item.title == "Объявление" and not item.hidden
    page = reader.get(reverse("web:main")).content.decode("utf-8")
    block = page[page.index("sp-news-row"):page.rindex("</article>")]
    assert "Объявление" in block and "root" not in block                # имя автора не показываем


def test_telegram_not_configured_keeps_news(site, tmp_path):
    client, _, sent = site
    (tmp_path / "config.json").write_text(json.dumps({"sopds": {}, "autosync": {}}), encoding="utf-8")
    response = client.post(reverse("web:news_create"), {"title": "Без канала", "telegram": "on"})
    assert response.status_code == 422 and News.objects.filter(title="Без канала", telegram_sent=None).exists()
    assert sent == []
