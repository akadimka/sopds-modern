"""Разовая чистка библиотеки — `manage.py library_cleanup` (шаг 3
docs/series-duplicates.md): серии в нескольких жанрах переносятся в один
(большинство или выбор суперпользователя), решения компилятора по всей
библиотеке. Без --apply на диске ничего не меняется."""
import sqlite3

import pytest
from django.core.management import call_command

from fb2parser_core import library_cleanup
from fb2parser_core.synchronization import SynchronizationService
from fb2parser_web import fb2parser_bridge as bridge
from tests.integration.fb2parser_core.test_third_party_omnibus import _omnibus

pytestmark = pytest.mark.django_db


def _put(lib, rel):
    path = lib / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    return path


@pytest.fixture
def lib(tmp_path, monkeypatch):
    root = tmp_path / "lib"
    root.mkdir()
    db = tmp_path / "cache.db"
    init = SynchronizationService.__init__

    def _init(self, config_path):
        init(self, config_path)
        self.library_path, self.db_path = root, db

    monkeypatch.setattr(SynchronizationService, "__init__", _init)
    monkeypatch.setattr(bridge, "_sync_lock_path", lambda: str(tmp_path / "sync.lock"))
    return root


def _run(*args):
    out = []
    call_command("library_cleanup", *args, stdout=type("W", (), {"write": lambda s, m: out.append(m),
                                                                  "flush": lambda s: None})())
    return "\n".join(out)


def test_split_series_found_with_other_author_word_order(lib):
    _put(lib, "Детектив/Престон Дуглас/Пендергаст/1.fb2.zip")
    _put(lib, "Детектив/Престон Дуглас/Пендергаст/2.fb2.zip")
    _put(lib, "Фантастика/Дуглас Престон/Пендергаст/3.fb2.zip")
    _put(lib, "Фантастика/Дуглас Престон/Другая/1.fb2.zip")
    [s] = library_cleanup.find_split_series(lib)
    assert (s.key, s.books, s.majority()) == ("Престон Дуглас / Пендергаст", {"Детектив": 2, "Фантастика": 1},
                                              "Детектив")


def test_report_changes_nothing_apply_moves_minority_into_majority(lib):
    _put(lib, "Детектив/Дашко Дмитрий/Мент/1.fb2.zip")
    _put(lib, "Детектив/Дашко Дмитрий/Мент/2.fb2.zip")
    moved = _put(lib, "Фантастика/Дашко Дмитрий/Мент/3.fb2.zip")
    db = lib.parent / "cache.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE books (file_path TEXT, genre TEXT)")
    conn.execute("INSERT INTO books VALUES ('Фантастика/Дашко Дмитрий/Мент/3.fb2.zip', 'Фантастика')")
    conn.commit()
    conn.close()

    report = _run("--part", "genres")
    assert "Дашко Дмитрий / Мент: Детектив (2), Фантастика (1) → Детектив (большинство)" in report
    assert moved.exists()

    _run("--part", "genres", "--apply")
    assert not (lib / "Фантастика").exists() or not any((lib / "Фантастика").iterdir())
    assert (lib / "Детектив/Дашко Дмитрий/Мент/3.fb2.zip").exists()
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT file_path, genre FROM books").fetchall() == [
        ("Детектив/Дашко Дмитрий/Мент/3.fb2.zip", "Детектив")]
    conn.close()


def test_tie_moves_only_by_choice(lib, tmp_path):
    _put(lib, "Классическая проза/Дюма Александр/Граф Монте-Кристо/1.fb2")
    _put(lib, "Фантастика/Дюма Александр/Граф Монте-Кристо/2.fb2")
    report = _run("--part", "genres", "--apply")
    assert "поровну, нужен выбор жанра" in report
    assert (lib / "Фантастика/Дюма Александр/Граф Монте-Кристо/2.fb2").exists()

    choices = tmp_path / "choices.tsv"
    choices.write_text("Классическая проза\tДюма Александр / Граф Монте-Кристо\n", encoding="utf-8")
    _run("--part", "genres", "--genre-choices", str(choices), "--apply")
    assert sorted(p.name for p in (lib / "Классическая проза/Дюма Александр/Граф Монте-Кристо").iterdir()) == [
        "1.fb2", "2.fb2"]


def test_occupied_place_is_not_overwritten(lib):
    _put(lib, "Детектив/Дашко Дмитрий/Мент/1.fb2.zip")
    _put(lib, "Детектив/Дашко Дмитрий/Мент/2.fb2.zip")
    other = _put(lib, "Фантастика/Дашко Дмитрий/Мент/1.fb2.zip")
    report = _run("--part", "genres", "--apply")
    assert "занято, не трогаю" in report and other.exists()


def test_compile_report_matches_apply(lib):
    folder = lib / "Фантастика" / "Белов Иннокентий" / "Слесарь"
    kept = _omnibus(folder / "Белов Иннокентий - Слесарь (в 3 книгах).fb2", "Слесарь (в 3 книгах)",
                    "Белов Иннокентий", [1, 2, 3])
    copy = _omnibus(folder / "Белов Иннокентий - Слесарь (Дилогия).fb2", "Слесарь (Дилогия)",
                    "Белов Иннокентий", [1, 2])
    report = _run("--part", "compile")
    assert f"ОСТАВИТЬ {kept.relative_to(lib).as_posix()}" in report
    assert f"УДАЛИТЬ {copy.relative_to(lib).as_posix()}" in report
    renamed = "Белов Иннокентий - Слесарь (Трилогия).fb2"
    assert f"ПЕРЕИМЕНОВАТЬ {kept.relative_to(lib).as_posix()} → {renamed}" in report
    assert copy.exists() and kept.exists()
    _run("--part", "compile", "--apply")
    assert [p.name for p in folder.iterdir()] == [renamed]
