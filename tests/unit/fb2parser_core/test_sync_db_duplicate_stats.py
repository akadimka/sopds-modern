"""Регрессия для `SynchronizationService._build_folder_structure()` —
обнаружено пользователем на реальном прогоне синхронизации: отчёт
показывал "точных совпадений (БД/имя файла): 13", но "всего удалено
дублей: 3" — разница выглядела так, будто 10 файлов остались не
удалёнными дубликатами.

На самом деле все 13 совпадений (автор, серия, название) с уже
существующей записью в БД реально удалялись с диска (`os.unlink()` в
этой же ветке) — но `duplicates_deleted` там никогда не увеличивался,
считая только удаления из СОВСЕМ других мест (`_resolve_target_collision`,
`_delete_records_and_files`, дедупликация компиляций). `duplicates_found`
и `duplicates_deleted` из-за этого не совпадали не потому, что что-то не
удалилось, а потому что этот путь удаления просто не учитывался в
`duplicates_deleted`.
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.synchronization import SynchronizationService


def _sync(tmp_path):
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.log_callback = None
    svc._log = lambda msg: None
    svc.last_scan_path = tmp_path
    svc.stats = {'duplicates_found': 0, 'duplicates_deleted': 0, 'errors': 0}
    return svc


class TestDbExactMatchDuplicateCountsInDeletedToo:
    def test_deleted_file_counts_in_both_stats(self, tmp_path):
        source = tmp_path / "dup.fb2"
        source.write_text("<FictionBook/>", encoding="utf-8")

        rec = BookRecord(
            file_path="dup.fb2", file_title="Название",
            metadata_authors="Автор Тест", proposed_author="Автор Тест",
            author_source="metadata", proposed_series="Серия Тест",
            series_source="folder_dataset", metadata_series="",
            series_number="", metadata_genre="Фантастика",
        )

        sync = _sync(tmp_path)
        sync._get_existing_entries = lambda: {("Автор Тест", "Серия Тест", "Название")}

        sync._build_folder_structure([rec])

        assert not source.exists()
        assert sync.stats['duplicates_found'] == 1
        assert sync.stats['duplicates_deleted'] == 1

    def test_missing_file_only_counts_as_found_not_deleted(self, tmp_path):
        # Файл уже отсутствует на диске (напр. удалён более ранним запуском) —
        # находим совпадение по БД, но реально ничего не удаляем.
        rec = BookRecord(
            file_path="already_gone.fb2", file_title="Название",
            metadata_authors="Автор Тест", proposed_author="Автор Тест",
            author_source="metadata", proposed_series="Серия Тест",
            series_source="folder_dataset", metadata_series="",
            series_number="", metadata_genre="Фантастика",
        )

        sync = _sync(tmp_path)
        sync._get_existing_entries = lambda: {("Автор Тест", "Серия Тест", "Название")}

        sync._build_folder_structure([rec])

        assert sync.stats['duplicates_found'] == 1
        assert sync.stats['duplicates_deleted'] == 0
