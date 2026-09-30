"""Жанры на главной: вид «По наборам жанров» (`genre_scan_results`) заменил
отдельную страницу Actions → Genre Combinations.
"""
import pytest
from django.test import RequestFactory

from fb2parser_web.views import (
    _genre_scan_cache_save,
    _genre_scan_folder_key,
    genre_scan_files,
    genre_scan_job,
    genre_scan_results,
    genre_scan_stop_flag,
    main_scan_start,
    main_scan_stop,
)

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook><description><title-info>
<genre>{genre}</genre><book-title>Книга</book-title>
</title-info></description><body><section><p>Текст</p></section></body></FictionBook>
"""


@pytest.fixture(autouse=True)
def _reset():
    genre_scan_job.reset()
    genre_scan_stop_flag.clear()
    yield
    # Подменённый поток скана не вызывает finish() — снимаем блокировку
    # запуска сами, иначе следующий тест не сможет стартовать скан.
    genre_scan_job.finish()
    genre_scan_job.reset()
    genre_scan_stop_flag.clear()


def _get(view, url, admin_user, session, **params):
    request = RequestFactory().get(url, params)
    request.user = admin_user
    request.session = session
    return view(request)


def test_results_restored_from_disk_after_restart(tmp_path, admin_user, monkeypatch):
    folder = tmp_path / "books"
    folder.mkdir()
    (folder / "1.fb2").write_text(_FB2.format(genre="sf_space"), encoding="utf-8")
    monkeypatch.setattr("fb2parser_web.views._run_genre_scan_thread", lambda folder_paths: None)

    session = {}
    request = RequestFactory().post("/fb2parser/main-scan/start/", {"root": str(folder)})
    request.user = admin_user
    request.session = session
    main_scan_start(request)

    key = _genre_scan_folder_key([str(folder)])
    assert session["genre_scan_key"] == key
    _genre_scan_cache_save(key, {"sf_space": [str(folder / "1.fb2")]}, [])

    # Рестарт сервера: состояние задачи в памяти пропало.
    genre_scan_job.reset()
    html = _get(genre_scan_results, "/fb2parser/genre-scan/results/", admin_user, session).content.decode()

    assert 'data-combo="sf_space"' in html
    assert genre_scan_job.get()["done"] is True


def test_results_picker_does_not_reuse_folder_tree_callback(admin_user):
    # Панель встроена в dashboard.html, где gpSelectGenre — picker дерева папок.
    genre_scan_job.update(done=True, running=False, results={"sf": ["a.fb2"]}, errors=[])
    html = _get(genre_scan_results, "/fb2parser/genre-scan/results/", admin_user, {}).content.decode()
    assert "cb=gsSelectGenre" in html
    assert "window.gpSelectGenre" not in html


def test_files_panel_links_each_code_to_genre_reference(admin_user):
    genre_scan_job.update(done=True, running=False, results={"sf_space, det_classic": ["a.fb2"]}, errors=[])
    html = _get(
        genre_scan_files, "/fb2parser/genre-scan/files/", admin_user, {}, combo="sf_space, det_classic",
    ).content.decode()
    assert "?code=sf_space" in html
    assert "?code=det_classic" in html


def test_stop_sets_flag_for_running_scan(admin_user):
    request = RequestFactory().post("/fb2parser/main-scan/stop/")
    request.user = admin_user
    response = main_scan_stop(request)
    assert response.status_code == 200
    assert genre_scan_stop_flag.is_set()
