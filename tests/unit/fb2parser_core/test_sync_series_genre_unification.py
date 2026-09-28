"""Тома одной серии не должны разъезжаться по разным жанровым папкам
библиотеки — `SynchronizationService._unify_series_genres()`.

Жанр папки берётся из `metadata_genre` каждого файла отдельно, и один том с
ошибочным кодом жанра уводил серию в две жанровые папки. Большинство томов
определяет жанр всей серии; при равенстве (например, 1 том против 1)
серия не перемещается и ждёт ручного выбора жанра.
"""
import sqlite3
from types import SimpleNamespace

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.synchronization import SynchronizationService


def _fs(*entries):
    """entries: (file_path, genre, author, series)"""
    return {p: (g, a, "", s, "") for p, g, a, s in entries}


def _service(priority=()):
    service = SynchronizationService.__new__(SynchronizationService)
    service.log_callback = lambda msg: None
    service.logger = None
    service.settings = SimpleNamespace(get_genre_priority_order=lambda: list(priority))
    return service


class TestUnifySeriesGenres:
    def test_majority_genre_applied_to_whole_series(self):
        fs = _fs(
            ("s/1.fb2", "Фантастика", "Волков Иван", "Кузнец"),
            ("s/2.fb2", "Детектив", "Волков Иван", "Кузнец"),
            ("s/3.fb2", "Фантастика", "Волков Иван", "Кузнец"),
        )
        unified, conflicts = _service()._unify_series_genres(fs)

        assert {g for g, *_ in fs.values()} == {"Фантастика"}
        assert conflicts == []
        assert unified == [{
            "author": "Волков Иван", "series": "Кузнец", "genre": "Фантастика",
            "overridden": [{"file_path": "s/2.fb2", "genre": "Детектив"}],
        }]

    def test_tie_leaves_series_out_of_structure_for_manual_choice(self):
        fs = _fs(
            ("s/1.fb2", "Детектив", "Волков Иван", "Кузнец"),
            ("s/2.fb2", "Фантастика", "Волков Иван", "Кузнец"),
            ("other.fb2", "Проза", "Волков Иван", "Другая"),
        )
        unified, conflicts = _service(priority=["Фантастика", "Детектив"])._unify_series_genres(fs)

        assert set(fs) == {"other.fb2"}
        assert unified == []
        assert len(conflicts) == 1
        note = conflicts[0]
        assert note["genres"] == ["Фантастика", "Детектив"]
        assert note["default_genre"] == "Фантастика"
        assert {f["file_path"]: f["genre"] for f in note["files"]} == {
            "s/1.fb2": "Детектив", "s/2.fb2": "Фантастика",
        }

    def test_tie_default_without_priority_is_alphabetical(self):
        fs = _fs(
            ("s/1.fb2", "Фантастика", "Волков Иван", "Кузнец"),
            ("s/2.fb2", "Детектив", "Волков Иван", "Кузнец"),
        )
        _, conflicts = _service()._unify_series_genres(fs)
        assert conflicts[0]["default_genre"] == "Детектив"

    def test_standalone_books_and_other_series_untouched(self):
        fs = _fs(
            ("a.fb2", "Фантастика", "Волков Иван", ""),
            ("b.fb2", "Детектив", "Волков Иван", ""),
            ("c.fb2", "Фантастика", "Волков Иван", "Кузнец"),
            ("d.fb2", "Детектив", "Петров Пётр", "Кузнец"),
        )
        before = dict(fs)
        unified, conflicts = _service()._unify_series_genres(fs)
        assert fs == before
        assert unified == [] and conflicts == []


_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>
<genre>{genre}</genre>
<author><first-name>Иван</first-name><last-name>Волков</last-name></author>
<book-title>{title}</book-title>
<sequence name="{series}" number="{n}"/>
</title-info></description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""


def _rec(file_path, series, title, n, genre):
    return BookRecord(
        file_path=file_path, file_title=title, metadata_authors="Иван Волков",
        proposed_author="Волков Иван", author_source="folder_dataset",
        metadata_series=series, proposed_series=series, series_source="folder_dataset",
        series_number=str(n), metadata_genre=genre,
    )


class TestSynchronizeKeepsConflictingSeriesInPlace:
    """Файлы серии с неразрешённым жанром отсутствуют в folder_structure, а
    _move_files() удаляет всё, чего там нет, как дубликат. Проверяем, что
    synchronize() их не удаляет и не переносит."""

    def test_tied_series_not_deleted_majority_series_moved_together(self, tmp_path, monkeypatch):
        library, staging = tmp_path / "library", tmp_path / "staging"
        library.mkdir()
        (staging / "src").mkdir(parents=True)
        db_path = tmp_path / "cache.db"
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
        conn.commit()
        conn.close()

        books = [
            # Ничья 1:1 — ждёт ручного выбора.
            ("src/Кузнец 1.fb2", "Кузнец", "Первая", 1, "Детектив"),
            ("src/Кузнец 2.fb2", "Кузнец", "Вторая", 2, "Фантастика"),
            # Большинство 2:1 — вся серия в «Фантастика».
            ("src/Страж 1.fb2", "Страж", "Альфа", 1, "Фантастика"),
            ("src/Страж 2.fb2", "Страж", "Бета", 2, "Детектив"),
            ("src/Страж 3.fb2", "Страж", "Гамма", 3, "Фантастика"),
        ]
        records = []
        for path, series, title, n, genre in books:
            (staging / path).write_text(
                _FB2.format(genre=genre, title=title, series=series, n=n), encoding="utf-8"
            )
            records.append(_rec(path, series, title, n, genre))

        service = _service(priority=["Фантастика", "Детектив"])
        service.library_path = library
        service.last_scan_path = staging
        service.db_path = db_path
        service.stats = {
            "files_moved": 0, "duplicates_found": 0, "duplicates_deleted": 0,
            "compilation_deletions": 0, "folders_deleted": 0, "errors": 0,
            "total_files": 0, "start_time": None, "end_time": None,
            "touched_author_dirs": set(), "reconciliation_notes": [],
            "genre_unified_notes": [], "genre_conflict_notes": [],
        }
        monkeypatch.setattr(service, "sync_database_with_library", lambda **kw: {"deleted": 0})
        monkeypatch.setattr(service, "_generate_csv_data", lambda *a, **kw: records)
        monkeypatch.setattr(service, "_deduplicate_by_compilation", lambda recs, cb=None: (recs, []))
        monkeypatch.setattr(service, "_update_database", lambda *a, **kw: None)

        stats = service.synchronize(log_callback=lambda msg: None)

        assert (staging / "src/Кузнец 1.fb2").exists()
        assert (staging / "src/Кузнец 2.fb2").exists()
        assert [n["series"] for n in stats["genre_conflict_notes"]] == ["Кузнец"]
        assert not (library / "Детектив").exists()
        moved = sorted(p.name for p in (library / "Фантастика").rglob("*.fb2"))
        assert len(moved) == 3
