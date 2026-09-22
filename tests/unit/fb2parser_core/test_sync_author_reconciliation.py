"""Регрессия для `SynchronizationService._build_folder_structure()` —
docs/quality-roadmap.md, баг №72 доп. (защита от задвоения библиотеки).

Реальный сценарий, поднятый пользователем: книга уже была синхронизирована
в библиотеку РАНЬШЕ, под одиночным автором ("Барчук Павел"), взятым из
имени папки старой версией эвристики. Баг №72 чинит эту эвристику — новый
прогон regen+sync для ТЕХ ЖЕ файлов (например, из свежего "сборника")
теперь корректно определяет соавторство ("Барчук Павел, Ларин Павел").
Без защиты `_build_folder_structure()` не узнаёт уже лежащую в библиотеке
запись (точный ключ (author, series, title) не совпадает — автор другой)
и создаёт ВТОРУЮ копию той же книги под новым именем автора — библиотека
задвоится.

Полное автоматическое перемещение старого файла сознательно НЕ
реализовано (см. обсуждение в docs/quality-roadmap.md) — content-hash
не подходит (пересобранная/дополненная компиляция не совпадёт побайтово
с более ранней версией), а надёжное автоматическое разрешение "какую
версию оставить" требует отдельного, более крупного проектирования.
Вместо этого — безопасное предотвращение: находим loose-совпадение
(та же серия+title, автор — пересекающееся, но не идентичное множество
токенов) и НЕ создаём дубликат, оставляя входящий файл нетронутым с
пометкой "требует ручной сверки".
"""
import sqlite3

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.synchronization import SynchronizationService


def _rec(file_path, author, series, title):
    return BookRecord(
        file_path=file_path, file_title=title, metadata_authors=author,
        proposed_author=author, author_source="folder_dataset",
        metadata_series=series, proposed_series=series, series_source="folder_dataset",
    )


def _make_service(tmp_path, existing_rows):
    """existing_rows: список (author, series, subseries, title, file_path)
    уже "синхронизированных" книг в БД."""
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
    for author, series, subseries, title, file_path in existing_rows:
        conn.execute(
            "INSERT INTO books (author, series, subseries, title, file_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (author, series, subseries, title, file_path),
        )
    conn.commit()
    conn.close()

    service = SynchronizationService.__new__(SynchronizationService)
    service.library_path = library_path
    service.last_scan_path = last_scan_path
    service.db_path = db_path
    service.log_callback = lambda msg: None
    service.logger = None
    service.stats = {'duplicates_found': 0, 'errors': 0}
    return service


class TestLooseAuthorMatchAvoidsSilentDuplication:
    def test_widened_coauthor_flagged_not_duplicated(self, tmp_path):
        service = _make_service(tmp_path, existing_rows=[
            ("Барчук Павел", "ОБХСС", "", "Финал",
             "Без жанра/Барчук Павел/ОБХСС/Барчук Павел - ОБХСС Финал.fb2"),
        ])
        records = [
            _rec("incoming/03_ОБХСС Финал.fb2", "Барчук Павел, Ларин Павел", "ОБХСС", "Финал"),
        ]

        folder_structure, reconciliation_notes = service._build_folder_structure(records, None)

        assert folder_structure == {}
        assert len(reconciliation_notes) == 1
        note = reconciliation_notes[0]
        assert note['incoming_author'] == "Барчук Павел, Ларин Павел"
        assert note['existing_author'] == "Барчук Павел"
        assert note['existing_file_path'].endswith("Барчук Павел - ОБХСС Финал.fb2")

    def test_unrelated_author_sharing_series_and_title_not_flagged(self, tmp_path):
        # Sanity: два совершенно разных автора, никак не пересекающихся по
        # токенам имени, но случайно (в тесте) делящих (серия, title) —
        # НЕ должны считаться "тем же произведением, уточнённым автором".
        service = _make_service(tmp_path, existing_rows=[
            ("Иванов Пётр", "Хроники", "", "Начало",
             "Без жанра/Иванов Пётр/Хроники/файл.fb2"),
        ])
        records = [
            _rec("incoming/файл.fb2", "Сидоров Олег", "Хроники", "Начало"),
        ]

        folder_structure, reconciliation_notes = service._build_folder_structure(records, None)

        assert reconciliation_notes == []
        assert "incoming/файл.fb2" in folder_structure

    def test_skipped_pair_treated_as_ordinary_new_file(self, tmp_path, monkeypatch):
        """Регрессия для действия "Пропустить" на панели reconciliation
        (fb2parser_web.views.sync_reconciliation_resolve) — файл, чью пару
        (incoming, existing) пользователь уже разобрал, не должен снова
        попадать в reconciliation_notes на следующей синхронизации: раз
        skip-лист содержит его incoming_file_path, loose-эвристика
        пропускается целиком и файл идёт обычным новым путём.
        """
        import fb2parser_core.synchronization as sync_module
        skip_path = tmp_path / ".reconciliation_skip.json"
        monkeypatch.setattr(sync_module, "_RECONCILIATION_SKIP_PATH", skip_path)
        sync_module._add_to_reconciliation_skip_set("incoming/03_ОБХСС Финал.fb2")

        service = _make_service(tmp_path, existing_rows=[
            ("Барчук Павел", "ОБХСС", "", "Финал",
             "Без жанра/Барчук Павел/ОБХСС/Барчук Павел - ОБХСС Финал.fb2"),
        ])
        records = [
            _rec("incoming/03_ОБХСС Финал.fb2", "Барчук Павел, Ларин Павел", "ОБХСС", "Финал"),
        ]

        folder_structure, reconciliation_notes = service._build_folder_structure(records, None)

        assert reconciliation_notes == []
        assert "incoming/03_ОБХСС Финал.fb2" in folder_structure

    def test_exact_duplicate_still_deleted_as_before(self, tmp_path):
        # Sanity: точное совпадение (author, series, title) — старое,
        # уже проверенное поведение (файл-дубликат физически удаляется
        # из staging) не должно измениться этим фиксом.
        service = _make_service(tmp_path, existing_rows=[
            ("Барчук Павел", "ОБХСС", "", "Финал", "любой/путь.fb2"),
        ])
        incoming = service.last_scan_path / "incoming.fb2"
        incoming.parent.mkdir(parents=True, exist_ok=True)
        incoming.write_bytes(b"stub")
        records = [
            _rec("incoming.fb2", "Барчук Павел", "ОБХСС", "Финал"),
        ]

        folder_structure, reconciliation_notes = service._build_folder_structure(records, None)

        assert reconciliation_notes == []
        assert folder_structure == {}
        assert not incoming.exists()
