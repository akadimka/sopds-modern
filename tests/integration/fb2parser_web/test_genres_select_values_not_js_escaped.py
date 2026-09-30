"""Выпадающие списки жанра на странице Менеджера жанров (по разделам и по
кодам) выводили `value` через `escapejs` — это экранирование для JS-строк, а
не для HTML-атрибута: «Non-Fiction» уходило на сервер как `Non\\u002DFiction`,
жанр с таким именем не находился, и привязка молча сбрасывалась на
«— not mapped —». Затронуты все жанры с дефисом.
"""
import re

import pytest
from django.test import RequestFactory

import fb2parser_web.fb2parser_bridge as bridge
from fb2parser_core.genres_manager import GenresManager
from fb2parser_web.views import genres, genres_code_assign_set


@pytest.fixture
def gm(tmp_path, monkeypatch):
    manager = GenresManager(str(tmp_path / "genres.xml"))
    manager.add_node("Non-Fiction")
    manager.add_node("Фантастика")
    monkeypatch.setattr(bridge, "get_genres_manager", lambda: manager)
    return manager


def test_hyphenated_genre_from_rendered_select_is_saved(gm, admin_user, settings):
    # Полная страница тянет {% static %}; в тестах нет collectstatic-манифеста.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    rf = RequestFactory()
    request = rf.get("/fb2parser/genres/")
    request.user = admin_user
    html = genres(request).content.decode()

    values = set(re.findall(r'<option value="([^"]*)"', html))
    assert "Non-Fiction" in values

    request = rf.post("/fb2parser/genres/code-assign/set/", {"code": "nonf_criticism", "genre": "Non-Fiction"})
    request.user = admin_user
    genres_code_assign_set(request)

    assert gm.resolve_code("nonf_criticism") == ("Non-Fiction", True)
