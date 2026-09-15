"""Регрессия для `_run_genre_scan_thread()` / `genre_scan_start()` —
docs/quality-roadmap.md, баг №78.

Реальный случай: инструмент извлечения жанров ("Жанровые наборы",
`/fb2parser/genre-scan/`) мог сканировать только ОДНУ папку из
собственного текстового поля — пользователь хотел использовать тот же
набор отмеченных на дашборде папок (`genre_assignments`, тот же список
"Folders to synchronize", что уже используется синхронизацией), в том
числе НЕСКОЛЬКО папок сразу, объединяя результат в один отчёт.
"""
from pathlib import Path

import pytest

from fb2parser_web.fb2parser_bridge import _config_path
from fb2parser_web.views import (
    _genre_scan_cache_load,
    _genre_scan_folder_key,
    _run_genre_scan_thread,
    genre_scan_job,
)

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


class TestGenreScanMergesMultipleFolders:
    def test_results_merged_across_folders(self, tmp_path):
        folder_a = tmp_path / "a"
        folder_b = tmp_path / "b"
        folder_a.mkdir()
        folder_b.mkdir()
        _write_fb2(folder_a / "1.fb2", "детектив")
        _write_fb2(folder_b / "2.fb2", "детектив")
        _write_fb2(folder_b / "3.fb2", "фантастика")

        genre_scan_job.try_start(folder="", total=3)
        _run_genre_scan_thread([str(folder_a), str(folder_b)])

        state = genre_scan_job.get()
        assert state["done"] is True
        assert len(state["results"]["детектив"]) == 2
        assert len(state["results"]["фантастика"]) == 1
        assert state["processed"] == 3

    def test_cache_survives_under_joined_folder_key(self, tmp_path):
        folder_a = tmp_path / "a"
        folder_b = tmp_path / "b"
        folder_a.mkdir()
        folder_b.mkdir()
        _write_fb2(folder_a / "1.fb2", "детектив")
        _write_fb2(folder_b / "2.fb2", "фантастика")

        genre_scan_job.try_start(folder="", total=2)
        _run_genre_scan_thread([str(folder_a), str(folder_b)])

        key = _genre_scan_folder_key([str(folder_a), str(folder_b)])
        cached = _genre_scan_cache_load(key)
        assert cached is not None
        results, errors = cached
        assert set(results.keys()) == {"детектив", "фантастика"}
