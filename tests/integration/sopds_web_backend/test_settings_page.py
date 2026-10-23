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


def _active(html):
    """(активная кнопка вкладки, активная панель)."""
    button = re.search(r'class="s-tab-btn active" onclick="sTab\(\'(\w+)\'\)"', html)
    pane = re.search(r'id="s-pane-(\w+)" class="s-tab-panel active"', html)
    return button and button.group(1), pane and pane.group(1)


def test_telegram_has_its_own_tab(page):
    client, _config = page
    html = client.get(reverse("web:settings")).content.decode("utf-8")
    assert ">Telegram</button>" in html
    telegram = html.split('id="s-pane-telegram"', 1)[1]
    schedule = html.split('id="s-pane-schedule"', 1)[1].split('id="s-pane-telegram"', 1)[0]
    assert 'name="autosync_telegram_token"' in telegram and 'name="autosync_telegram_api_url"' in telegram
    assert "autosync_telegram" not in schedule                  # из «Расписания» убран целиком


@pytest.mark.parametrize("tab", ["general", "scanner", "schedule", "telegram"])
def test_saving_stays_on_the_same_tab(page, tab):
    client, _config = page
    form = _form(client.get(reverse("web:settings")).content.decode("utf-8"))
    response = client.post(reverse("web:settings"), {**form, "tab": tab})
    assert response.status_code == 302 and response["Location"].endswith(f"?saved=1&tab={tab}")
    html = client.get(response["Location"]).content.decode("utf-8")
    assert _active(html) == (tab, tab) and f'name="tab" id="s-active-tab" value="{tab}"' in html


def test_unknown_or_missing_tab_opens_general(page):
    client, _config = page
    for url in (reverse("web:settings"), reverse("web:settings") + "?tab=<script>"):
        assert _active(client.get(url).content.decode("utf-8")) == ("general", "general")


def test_users_and_sign_in_security_live_on_the_users_page(page):
    client, config = page
    settings_html = client.get(reverse("web:settings")).content.decode("utf-8")
    form = settings_html.split('<form method="post" action="/web/settings/">', 1)[1].split("</form>", 1)[0]
    users_pane = settings_html.split('id="s-pane-users"', 1)[1]
    for name in ("devices_per_user", "admin_login_code", "opds_app_passwords_only"):
        assert f'name="{name}"' not in form and f'name="{name}"' in users_pane    # свои формы — вкладка «Пользователи»
    users_html = client.get(reverse("web:users_list")).content.decode("utf-8")
    assert _active(users_html) == ("users", "users")
    response = client.post(reverse("web:users_settings"), {"devices_per_user": "3", "opds_app_passwords_only": "on"})
    assert response.status_code == 302 and response["Location"].endswith("?saved=1#user-settings")
    sopds = json.loads(config.read_text(encoding="utf-8"))["sopds"]
    assert sopds["devices_per_user"] == 3 and sopds["opds_app_passwords_only"] is True
    assert "Settings saved." in client.get(response["Location"]).content.decode("utf-8")


def test_saving_main_settings_keeps_users_and_security_settings(page):
    """Общая форма настроек их больше не присылает — и не должна сбрасывать."""
    client, config = page
    client.post(reverse("web:users_settings"), {"devices_per_user": "3", "opds_app_passwords_only": "on"})
    form = _form(client.get(reverse("web:settings")).content.decode("utf-8"))
    client.post(reverse("web:settings"), form)
    sopds = json.loads(config.read_text(encoding="utf-8"))["sopds"]
    assert sopds["devices_per_user"] == 3 and sopds["opds_app_passwords_only"] is True


def test_users_is_a_regular_tab(page):
    """«Пользователи» — такая же вкладка, как остальные (не ссылка на отдельную
    страницу); /web/settings/users/ открывает настройки сразу на ней."""
    client, _config = page
    html = client.get(reverse("web:settings")).content.decode("utf-8")
    assert "onclick=\"sTab('users')\"" in html and 'href="/web/settings/users/"' not in html
    assert 'id="s-pane-users" class="s-tab-panel"' in html                # скрыта, пока не выбрана
    assert _active(client.get(reverse("web:users_list") + "?tab=telegram").content.decode("utf-8")) == ("telegram", "telegram")
    error = client.post(reverse("web:user_create"), {"username": "", "password": ""})
    assert error.status_code == 422 and _active(error.content.decode("utf-8")) == ("users", "users")


def test_language_switch_returns_to_the_current_address(page):
    client, _config = page
    html = client.get(reverse("web:settings")).content.decode("utf-8")
    assert "function sopdsSetLanguage" in html and "location.pathname + location.search + location.hash" in html
    assert "sopdsSetLanguage(" in html.split("active-lang", 1)[1]


def test_device_limit_has_its_own_column_next_to_devices(page):
    """Лимит устройств — своя колонка сразу после «Устройств» (раньше — безымянное
    поле в «Действиях» после пароля); поле — часть формы строки (form=…)."""
    client, _config = page
    anna = User.objects.create_user("anna", password="Kx7#vQ2!mLp9")
    html = client.get(reverse("web:users_list")).content.decode("utf-8")
    headers = re.findall(r"<th>([^<]+)</th>", html.split('class="sp-users"', 1)[1].split("</thead>", 1)[0])
    assert headers[headers.index("Devices") + 1] == "Device limit"
    row = html.split(f'id="user-{anna.pk}"', 1)[1].split("</tr>", 1)[0]
    cells = re.findall(r"<td>(.*?)</td>", row, re.S)
    assert 'name="max_devices"' in cells[5] and f'form="user-edit-{anna.pk}"' in cells[5]
    assert f'id="user-edit-{anna.pk}"' in row and 'name="max_devices"' not in cells[6]   # в «Действиях» его нет


def test_role_and_password_have_own_columns(page):
    """«Действия» — только «Сохранить» и «Удалить»; роль и новый пароль — свои
    колонки «Смена роли» и «Новый пароль», поля — в форме строки (form=…)."""
    client, _config = page
    anna = User.objects.create_user("anna", password="Kx7#vQ2!mLp9")
    html = client.get(reverse("web:users_list")).content.decode("utf-8")
    headers = re.findall(r"<th>([^<]+)</th>", html.split('class="sp-users"', 1)[1].split("</thead>", 1)[0])
    assert headers[-3:] == ["Change role", "New password", "Actions"]
    row = html.split(f'id="user-{anna.pk}"', 1)[1].split("</tr>", 1)[0]
    cells = re.findall(r"<td>(.*?)</td>", row, re.S)
    assert 'name="role"' in cells[6] and f'form="user-edit-{anna.pk}"' in cells[6]
    assert 'name="password"' in cells[7] and f'form="user-edit-{anna.pk}"' in cells[7] and "sopdsFillPassword" in cells[7]
    assert 'name="role"' not in cells[8] and 'name="password"' not in cells[8] and "Delete" in cells[8]


def test_sticky_header_switch(page, override_config):
    """«Закреплять шапку при прокрутке» (Общие → Интерфейс): меню, «Сейчас на
    сайте» и поиск остаются вверху; по умолчанию выключено."""
    client, config = page
    html = client.get(reverse("web:settings")).content.decode("utf-8")
    interface = html.split('id="s-pane-general"', 1)[1].split("Conversion", 1)[0]
    assert 'name="sticky_header"' in interface and "stay at the top of the screen" in interface
    assert not re.search(r'name="sticky_header"\s+checked', html)                       # по умолчанию выкл.
    form = _form(html)
    client.post(reverse("web:settings"), {**form, "sticky_header": "on"})
    assert json.loads(config.read_text(encoding="utf-8"))["sopds"]["sticky_header"] is True
    for on in (False, True):
        with override_config(SOPDS_STICKY_HEADER=on):
            main = client.get(reverse("web:main")).content.decode("utf-8")
        assert ('class="sp-head sp-head--sticky"' in main) is on and 'id="sp-head"' in main
        assert ("scrollPaddingTop" in main) is on
