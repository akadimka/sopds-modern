"""Регрессия для `_run_broken_files_thread()`/`broken_files_start()` —
docs/quality-roadmap.md, баг №99.

Раньше `broken_files_list` сканировала и парсила ВСЮ библиотеку
СИНХРОННО внутри самого HTTP-запроса — тот же анти-паттерн, который
команда уже нашла и исправила для присвоения жанров (`genre_assign_job`):
на большой библиотеке это не укладывается в таймаут воркера gunicorn
(WEB_TIMEOUT, 120с), воркер убивается посреди работы.
"""
import pytest
from django.test import RequestFactory

from fb2parser_web.views import (
    _run_broken_files_thread,
    broken_files_job,
    broken_files_start,
    norm_job,
)

_GOOD_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook>
<description><title-info>
<genre>prose</genre>
<author><first-name>Тест</first-name></author>
<book-title>Хорошая книга</book-title>
</title-info></description>
<body><section><p>Полный текст.</p></section></body>
</FictionBook>
"""

_INCOMPLETE_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook>
<description><title-info>
<genre>prose</genre>
<author><first-name>Тест</first-name></author>
<book-title>Неполная книга</book-title>
</title-info></description>
<body><section><p>Конец ознакомительного фрагмента.</p></section></body>
</FictionBook>
"""


@pytest.fixture(autouse=True)
def _reset_jobs():
    broken_files_job.reset()
    norm_job.reset()
    yield
    broken_files_job.reset()
    norm_job.reset()


class TestRunBrokenFilesThread:
    def test_classifies_good_broken_and_incomplete_files(self, tmp_path):
        (tmp_path / "good.fb2").write_text(_GOOD_FB2, encoding="utf-8")
        (tmp_path / "incomplete.fb2").write_text(_INCOMPLETE_FB2, encoding="utf-8")
        (tmp_path / "broken.fb2").write_text("not even xml", encoding="utf-8")

        broken_files_job.try_start(folder=str(tmp_path))
        _run_broken_files_thread(str(tmp_path))

        state = broken_files_job.get()
        assert state["done"] is True
        assert state["running"] is False
        assert state["processed"] == 3
        assert state["total"] == 3

        by_file = {r["file_path"]: r["reason"] for r in state["rows"]}
        assert by_file == {"incomplete.fb2": "incomplete", "broken.fb2": "broken"}
        assert "good.fb2" not in by_file

    def test_progress_updated_incrementally(self, tmp_path):
        """Прогресс должен обновляться по мере обработки каждого файла —
        не одним скачком в конце (иначе это снова синхронная работа,
        просто спрятанная за JobState)."""
        for i in range(5):
            (tmp_path / f"book{i}.fb2").write_text(_GOOD_FB2, encoding="utf-8")

        seen_processed_values = []
        original_update = broken_files_job.update

        def _spy_update(*args, **kwargs):
            result = original_update(*args, **kwargs)
            if "processed" in kwargs:
                seen_processed_values.append(kwargs["processed"])
            return result

        broken_files_job.try_start(folder=str(tmp_path))
        broken_files_job.update = _spy_update
        try:
            _run_broken_files_thread(str(tmp_path))
        finally:
            broken_files_job.update = original_update

        # Хотя бы несколько промежуточных значений 1..5, не только финальное.
        assert sorted(set(seen_processed_values)) == [1, 2, 3, 4, 5]


class TestBrokenFilesStartView:
    # Запуск реального threading.Thread из вьюхи не тестируем напрямую —
    # это гонка (поток на маленькой библиотеке может успеть завершиться
    # раньше, чем вьюха вернёт ответ); тот же подход уже принят в этом
    # проекте для остальных JobState-вьюх (см. test_genre_scan_multi_folder.py,
    # test_main_scan_scoped.py — они тестируют _run_*_thread() напрямую,
    # не сам *_start()). Здесь тестируем только детерминированный ранний
    # выход "папка не настроена", до какого-либо запуска потока.
    def test_requires_folder_configured(self, admin_user, override_config, tmp_path):
        norm_job.update(folder="")
        # norm_job.folder пуст -> вьюха падает на fallback SOPDS_ROOT_LIB;
        # явно делаем и его "не существующим", иначе тест зависит от того,
        # что реально настроено в config.json дев-окружения.
        with override_config(SOPDS_ROOT_LIB=str(tmp_path / "does-not-exist")):
            rf = RequestFactory()
            request = rf.post("/fb2parser/normalize/broken-files/start/")
            request.user = admin_user

            response = broken_files_start(request)

        assert "Сначала создайте CSV" in response.content.decode("utf-8")
        assert broken_files_job.get()["running"] is False
