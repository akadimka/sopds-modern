"""`sync_genre_conflict_resolve()` — выбор жанра для серии, тома которой
получили поровну разные жанры (см. SynchronizationService._unify_series_genres).
Выбранный жанр прописывается в FB2-файлы серии в исходной папке; перенос
делает следующая синхронизация.
"""
import json

import pytest
from django.test import RequestFactory

from fb2parser_web.views import (
    genre_assignment_times, genre_assignments, sync_genre_conflict_resolve, sync_job,
)

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>
<genre>{genre}</genre>
<author><first-name>Иван</first-name><last-name>Волков</last-name></author>
<book-title>Том</book-title>
</title-info></description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""


def _post(payload, admin_user):
    request = RequestFactory().post(
        "/fb2parser/sync/genre-conflict/resolve/",
        data=json.dumps(payload), content_type="application/json",
    )
    request.user = admin_user
    return sync_genre_conflict_resolve(request)


@pytest.fixture(autouse=True)
def _reset_sync_job():
    sync_job.reset()
    genre_assignments.clear()
    genre_assignment_times.clear()
    yield
    sync_job.reset()
    genre_assignments.clear()
    genre_assignment_times.clear()


@pytest.fixture
def conflict(tmp_path):
    scan_path = tmp_path / "staging"
    (scan_path / "src").mkdir(parents=True)
    files = [("src/Кузнец 1.fb2", "Детектив"), ("src/Кузнец 2.fb2", "Фантастика")]
    for rel, genre in files:
        (scan_path / rel).write_text(_FB2.format(genre=genre), encoding="utf-8")
    note = {
        "author": "Волков Иван", "series": "Кузнец",
        "genres": ["Фантастика", "Детектив"], "default_genre": "Фантастика",
        "files": [{"file_path": rel, "genre": g} for rel, g in files],
    }
    sync_job.update(scan_path=str(scan_path), genre_conflict_notes=[note])
    return scan_path, note


def test_chosen_genre_written_into_all_series_files(conflict, admin_user):
    scan_path, _ = conflict
    response = _post({"author": "Волков Иван", "series": "Кузнец", "genre": "Фантастика"}, admin_user)
    data = json.loads(response.content)

    assert response.status_code == 200, data
    assert data["remaining"] == 0
    assert sync_job.get()["genre_conflict_notes"] == []
    for rel in ("src/Кузнец 1.fb2", "src/Кузнец 2.fb2"):
        text = (scan_path / rel).read_text(encoding="utf-8")
        assert "<genre>Фантастика</genre>" in text
        assert "Детектив" not in text
    # Папка остаётся в списке назначенных жанров — её подхватит следующая синхронизация.
    assert genre_assignments.get()[str((scan_path / "src").resolve())] == "Фантастика"


def test_genre_outside_series_options_refused(conflict, admin_user):
    scan_path, note = conflict
    response = _post({"author": "Волков Иван", "series": "Кузнец", "genre": "Проза"}, admin_user)

    assert response.status_code == 400
    assert sync_job.get()["genre_conflict_notes"] == [note]
    assert "<genre>Детектив</genre>" in (scan_path / "src/Кузнец 1.fb2").read_text(encoding="utf-8")


def test_unknown_series_not_found(conflict, admin_user):
    response = _post({"author": "Волков Иван", "series": "Другая", "genre": "Фантастика"}, admin_user)
    assert response.status_code == 404
