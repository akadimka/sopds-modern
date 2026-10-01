"""XSS в /fb2parser/: значения из запроса и из FB2 (недоверенные) не должны
попадать в HTML/JS неэкранированными.

- `names_from_csv` возвращал `?csv_path=` прямо в HTML-фрагменте;
- `compiler_groups.html`/`settings.html` вставляли `json.dumps(...)|safe`
  внутрь `<script>` — название книги `</script><img onerror=…>` закрывало
  скрипт;
- `browse_folders` подставлял `?target=` в `onclick="…('{{ target }}')"`,
  где HTML-экранирование не защищает JS-строку.
"""
import pytest
from django.template.loader import render_to_string
from django.test import RequestFactory

from fb2parser_web.views import browse_folders, names_from_csv

_PAYLOAD = '</script><img src=x onerror=alert(1)>'


@pytest.fixture
def plain_static(settings):
    # Шаблоны тянут {% static %}; в тестах нет collectstatic-манифеста.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def _get(view, url, user, **params):
    request = RequestFactory().get(url, params)
    request.user = user
    return view(request)


def test_names_from_csv_escapes_missing_path(admin_user):
    html = _get(names_from_csv, "/fb2parser/normalize/names/from-csv/",
                admin_user, csv_path=_PAYLOAD).content.decode()
    assert "<img" not in html
    assert "&lt;img" in html


def test_compiler_groups_title_cannot_close_script(plain_static):
    html = render_to_string("fb2parser/compiler_groups.html", {
        "groups": [{"idx": 0, "author": "A", "series": "S"}],
        "total": 1,
        "groups_books": {0: {"books": [{"title": _PAYLOAD}]}},
    })
    assert _PAYLOAD not in html
    assert 'id="cg-groups-books"' in html


def test_settings_list_values_cannot_close_script(plain_static):
    html = render_to_string("fb2parser/settings.html", {
        "lists_meta": [("filename_blacklist", "BL")],
        "first_list_key": "filename_blacklist",
        "lists_data": {"filename_blacklist": [_PAYLOAD]},
    })
    assert _PAYLOAD not in html
    assert 'id="s-lists-data"' in html


@pytest.mark.parametrize("target", ["x');alert(1);('", 'x" onmouseover="alert(1)', "a b"])
def test_browse_rejects_non_id_target(admin_user, tmp_path, target):
    response = _get(browse_folders, "/fb2parser/browse/", admin_user,
                    path=str(tmp_path), target=target)
    assert response.status_code == 400


def test_browse_accepts_input_id_target(admin_user, tmp_path):
    response = _get(browse_folders, "/fb2parser/browse/", admin_user,
                    path=str(tmp_path), target="names-csv-path")
    assert response.status_code == 200
    assert "closeBrowser('names-csv-path')" in response.content.decode()
