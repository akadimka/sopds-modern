"""Регрессия для `genre_scan_assign()` — docs/quality-roadmap.md, баг №80.

При многопапочном скане жанров (баг №78) `genre_scan_job["folder"]`
стал ключом НАБОРА папок ("C:\\A|C:\\B"), а не единственным путём — но
`genre_scan_assign()` по-прежнему пыталась достроить абсолютный путь
файла как `Path(state["folder"], rel_path)`, что для набора из двух и
более папок гарантированно ломается (объединённая строка — не реальная
папка на диске). Теперь `_run_genre_scan_thread()` сохраняет в
`results[combo]` сразу АБСОЛЮТНЫЕ пути (по СВОЕЙ папке для каждого
файла), и `genre_scan_assign()` использует их напрямую.
"""
from pathlib import Path

import pytest
from django.test import RequestFactory

from fb2parser_web.views import _run_genre_scan_thread, genre_scan_assign, genre_scan_job

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook>
<description>
<title-info>
<genre>{genre}</genre>
<author><first-name>Тест</first-name></author>
<book-title>Книга</book-title>
</title-info>
</description>
<body><section><p>Текст</p></section></body>
</FictionBook>
"""


def _write_fb2(path: Path, genre: str):
    path.write_text(_FB2_TEMPLATE.format(genre=genre), encoding="utf-8")


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture(autouse=True)
def _reset_genre_scan_job():
    genre_scan_job.reset()
    yield
    genre_scan_job.reset()


class TestGenreScanAssignAcrossMultipleFolders:
    def test_assign_resolves_absolute_paths_from_different_folders(self, tmp_path, rf, admin_user):
        folder_a = tmp_path / "a"
        folder_b = tmp_path / "b"
        folder_a.mkdir()
        folder_b.mkdir()
        file_a = folder_a / "1.fb2"
        file_b = folder_b / "2.fb2"
        _write_fb2(file_a, "детектив")
        _write_fb2(file_b, "детектив")

        # Реальный многопапочный скан (не сконструированное вручную
        # состояние) — folder становится объединённым ключом набора папок,
        # а results[combo] — тем, что реально кладёт туда
        # _run_genre_scan_thread (абсолютные пути после фикса).
        genre_scan_job.try_start(folder="", total=2)
        _run_genre_scan_thread([str(folder_a), str(folder_b)])

        import json
        request = rf.post(
            "/fb2parser/genre-scan/assign/",
            data=json.dumps({"mappings": {"детектив": "sf_history"}}),
            content_type="application/json",
        )
        request.user = admin_user
        response = genre_scan_assign(request)

        data = json.loads(response.content)
        assert data["results"]["детектив"]["success"] == 2
        assert data["results"]["детектив"]["failed"] == 0
        assert "<genre>sf_history</genre>" in file_a.read_text(encoding="utf-8")
        assert "<genre>sf_history</genre>" in file_b.read_text(encoding="utf-8")
