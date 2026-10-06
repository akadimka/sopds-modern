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
