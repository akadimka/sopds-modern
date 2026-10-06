"""Настройки автосинхронизации на странице настроек SOPDS.

Папка наблюдения определяет, откуда автосинхронизация перемещает и
удаляет файлы, поэтому её (и режим) меняет только суперпользователь, а
пересечение с библиотекой запрещено (docs/watch-folder-autosync-design.md).
"""
import json

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

_STRONG = "Kx7#vQ2!mLp9"


@pytest.fixture
def env(tmp_path, monkeypatch, settings):
    import fb2parser_core.settings_manager as sm_module
    import opds_catalog.ratings_fetchers as fetchers

    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    library = tmp_path / "library"
    watch = tmp_path / "incoming"
    library.mkdir()
    watch.mkdir()
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"library_path": str(library),
                                  "sopds": {"fb2toepub": "", "temp_dir": "", "language": "en-US"}}),
                      encoding="utf-8")
    real = sm_module.SettingsManager
    monkeypatch.setattr(sm_module, "SettingsManager", lambda _path: real(str(config)))
    monkeypatch.setattr(fetchers, "wake_or_start", lambda name: None)
    monkeypatch.setattr(fetchers, "stop_fetcher", lambda name: None)

    def autosync():
        return json.loads(config.read_text(encoding="utf-8")).get("autosync", {})

    return {"library": library, "watch": watch, "autosync": autosync, "app": tmp_path / "app_settings.json"}


def _post(client, **fields):
    data = {"language": "ru-RU", "maxitems": "20", "fb2toepub": "", "temp_dir": ""}
    data.update(fields)
    return client.post(reverse("web:settings"), data)


def _login(client, superuser):
    if superuser:
        user = User.objects.create_superuser("root", password=_STRONG)
    else:
        user = User.objects.create_user("staff", password=_STRONG, is_staff=True)
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_superuser_enables_autosync(client, env):
    response = _post(_login(client, True), autosync_mode="dry_run", autosync_watch_folder=str(env["watch"]),
                     autosync_confidence="0.85", autosync_quiet_minutes="45")
    assert response.status_code == 302
    saved = env["autosync"]()
    assert saved["mode"] == "dry_run" and saved["watch_folder"] == str(env["watch"])
    assert saved["confidence"] == 0.85 and saved["quiet_minutes"] == 45
    assert not env["app"].exists() or "autosync" not in env["app"].read_text(encoding="utf-8")


@pytest.mark.django_db
def test_staff_cannot_change_autosync(client, env):
    _post(_login(client, False), autosync_mode="auto", autosync_watch_folder=str(env["watch"]))
    assert env["autosync"]().get("mode", "off") == "off"


@pytest.mark.django_db
def test_watch_folder_inside_library_is_rejected(client, env):
    inside = env["library"] / "Фантастика"
    inside.mkdir()
    response = _post(_login(client, True), autosync_mode="auto", autosync_watch_folder=str(inside))
    assert response.status_code == 200  # форма с ошибкой
    saved = env["autosync"]()
    assert saved["watch_folder"] == "" and saved["mode"] == "off"


@pytest.mark.django_db
def test_missing_watch_folder_is_rejected(client, env):
    response = _post(_login(client, True), autosync_mode="auto",
                     autosync_watch_folder=str(env["watch"] / "нет такой"))
    assert response.status_code == 200
    assert env["autosync"]()["mode"] == "off"


# ---------- Telegram ----------

def _tg(env, **extra):
    data = {"autosync_mode": "off", "autosync_telegram_channel": "@library_news",
            "autosync_telegram_admin_chat": "123456789", "autosync_public_url": "https://books.example.org/"}
    data.update(extra)
    return data


@pytest.mark.django_db
def test_telegram_token_saved_but_never_rendered(client, env):
    c = _login(client, True)
    _post(c, **_tg(env, autosync_telegram_token="7770001:SECRETTOKENxxxxxxxxxxxxxxxxxxxxxxxxxx"))
    saved = env["autosync"]()
    assert saved["telegram_token"] == "7770001:SECRETTOKENxxxxxxxxxxxxxxxxxxxxxxxxxx" and saved["telegram_channel"] == "@library_news"
    assert saved["public_url"] == "https://books.example.org"

    page = c.get(reverse("web:settings")).content.decode("utf-8")
    assert "SECRETTOKEN" not in page and "…xxxx" in page  # только хвост в подсказке

    _post(c, **_tg(env))  # пустое поле — токен остаётся
    assert env["autosync"]()["telegram_token"] == "7770001:SECRETTOKENxxxxxxxxxxxxxxxxxxxxxxxxxx"
    _post(c, **_tg(env, autosync_telegram_token_clear="on"))
    assert env["autosync"]()["telegram_token"] == ""


@pytest.mark.django_db
def test_telegram_bad_values_rejected(client, env):
    response = _post(_login(client, True), **_tg(env, autosync_telegram_admin_chat="мой чат",
                                                 autosync_public_url="books.example.org"))
    assert response.status_code == 200
    saved = env["autosync"]()
    assert saved["telegram_admin_chat"] == "" and saved["public_url"] == ""


class _FakeClient:
    sent = []

    def __init__(self, token, proxy=""):
        self.token = token

    def send_message(self, chat, text):
        _FakeClient.sent.append((self.token, chat))

    def chats(self):
        return [{"id": "42", "type": "private", "title": "Дмитрий"}]


@pytest.mark.django_db
def test_telegram_buttons_superuser_only(client, env, monkeypatch):
    import fb2parser_core.telegram_notify as tn
    monkeypatch.setattr(tn, "TelegramClient", _FakeClient)
    _FakeClient.sent = []

    staff = _login(client, False)
    assert staff.post(reverse("web:telegram_test"), _tg(env)).status_code in (302, 403)

    client.logout()
    su = _login(client, True)
    body = su.post(reverse("web:telegram_test"), _tg(env, autosync_telegram_token="9:T")).content.decode("utf-8")
    assert body.count("✅") == 2
    assert _FakeClient.sent == [("9:T", "@library_news"), ("9:T", "123456789")]
    chats = su.post(reverse("web:telegram_chats"), {"autosync_telegram_token": "9:T"}).content.decode("utf-8")
    assert "<code>42</code>" in chats and "Дмитрий" in chats


_BOTFATHER = ("Done! Congratulations on your new bot. You will find it at t.me/my_news_bot. "
              "Use this token to access the HTTP API:\n"
              "7712345678:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw\n"
              "Keep your token secure and store it safely, it can be used by anyone to control your bot.")


@pytest.mark.django_db
def test_whole_botfather_message_pasted_saves_only_the_token(client, env):
    _post(_login(client, True), **_tg(env, autosync_telegram_token=_BOTFATHER))
    assert env["autosync"]()["telegram_token"] == "7712345678:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


@pytest.mark.django_db
def test_text_without_token_is_rejected_and_old_token_kept(client, env):
    c = _login(client, True)
    _post(c, **_tg(env, autosync_telegram_token="7712345678:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"))
    response = _post(c, **_tg(env, autosync_telegram_token="admin-password"))
    assert response.status_code == 200
    assert env["autosync"]()["telegram_token"] == "7712345678:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"


@pytest.mark.django_db
def test_token_field_is_not_a_password_field(client, env):
    # иначе менеджер паролей браузера подставляет логин сайта в соседние поля
    page = _login(client, True).get(reverse("web:settings")).content.decode("utf-8")
    assert 'type="password"' not in page and 'name="autosync_telegram_token"' in page


@pytest.mark.django_db
def test_find_chat_id_while_bot_process_runs_shows_remembered_chats(client, env, monkeypatch, tmp_path):
    from types import SimpleNamespace

    import fb2parser_core.telegram_notify as tn
    import fb2parser_web.fb2parser_bridge as bridge
    from fb2parser_core.autosync_service import AutosyncJournal
    from fb2parser_core.telegram_bot import remember_chats

    class _Busy(_FakeClient):
        def chats(self):
            raise tn.TelegramError("Conflict: terminated by other getUpdates request", 409)

    journal = AutosyncJournal(tmp_path / "journal.db")
    remember_chats(journal, [{"update_id": 1, "channel_post": {"chat": {"id": -1001, "type": "channel",
                                                                         "title": "Новинки"}}}])
    monkeypatch.setattr(tn, "TelegramClient", _Busy)
    monkeypatch.setattr(bridge, "get_autosync_service", lambda: SimpleNamespace(journal=journal))

    body = _login(client, True).post(reverse("web:telegram_chats"),
                                     {"autosync_telegram_token": "9:T"}).content.decode("utf-8")
    assert "<code>-1001</code>" in body and "Новинки" in body
