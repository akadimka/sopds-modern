"""Регрессия для `SynchronizationService._build_folder_structure()` /
`_sanitize_path_component()` — docs/quality-roadmap.md, баг №44.

Реальный случай (замечен пользователем в логе синхронизации): жанр в
метаданных нескольких реальных FB2-файлов оказался испорчен на уровне
ИСХОДНОГО файла — `<genre>??????????</genre>` (10 литеральных '?',
похоже на артефакт старого конвертера, не сумевшего записать
кириллицу). Это значение напрямую использовалось как имя папки:
`target_dir.mkdir()` падал с WinError 123 ("синтаксическая ошибка в
имени файла..."), и ВСЯ синхронизация (0 из 6 файлов перемещено, хотя
только жанр был испорчен, остальные метаданные — исправны) вставала.
"""
from pathlib import Path

from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.synchronization import SynchronizationService, _sanitize_path_component


class TestSanitizePathComponentUnit:
    def test_illegal_chars_stripped(self):
        assert _sanitize_path_component('??????????', 'Без жанра') == 'Без жанра'

    def test_clean_value_untouched(self):
        assert _sanitize_path_component('Боевая фантастика', 'Без жанра') == 'Боевая фантастика'

    def test_partial_illegal_chars_stripped_not_replaced_wholesale(self):
        # Только некоторые символы недопустимы — остальной текст сохраняется.
        assert _sanitize_path_component('Фантастика?', 'Без жанра') == 'Фантастика'


def _make_service(tmp_path):
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.logger = Logger()
    svc.log_callback = None
    svc.db_path = tmp_path / "nonexistent.db"
    svc.stats = {}
    return svc


def _rec(file_path, genre):
    return BookRecord(
        file_path=file_path, file_title=Path(file_path).stem,
        metadata_authors="Автор Тест", proposed_author="Автор Тест", author_source="filename",
        metadata_series="", proposed_series="", series_source="", series_number="",
        metadata_genre=genre,
    )


class TestBuildFolderStructureSanitizesGenre:
    def test_corrupted_genre_falls_back_to_bez_zhanra(self, tmp_path):
        svc = _make_service(tmp_path)
        records = [_rec("Автор Тест - Книга.fb2", "??????????")]
        folder_structure = svc._build_folder_structure(records)
        genre, author, series, subseries = folder_structure["Автор Тест - Книга.fb2"]
        assert genre == "Без жанра"

    def test_clean_genre_still_used_as_is(self, tmp_path):
        svc = _make_service(tmp_path)
        records = [_rec("Автор Тест - Книга.fb2", "sf_boevaya")]
        folder_structure = svc._build_folder_structure(records)
        genre, author, series, subseries = folder_structure["Автор Тест - Книга.fb2"]
        assert genre == "sf_boevaya"
