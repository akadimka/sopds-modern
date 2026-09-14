"""Регрессия для `SynchronizationService._build_folder_structure()` —
docs/quality-roadmap.md, баг №74.

Реальный случай (замечен пользователем): предкомпиляция корректно
показывала, что 12 файлов "Тысяча и одна ночь. В 12 томах" должны
собраться в один компилированный файл, но после синхронизации в
библиотеке оказался только "...т. 1.fb2" размером с ОДИН том — 11
остальных исчезли, не долетев до автокомпиляции.

Причина — все 12 физических файлов несут ОДИНАКОВОЕ `<book-title>`
("Тысяча и одна ночь. В 12 томах" — общее заглавие всей работы, не
различающее том), различаясь только `series_number` (1..12). Дубликат
определялся по `(author, series, title)` — без номера тома. Как только
том 1 оказывался в БД (либо был первым в батче, либо уже был
синхронизирован ранее), тома 2-12 совпадали с ним по этому ключу и
физически удалялись из staging как "уже существующие дубликаты" —
теряя контент, который автокомпиляция как раз должна была объединить.
"""
import sqlite3

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.synchronization import SynchronizationService


def _tom(n):
    return BookRecord(
        file_path=f"Тысяча и одна ночь. Том {n}.fb2",
        file_title="Тысяча и одна ночь. В 12 томах",
        metadata_authors="Народные сказки",
        proposed_author="Автор Неизвестен -- Народные Сказки",
        author_source="folder_dataset",
        metadata_series="Тысяча и одна ночь. В 12 томах",
        proposed_series="Тысяча и одна ночь. В 12 томах",
        series_source="folder_meta_consensus",
        series_number=str(n),
    )


def _make_service(tmp_path, existing_series_number):
    library_path = tmp_path / "library"
    last_scan_path = tmp_path / "staging"
    library_path.mkdir()
    last_scan_path.mkdir()
    db_path = tmp_path / "test_library_cache.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author TEXT, author_source TEXT, series TEXT, series_source TEXT,
            series_number TEXT DEFAULT '', subseries TEXT DEFAULT '',
            title TEXT, file_path TEXT UNIQUE, file_hash TEXT, genre TEXT,
            added_date TEXT, updated_date TEXT, last_sync_check TEXT
        )
    """)
    conn.execute(
        "INSERT INTO books (author, series, series_number, title, file_path) "
        "VALUES (?, ?, ?, ?, ?)",
        ("Автор Неизвестен -- Народные Сказки", "Тысяча и одна ночь. В 12 томах",
         existing_series_number, "Тысяча и одна ночь. В 12 томах",
         "Классическая проза/уже в библиотеке.fb2"),
    )
    conn.commit()
    conn.close()

    service = SynchronizationService.__new__(SynchronizationService)
    service.library_path = library_path
    service.last_scan_path = last_scan_path
    service.db_path = db_path
    service.log_callback = lambda msg: None
    service.logger = None
    service.stats = {'duplicates_found': 0, 'duplicates_deleted': 0, 'errors': 0}
    return service


class TestMultiVolumeSameTitleDistinguishedBySeriesNumber:
    def test_other_volumes_not_deleted_as_duplicates_of_tom_1(self, tmp_path):
        # Том 1 уже "в библиотеке" (как в реальном случае) — тома 2-12
        # приходят в этом же прогоне и не должны считаться его дубликатами.
        service = _make_service(tmp_path, existing_series_number="1")
        records = [_tom(n) for n in range(2, 13)]

        folder_structure, _ = service._build_folder_structure(records, None)

        assert len(folder_structure) == 11
        assert service.stats['duplicates_found'] == 0

    def test_same_volume_still_detected_as_exact_duplicate(self, tmp_path):
        # Sanity: том, УЖЕ реально лежащий в библиотеке (тот же номер тома),
        # по-прежнему корректно определяется как дубликат.
        service = _make_service(tmp_path, existing_series_number="1")
        records = [_tom(1)]

        folder_structure, _ = service._build_folder_structure(records, None)

        assert folder_structure == {}
        assert service.stats['duplicates_found'] == 1
