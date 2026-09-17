"""Регрессия для `_run_assign_genre_thread()` / `assign_genre_multi()`.

Реальный случай (папка "Книжная полка Дозора" — 2458 файлов, 14.6 ГБ):
присвоение жанра папке выполнялось СИНХРОННО внутри самого HTTP-запроса.
На больших папках операция не укладывалась в таймаут воркера gunicorn
(WEB_TIMEOUT, 120с по умолчанию) — воркер убивался посреди работы, а с
точки зрения браузера это выглядело как зависание навечно без какой-либо
обратной связи. Присвоение жанра теперь выполняется фоновым потоком с
прогрессом на уровне файлов — тот же JobState-паттерн, что и у
genre_scan_job (см. test_genre_scan_multi_folder.py).
"""
from pathlib import Path

from fb2parser_web.views import _run_assign_genre_thread, genre_assign_job

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


def _write_fb2(path: Path, genre: str = "старый-жанр"):
    path.write_text(_FB2_TEMPLATE.format(genre=genre), encoding="utf-8")


class TestAssignGenreBackgroundJob:
    def test_genre_assigned_across_multiple_folders_with_full_progress(self, tmp_path):
        folder_a = tmp_path / "a"
        folder_b = tmp_path / "b"
        folder_a.mkdir()
        folder_b.mkdir()
        _write_fb2(folder_a / "1.fb2")
        _write_fb2(folder_b / "2.fb2")
        _write_fb2(folder_b / "3.fb2")

        genre_assign_job.try_start(total=3, results=[])
        _run_assign_genre_thread("детская литература", [str(folder_a), str(folder_b)])

        state = genre_assign_job.get()
        assert state["done"] is True
        assert state["running"] is False
        assert state["processed"] == 3
        assert state["error"] is None

        results_by_path = {r["path"]: r for r in state["results"]}
        assert results_by_path[str(folder_a)]["success"] is True
        assert results_by_path[str(folder_a)]["count"] == 1
        assert results_by_path[str(folder_b)]["success"] is True
        assert results_by_path[str(folder_b)]["count"] == 2

        assert (folder_a / "1.fb2").read_text(encoding="utf-8").count("<genre>детская литература</genre>") == 1
        assert (folder_b / "2.fb2").read_text(encoding="utf-8").count("<genre>детская литература</genre>") == 1

    def test_progress_advances_past_prior_folders_base_offset(self, tmp_path):
        """Прогресс должен идти по файлам КУМУЛЯТИВНО через все папки, а не
        сбрасываться на 0 при переходе к следующей — иначе прогресс-бар
        дёргается назад посреди одного запуска.
        """
        folder_a = tmp_path / "a"
        folder_b = tmp_path / "b"
        folder_a.mkdir()
        folder_b.mkdir()
        _write_fb2(folder_a / "1.fb2")
        _write_fb2(folder_a / "2.fb2")
        _write_fb2(folder_b / "3.fb2")

        seen_processed = []
        original_update = genre_assign_job.update

        def _spy_update(*args, **kwargs):
            result = original_update(*args, **kwargs)
            if "processed" in kwargs:
                seen_processed.append(kwargs["processed"])
            return result

        genre_assign_job.try_start(total=3, results=[])
        try:
            genre_assign_job.update = _spy_update
            _run_assign_genre_thread("фантастика", [str(folder_a), str(folder_b)])
        finally:
            genre_assign_job.update = original_update

        # Монотонно неубывающая последовательность — никогда не откатывается назад.
        assert seen_processed == sorted(seen_processed)
        assert seen_processed[-1] == 3

    def test_missing_folder_reported_as_error_without_blocking_others(self, tmp_path):
        folder_ok = tmp_path / "ok"
        folder_ok.mkdir()
        _write_fb2(folder_ok / "1.fb2")
        missing = str(tmp_path / "does-not-exist")

        genre_assign_job.try_start(total=1, results=[])
        _run_assign_genre_thread("фантастика", [missing, str(folder_ok)])

        state = genre_assign_job.get()
        results_by_path = {r["path"]: r for r in state["results"]}
        assert results_by_path[missing]["success"] is False
        assert results_by_path[str(folder_ok)]["success"] is True
