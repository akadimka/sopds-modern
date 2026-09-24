"""Регрессия: страница "Жанровые наборы" (`/fb2parser/genre-scan/`)
откатывала поле "Books folder" на дефолт (`SOPDS_ROOT_LIB`) при каждом
обновлении страницы, если рабочая папка была установлена ТОЛЬКО в
`genre_scan_job` — кэш job-состояния живёт в `LocMemCache` текущего
процесса и пуст сразу после рестарта. Пользователь ожидает, что реально
использованная папка запоминается (в сессии — переживает рестарт
процесса) и сбрасывается на дефолт ТОЛЬКО если сама папка перестала
существовать (удалена/перенесена).
"""
import pytest
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from fb2parser_web.views import genre_scan, genre_scan_job, genre_scan_start

# genre_scan() рендерит полную страницу (extends base.html, {% static %}
# для favicon и т.п.) — в тестовом окружении manifest whitenoise-хранилища
# не собран (нет collectstatic), поэтому подменяем на обычное хранилище
# без манифеста — не влияет на проверяемую логику (root persistence).
_NO_MANIFEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


def _request_with_session(rf, method, path, **kwargs):
    request = getattr(rf, method)(path, **kwargs)
    request.session = SessionStore()
    return request


def _rendered_root(response):
    content = response.content.decode("utf-8")
    marker = 'id="genre-scan-root"'
    idx = content.index(marker)
    value_idx = content.index('value="', idx) + len('value="')
    return content[value_idx:content.index('"', value_idx)]


@pytest.fixture(autouse=True)
def _reset_genre_scan_job():
    # .reset() только очищает сам state-словарь — try_start()'s отдельный
    # lock-ключ переживает reset() (у него своя ветка cache.delete() внутри
    # finish()) — без .finish() лок, взятый в тесте с подменённым
    # (no-op) фоновым потоком, утёк бы в СЛЕДУЮЩИЙ тестовый файл и там
    # try_start() молча отказывал бы в запуске.
    genre_scan_job.reset()
    genre_scan_job.finish()
    yield
    genre_scan_job.reset()
    genre_scan_job.finish()


class TestGenreScanRootPersistsAcrossReload:
    def test_root_survives_job_state_reset(self, tmp_path, admin_user, monkeypatch, settings):
        settings.STORAGES = _NO_MANIFEST_STORAGES
        rf = RequestFactory()
        folder = tmp_path / "library"
        folder.mkdir()
        (folder / "book.fb2").write_text(
            "<?xml version=\"1.0\"?><FictionBook><description><title-info>"
            "<genre>детектив</genre>"
            "<author><first-name>Т</first-name></author>"
            "<book-title>Книга</book-title>"
            "</title-info></description><body><section><p>Т</p></section></body></FictionBook>",
            encoding="utf-8",
        )
        # Не даём реальному фоновому потоку сканирования запуститься — нас
        # интересует только синхронная часть genre_scan_start() (запись в
        # сессию), не сам скан.
        monkeypatch.setattr("fb2parser_web.views._run_genre_scan_thread", lambda folder_paths: None)

        start_request = _request_with_session(
            rf, "post", "/fb2parser/genre-scan/start/",
            data={"root": str(folder), "paths": "[]"},
        )
        start_request.user = admin_user
        genre_scan_start(start_request)
        session = start_request.session

        # Симулируем рестарт процесса — LocMemCache-backed job-состояние
        # (и try_start()'s lock, который наш no-op поток выше никогда не
        # снял бы сам) обнуляется, сохраняется только сессия (наш новый
        # механизм).
        genre_scan_job.reset()
        genre_scan_job.finish()

        get_request = _request_with_session(rf, "get", "/fb2parser/genre-scan/")
        get_request.user = admin_user
        get_request.session = session
        response = genre_scan(get_request)

        assert _rendered_root(response) == str(folder)

    def test_falls_back_to_default_if_saved_folder_gone(self, tmp_path, admin_user, monkeypatch, settings):
        settings.STORAGES = _NO_MANIFEST_STORAGES
        rf = RequestFactory()
        missing_folder = str(tmp_path / "no-longer-there")
        default_folder = tmp_path / "default-lib"
        default_folder.mkdir()

        monkeypatch.setattr("fb2parser_web.views.config.SOPDS_ROOT_LIB", str(default_folder))

        get_request = _request_with_session(rf, "get", "/fb2parser/genre-scan/")
        get_request.user = admin_user
        get_request.session["genre_scan_root"] = missing_folder
        genre_scan_job.reset()

        response = genre_scan(get_request)

        assert _rendered_root(response) == str(default_folder)
