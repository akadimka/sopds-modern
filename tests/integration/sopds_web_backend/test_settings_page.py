"""Страница настроек сайта показывает действующие значения: из config.json,
а если ключа там нет — умолчание, как его видит сам сайт (`sopds_config`).
На сервере config.json бывает почти пустым: раньше флажки, включённые по
умолчанию (алфавитное меню, скрытие дубликатов, ZIP…), выглядели
выключенными, а сохранение формы молча их выключало. У каждого параметра —
описание (не из `_comments` файла, которого обычно нет)."""
import json
import re

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse

from opds_catalog.sopds_config import _DEFAULTS, effective

pytestmark = pytest.mark.django_db


def test_effective_fills_unset_keys_and_keeps_set_ones():
    eff = effective({"maxitems": 0, "alphabet_menu": False, "language": "  ", "root_lib": "/srv/books"})
    assert eff["maxitems"] == 10                     # 0 — «не задано» для размера страницы
    assert eff["alphabet_menu"] is False             # явно выключено — остаётся выключенным
    assert eff["language"] == "en-US" and eff["doubles_hide"] is True and eff["root_lib"] == "/srv/books"
    assert set(_DEFAULTS) <= set(eff)


@pytest.fixture
def page(tmp_path, monkeypatch, settings):
    import fb2parser_core.settings_manager as sm_module
    import opds_catalog.ratings_fetchers as fetchers

    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sopds": {"root_lib": "/srv/books"}}), encoding="utf-8")   # как на сервере
    real = sm_module.SettingsManager
    monkeypatch.setattr(sm_module, "SettingsManager", lambda _path: real(str(config)))
    monkeypatch.setattr(fetchers, "wake_or_start", lambda name: None)
    monkeypatch.setattr(fetchers, "stop_fetcher", lambda name: None)
    client = Client()
    client.force_login(User.objects.create_superuser("root", password="Kx7#vQ2!mLp9"))
    return client, config


def _form(html):
    """Что браузер отправит, если нажать «Сохранить», ничего не меняя."""
    data = {}
    for tag in re.findall(r"<input[^>]*>", html):
        name = re.search(r'name="([^"]+)"', tag)
        if not name or "csrfmiddlewaretoken" in tag:
            continue
        kind = (re.search(r'type="([^"]+)"', tag) or [None, "text"])[1]
        if kind in ("checkbox", "radio"):
            if re.search(r"\schecked\b", tag):
                data[name.group(1)] = (re.search(r'value="([^"]*)"', tag) or [None, "on"])[1]
        else:
            data[name.group(1)] = (re.search(r'value="([^"]*)"', tag) or [None, ""])[1]
    return data


def test_page_shows_defaults_for_keys_missing_in_config(page):
    client, _config = page
    html = client.get(reverse("web:settings")).content.decode("utf-8")
    for flag in ("alphabet_menu", "doubles_hide", "zipscan", "inpx_skip_unchanged", "delete_logical"):
        assert re.search(rf'name="{flag}"\s+checked', html), flag
    assert 'name="maxitems" value="10"' in html and 'name="splititems" value="100"' in html
    assert 'name="language" value="en-US"' in html and 'name="scan_shed_dow" value="-1"' in html


def test_saving_unchanged_form_keeps_defaults(page):
    client, config = page
    form = _form(client.get(reverse("web:settings")).content.decode("utf-8"))
    client.post(reverse("web:settings"), form)
    sopds = json.loads(config.read_text(encoding="utf-8"))["sopds"]
    assert sopds["alphabet_menu"] is True and sopds["doubles_hide"] is True and sopds["zipscan"] is True
    assert sopds["maxitems"] == 10 and sopds["splititems"] == 100 and sopds["root_lib"] == "/srv/books"


def test_every_parameter_has_a_description(page):
    client, _config = page
    html = client.get(reverse("web:settings")).content.decode("utf-8")
    assert '<div class="s-hint"></div>' not in html
    assert "If a letter has more items than this" in html and "Default: 100." in html
