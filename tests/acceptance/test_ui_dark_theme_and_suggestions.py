"""Интерфейс (по замечаниям пользователя):
0. только тёмная тема — без переключателя и выбора темы в localStorage
   (сайт, fb2parser, админка Django);
1. блоки не сдвигаются при наведении курсора;
2. подсказки поиска появляются при наборе (событие `input` — и на телефоне,
   и при вводе с подбором слов, и при вставке) и показывают те же варианты,
   что найдёт поиск по Enter (вхождение в любом месте), начинающиеся — первыми,
   с пунктом «Все результаты (N)».
"""
import re
from pathlib import Path

import pytest
from django.urls import reverse

from opds_catalog.models import Author, bauthor

THEME_CSS = Path(__file__).resolve().parents[2] / "src" / "sopds_web_backend" / "static" / "css" / "theme.css"


@pytest.fixture
def superuser_client(client, django_user_model, settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    client.force_login(django_user_model.objects.create_superuser("root", password="Kx7#vQ2!mLp9"))
    return client


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/web/", "/fb2parser/", "/admin/"])
def test_dark_theme_only(superuser_client, url):
    html = superuser_client.get(url, follow=True).content.decode("utf-8")
    assert "theme-toggle" not in html and "toggleTheme" not in html
    assert "localStorage.getItem('theme')" not in html and "admin/js/theme.js" not in html
    assert 'data-theme="dark"' in html or 'dataset.theme = "dark"' in html


def test_no_movement_on_hover():
    css = THEME_CSS.read_text(encoding="utf-8")
    hover_rules = re.findall(r"[^{}]*:hover[^{}]*\{[^}]*\}", css)
    assert hover_rules and not [r for r in hover_rules if "transform" in r or "translate" in r]


@pytest.mark.django_db
def test_search_box_suggests_on_input(superuser_client):
    html = superuser_client.get(reverse("web:main")).content.decode("utf-8")
    assert 'hx-trigger="input changed delay:300ms, search"' in html


def _book(catalog, title, publish):
    from opds_catalog.models import Book
    b = Book.objects.create(filename=f"{title}.fb2", path=".", format="fb2", catalog=catalog, avail=2,
                            title=title, search_title=title.upper(), docdate="", lang="ru", annotation="")
    publish(b)
    return b


@pytest.mark.django_db
def test_suggestions_match_anywhere_prefix_first_with_all_results(superuser_client, catalog, publish):
    for title in ("Мировая война", "Война и мир", "Война миров", "Просто книга"):
        _book(catalog, title, publish)
    response = superuser_client.post(reverse("web:suggest"), {"searchterms": "война", "type": "title"})
    items = re.findall(r'class="sopds-suggestion-item">([^<]+)</a>', response.content.decode("utf-8"))
    assert items == ["Война и мир", "Война миров", "Мировая война"]   # начинающиеся — первыми
    assert "sopds-suggestion-all" in response.content.decode("utf-8")
    assert "searchtype=m&amp;searchterms=%D0%B2%D0%BE%D0%B9%D0%BD%D0%B0" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_author_suggestions_match_anywhere(superuser_client, catalog, publish):
    book = _book(catalog, "Книга", publish)
    for name in ("Толстой Лев", "Алексей Толстой"):
        bauthor.objects.create(book=book, author=Author.objects.create(full_name=name, search_full_name=name.upper()))
    html = superuser_client.post(reverse("web:suggest"), {"searchterms": "толст", "type": "author"}).content.decode()
    assert re.findall(r'class="sopds-suggestion-item">([^<]+)</a>', html) == ["Толстой Лев", "Алексей Толстой"]


@pytest.mark.django_db
def test_search_results_keep_signed_in_header(superuser_client):
    """После Enter в поиске шапка показывала «Login»: SearchBooksView клал в
    контекст `user` = имя пользователя (строку), и оно перекрывало
    request.user — шаблон считал, что пользователь не вошёл."""
    response = superuser_client.get(reverse("web:searchbooks"), {"searchtype": "m", "searchterms": "игро"})
    html = response.content.decode("utf-8")
    assert response.context["user"].is_authenticated
    assert reverse("web:logout") in html and ">Login<" not in html


@pytest.mark.django_db
def test_search_form_does_not_put_csrf_token_into_url(superuser_client):
    """GET-форма поиска с {% csrf_token %} отправляла токен в адрес страницы
    (история браузера, логи); токен для htmx — на странице, вне форм."""
    html = superuser_client.get(reverse("web:main")).content.decode("utf-8")
    form = html[html.index('id="searchform"'):html.index("</form>", html.index('id="searchform"'))]
    assert "csrfmiddlewaretoken" not in form and 'name="csrfmiddlewaretoken"' in html


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/web/", "/web/search/books/?searchtype=m&searchterms=x", "/fb2parser/", "/admin/"])
def test_no_template_syntax_leaks_into_pages(superuser_client, url):
    """Многострочный {# … #} Django не считает комментарием — хвост «… #}»
    выводился вверху каждой страницы сайта."""
    html = superuser_client.get(url, follow=True).content.decode("utf-8")
    assert "#}" not in html and "{%" not in html and "{#" not in html


def test_logout_has_no_side_padding():
    """«(Logout)», а не «( Logout )» — без отступов по бокам у ссылки/кнопки."""
    css = THEME_CSS.read_text(encoding="utf-8")
    assert re.search(r"\.sp-logout-form button \{[^}]*padding: 0\.3rem 0;", css)
    assert re.search(r"\.fb2-topbar-links a\.fb2-logout \{ padding: 0\.3rem 0; \}", css)


def test_header_items_share_one_line_height():
    """«admin (Logout)» садился ниже ссылок Scan/FB2Parser/Settings: строчная
    ссылка и кнопка со своей высотой строки. Пункты меню — flex по центру,
    ссылки и кнопка выхода — одна высота строки."""
    css = THEME_CSS.read_text(encoding="utf-8")
    assert re.search(r"\.sopds-top-bar \.menu > li \{ display: flex; align-items: center;", css)
    assert re.search(r"\.sopds-top-bar \.menu li a \{[^}]*line-height: 1;", css)
    assert re.search(r"\.sopds-top-bar \.sp-logout-form button \{[^}]*line-height: 1;", css)
