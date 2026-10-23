"""Режим «На реконструкции» (sopds_web_backend/maintenance.py): сайт, OPDS и
Telegram-бот закрыты для всех, кроме суперпользователя на сайте.

Главное — обход адресов: для КАЖДОГО маршрута сайта, OPDS, fb2parser и
/admin/ строится настоящий адрес, и аноним и обычный пользователь получают
503, кроме белого списка (вход, код, выход, смена пароля, язык, сервис-воркер,
«нет сети»). Новый адрес, забытый в правилах, уронит этот тест. Отдельно —
«хитрые» адреса (параметры, ../, хвосты к /login/).
"""
import base64
import re

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client
from django.urls import URLResolver, get_resolver, resolve
from django.urls.exceptions import Resolver404

from sopds_web_backend.maintenance import ALLOWED

pytestmark = pytest.mark.django_db
STRONG = "Kx7#vQ2!mLp9"


@pytest.fixture(autouse=True)
def plain_static(settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


@pytest.fixture
def closed(override_config):
    with override_config(SOPDS_MAINTENANCE=True, SOPDS_MAINTENANCE_MESSAGE="Вернёмся к 18:00", SOPDS_AUTH=True):
        yield


def _fill(regex: str) -> str:
    """Регулярное выражение маршрута → пример адреса: группы → «1»."""
    out, i = [], 0
    while i < len(regex):
        if regex[i] == "(":                          # группа (в т.ч. вложенные) → «1»
            depth, j = 0, i
            while j < len(regex):
                if regex[j] == "\\":
                    j += 2
                    continue
                depth += {"(": 1, ")": -1}.get(regex[j], 0)
                if depth == 0:
                    break
                j += 1
            out.append("1")
            i = j + 1
            if i < len(regex) and regex[i] in "?*+":
                i += 1
            continue
        if regex[i] == "\\" and i + 1 < len(regex):
            out.append(regex[i + 1])
            i += 2
            continue
        if regex[i] not in "^$?":
            out.append(regex[i])
        i += 1
    return "".join(out)


def _all_paths():
    paths = []

    def walk(resolver, prefix):
        for p in resolver.url_patterns:
            piece = str(p.pattern)
            piece = re.sub(r"<(?:\w+:)?\w+>", "1", piece)           # path("…/<int:id>/")
            piece = _fill(piece)
            if isinstance(p, URLResolver):
                walk(p, prefix + piece)
            else:
                paths.append("/" + prefix + piece)

    walk(get_resolver(), "")
    resolved = []
    for path in paths:
        try:
            match = resolve(path)
        except Resolver404:
            continue
        if match.namespace == "djdt":
            continue
        resolved.append((path, (match.namespace, match.url_name)))
    return paths, resolved


def test_every_address_is_closed_except_the_allowlist(closed):
    paths, resolved = _all_paths()
    assert len(resolved) >= 0.8 * len(paths) and len(resolved) > 50        # обход действительно что-то обходит
    reader = Client()
    reader.force_login(User.objects.create_user("reader", password=STRONG))
    leaks = []
    for client, who in ((Client(), "anonymous"), (reader, "user")):
        for path, key in resolved:
            status = client.get(path).status_code
            if (key in ALLOWED) == (status == 503):
                leaks.append((who, path, key, status))
    assert not leaks, leaks


@pytest.mark.parametrize("path", [
    "/web/?login", "/web/?next=/web/login/", "/web/login/../", "/web/login/x", "/web/login/../settings/",
    "/web/settings/users/", "/web/online/", "/opds/", "/opds/download/1/0/", "/admin/", "/fb2parser/",
    "/nonexistent/", "/web/login/%2e%2e/main/",
])
def test_tricky_addresses_stay_closed(closed, path):
    reader = Client()
    reader.force_login(User.objects.create_user("reader", password=STRONG))
    assert reader.get(path).status_code == 503 and Client().get(path).status_code == 503


def test_maintenance_page_says_why_and_leaks_nothing(closed):
    response = Client().get("/web/")
    html = response.content.decode("utf-8")
    assert response.status_code == 503 and response["Retry-After"] == "3600"
    assert "Вернёмся к 18:00" in html and "under maintenance" in html
    assert "searchterms" not in html and "sopds-top-bar" not in html             # ни поиска, ни меню


def test_user_can_sign_in_but_still_sees_maintenance(closed):
    User.objects.create_user("reader", password=STRONG)
    browser = Client()
    page = browser.get("/web/login/")
    assert page.status_code == 200 and "only a superuser can sign in" in page.content.decode("utf-8")
    assert browser.post("/web/login/", {"username": "reader", "password": STRONG}).status_code == 302
    assert browser.get("/web/").status_code == 503


def test_superuser_works_on_the_site_with_a_reminder(closed):
    User.objects.create_superuser("root", password=STRONG)
    browser = Client()
    browser.post("/web/login/", {"username": "root", "password": STRONG})
    main = browser.get("/web/")
    assert main.status_code == 200 and "sp-maintenance-banner" in main.content.decode("utf-8")
    for url in ("/web/settings/", "/web/settings/users/", "/fb2parser/", "/admin/"):
        assert browser.get(url).status_code == 200, url


def test_opds_closed_for_everyone_including_superuser(closed):
    User.objects.create_superuser("root", password=STRONG)
    auth = "Basic " + base64.b64encode(f"root:{STRONG}".encode()).decode()
    response = Client().get("/opds/", HTTP_AUTHORIZATION=auth)
    assert response.status_code == 503 and "Вернёмся к 18:00" in response.content.decode("utf-8")
    browser = Client()
    browser.force_login(User.objects.get(username="root"))
    assert browser.get("/opds/").status_code == 503


def test_htmx_requests_reload_the_page(closed):
    reader = Client()
    reader.force_login(User.objects.create_user("reader", password=STRONG))
    response = reader.get("/web/online/", HTTP_HX_REQUEST="true")
    assert response.status_code == 503 and response["HX-Refresh"] == "true"


def test_off_by_default(override_config):
    reader = Client()
    reader.force_login(User.objects.create_user("reader", password=STRONG))
    assert reader.get("/web/").status_code == 200


def test_management_command_turns_it_on_and_off(tmp_path, monkeypatch):
    import json

    import opds_catalog.sopds_config as sopds_config

    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sopds": {}}), encoding="utf-8")
    monkeypatch.setattr(sopds_config, "_CONFIG_PATH", str(config))
    sopds_config._sm_cache.update(config_mtime=None, app_settings_mtime=None, sm=None)
    call_command("maintenance", "on", "--message", "До вечера")
    saved = json.loads(config.read_text(encoding="utf-8"))["sopds"]
    assert saved["maintenance"] is True and saved["maintenance_message"] == "До вечера"
    call_command("maintenance", "off")
    assert json.loads(config.read_text(encoding="utf-8"))["sopds"]["maintenance"] is False
    sopds_config._sm_cache.update(config_mtime=None, app_settings_mtime=None, sm=None)


def test_bot_answers_maintenance_and_does_nothing_else(monkeypatch):
    from types import SimpleNamespace

    from fb2parser_core.telegram_bot import AdminBot

    class FakeClient:
        def __init__(self):
            self.messages, self.answers = [], []

        def get_updates(self, offset, timeout=None):
            return [{"update_id": 5, "message": {"chat": {"id": 42, "type": "private"}, "text": "/start"}},
                    {"update_id": 6, "callback_query": {"id": "cq1", "data": "d:1:sf", "from": {"id": 7}}}]

        def send_message(self, chat, text, reply_markup=None):
            self.messages.append((chat, text))

        def answer_callback(self, cid, text=""):
            self.answers.append((cid, text))

    client, handled = FakeClient(), []
    service = SimpleNamespace(cfg={"telegram_token": "1:x", "telegram_admin_chat": "7"},
                              journal=SimpleNamespace(set_state=lambda *a: None, get_state=lambda *a: None))
    bot = AdminBot(lambda: service, client_factory=lambda cfg: client,
                   extra_handler=lambda *a: handled.append(a) or True, maintenance=lambda: "🛠 ремонт")
    import fb2parser_core.telegram_bot as tb
    monkeypatch.setattr(tb, "remember_chats", lambda journal, updates: None)
    assert bot.poll_once(None) == 7
    assert client.messages == [("42", "🛠 ремонт")] and client.answers == [("cq1", "🛠 ремонт")] and handled == []
