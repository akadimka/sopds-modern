"""Регрессия для `genre_names()` — docs/quality-roadmap.md, баг №80.

`?cb=<jsFuncName>` позволяет странице выбрать, какая глобальная JS-функция
вызывается при клике на жанр в picker'е (нужно, когда на одной странице
сосуществуют несколько независимых picker'ов — дерево папок на
dashboard.html и множественный выбор genre-комбинаций там же). Значение
подставляется прямо в атрибут `onclick` шаблона — обязательно проверять
белым списком (иначе произвольный JS через query-параметр).
"""
import pytest
from django.test import RequestFactory

from fb2parser_web.views import genre_names


@pytest.fixture
def rf():
    return RequestFactory()


class TestGenreNamesCallbackParam:
    def test_default_callback_used_when_absent(self, rf, admin_user):
        request = rf.get("/fb2parser/genre-names/")
        request.user = admin_user
        response = genre_names(request)
        content = response.content.decode("utf-8")
        assert "gpSelectGenre(" in content

    def test_custom_safe_callback_is_used(self, rf, admin_user):
        request = rf.get("/fb2parser/genre-names/?cb=mrSelectGenre")
        request.user = admin_user
        response = genre_names(request)
        content = response.content.decode("utf-8")
        assert "mrSelectGenre(" in content

    def test_unsafe_callback_falls_back_to_default(self, rf, admin_user):
        request = rf.get(
            "/fb2parser/genre-names/?cb=" + "alert(1))//"
        )
        request.user = admin_user
        response = genre_names(request)
        content = response.content.decode("utf-8")
        assert "alert(1))//(" not in content
        assert "gpSelectGenre(" in content
