"""Пути к конвертерам из настроек SOPDS сервер запускает
(`subprocess.Popen([path, src, dst])` в ConvertFB2). Любой, кто мог
открыть настройки, мог вписать /bin/sh как «конвертер» и выполнять
программы на сервере.

Теперь: настройки — только суперпользователю (роль «Суперпользователь»;
«admin» — только имя учётной записи), и пути к конвертерам — только на
известный конвертер, который существует.
"""
import json

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

from opds_catalog.converters import converter_name_allowed, converter_path_error

_STRONG = "Kx7#vQ2!mLp9"


@pytest.mark.parametrize("path, ok", [
    ("/usr/bin/ebook-convert", True),
    (r"C:\Program Files\Calibre2\ebook-convert.exe", True),
    ("/opt/fb2c/fb2c", True),
    ("/bin/sh", False),
    ("/usr/bin/python3", False),
    ("/tmp/ebook-convert.fb2", False),
])
def test_converter_name_allowlist(path, ok):
    assert converter_name_allowed(path) is ok


def test_missing_converter_reported(tmp_path):
    assert converter_path_error(str(tmp_path / "ebook-convert")) == "missing"
    (tmp_path / "ebook-convert").write_text("")
    assert converter_path_error(str(tmp_path / "ebook-convert")) is None
    assert converter_path_error("") is None


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch, settings):
    """View пишет в настоящий config.json — подменяем временным файлом."""
    import fb2parser_core.settings_manager as sm_module
    import opds_catalog.ratings_fetchers as fetchers

    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"sopds": {"fb2toepub": "", "temp_dir": "", "language": "en-US"}}), encoding="utf-8")
    real = sm_module.SettingsManager
    monkeypatch.setattr(sm_module, "SettingsManager", lambda _path: real(str(config)))
    monkeypatch.setattr(fetchers, "wake_or_start", lambda name: None)
    monkeypatch.setattr(fetchers, "stop_fetcher", lambda name: None)
    return lambda: json.loads(config.read_text(encoding="utf-8"))["sopds"]


def _post(client, **fields):
    data = {"language": "ru-RU", "maxitems": "20", "fb2toepub": "", "temp_dir": ""}
    data.update(fields)
    return client.post(reverse("web:settings"), data)


@pytest.fixture
def staff_client(client, db):
    """Бывший «Admin» — is_staff без суперправ (такой роли больше нет)."""
    client.force_login(User.objects.create_user("staff", password=_STRONG, is_staff=True))
    return client


@pytest.fixture
def superuser_client(client, db):
    client.force_login(User.objects.create_superuser("root", password=_STRONG))
    return client


def test_non_superuser_cannot_change_settings(staff_client, isolated_settings):
    response = _post(staff_client, fb2toepub="/bin/sh")
    assert response.status_code == 302 and "/login/" in response["Location"]
    assert isolated_settings()["fb2toepub"] == "" and isolated_settings()["language"] == "en-US"


def test_superuser_saves_other_settings_without_errors(superuser_client, isolated_settings):
    response = _post(superuser_client)
    assert response.status_code == 302
    assert isolated_settings()["language"] == "ru-RU"


def test_superuser_cannot_set_unknown_program(superuser_client, isolated_settings):
    _post(superuser_client, fb2toepub="/bin/sh")
    assert isolated_settings()["fb2toepub"] == ""


def test_superuser_sets_known_existing_converter(superuser_client, isolated_settings, tmp_path):
    conv = tmp_path / "ebook-convert"
    conv.write_text("")
    response = _post(superuser_client, fb2toepub=str(conv), temp_dir=str(tmp_path))
    assert response.status_code == 302
    assert isolated_settings()["fb2toepub"] == str(conv)
    assert isolated_settings()["temp_dir"] == str(tmp_path)


def test_superuser_cannot_set_missing_temp_dir(superuser_client, isolated_settings, tmp_path):
    _post(superuser_client, temp_dir=str(tmp_path / "nope"))
    assert isolated_settings()["temp_dir"] == ""
