"""`manage.py library_cleanup --part books` — книги по файлу решений.

Сервер 2026-11-02: 24 книги в ошибочных жанровых папках («Без жанра»,
`poetry`, `prose_military`) — .fb2.zip, у которых не читались жанр и
название, и книги с encoding="latin-1" при тексте в UTF-8, где старая
синхронизация затёрла автора, название и жанр знаками «????». Файл решений
говорит, куда перенести книгу и что записать в теги. Без --apply на диске
ничего не меняется.
"""
import sqlite3
import zipfile

import pytest

from tests.integration.fb2parser_core.test_library_cleanup import _run, lib  # noqa: F401 — фикстура

pytestmark = pytest.mark.django_db

_TEXT = "Длинный текст книги, которого достаточно для сравнения версий. "
_FB2 = """<?xml version="1.0" encoding="{enc}"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>{genre}</genre>
<author><last-name>{author}</last-name></author>
<book-title>{title}</book-title>{seq}</title-info></description>
<body><section><p>{text}</p></section></body>
</FictionBook>
"""


def _book(path, enc="windows-1251", data_enc=None, genre="Классическая проза", author="Шукшин", title="x",
          seq="", text=_TEXT * 60):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _FB2.format(enc=enc, genre=genre, author=author, title=title, seq=seq, text=text)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(path.name[:-4], data.encode(data_enc or enc, errors="replace"))
    return path


def _text(path):
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    enc = "utf-8" if b'encoding="utf-8"' in raw[:100] else "cp1251"
    return raw.decode(enc)


def _fixes(tmp_path, *rows):
    path = tmp_path / "book_fixes.tsv"
    path.write_text("# откуда\tкуда\tавтор\tназвание\tсерия\tжанр\n" + "".join("\t".join(r) + "\n" for r in rows),
                    encoding="utf-8")
    return str(path)


def test_report_changes_nothing_apply_moves_and_writes_title(lib, tmp_path):
    src = _book(lib / "Без жанра/Шукшин Василий/Шукшин Василий - [no title].fb2.zip",
                title="shukshin_vasiliy_bessovestnyie.fb2", seq='<sequence name="Рассказы"/>')
    dst = "Классическая проза/Шукшин Василий/Шукшин Василий - Бессовестные.fb2.zip"
    (lib / "Без жанра/Калинин Даниил").mkdir()                    # пустая и до чистки
    db = lib.parent / "cache.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE books (file_path TEXT, genre TEXT)")
    conn.execute("INSERT INTO books VALUES (?, 'Без жанра')", (src.relative_to(lib).as_posix(),))
    conn.commit()
    conn.close()
    fixes = _fixes(tmp_path, (src.relative_to(lib).as_posix(), dst, "", "Бессовестные", "-", ""))

    report = _run("--part", "books", "--fixes", fixes)
    assert "→ Без жанра/Шукшин Василий" in report and "название: Бессовестные; серия: убрать" in report
    assert "перенесено: 1" in report and src.exists()

    _run("--part", "books", "--fixes", fixes, "--apply")
    assert not (lib / "Без жанра").exists()                       # опустевшие папки убраны
    text = _text(lib / dst)
    assert "<book-title>Бессовестные</book-title>" in text and "<sequence" not in text
    assert 'encoding="windows-1251"' in text                      # своя кодировка у книги осталась
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT file_path, genre FROM books").fetchall() == [(dst, "Классическая проза")]
    conn.close()


def test_damaged_mislabeled_book_gets_tags_in_utf8(lib, tmp_path):
    """encoding="latin-1", текст в UTF-8, теги — «????», в тексте битый байт."""
    path = lib / "Фантастика/Рымин Андрей/К Вершине/Рымин Андрей - К Вершине (в 13 книгах).fb2.zip"
    text = "Текст книги в UTF-8. " * 300
    _book(path, enc="latin-1", data_enc="utf-8", genre="??????????", author="?????", title="????",
          seq='<sequence name="? ???????"/>', text=text)
    with zipfile.ZipFile(path) as z:
        raw = z.read(z.namelist()[0])
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(path.name[:-4], raw.replace("книги".encode("utf-8"), b"\xd0\xba\xbd", 1))
    fixes = _fixes(tmp_path, (path.relative_to(lib).as_posix(), "", "Рымин Андрей", "К Вершине (в 13 книгах)",
                              "К Вершине", "Фантастика"))

    report = _run("--part", "books", "--fixes", fixes, "--apply")
    assert "теги на месте: 1" in report and "ошибок: 0" in report
    result = _text(path)
    assert result.startswith('<?xml version="1.0" encoding="utf-8"?>')
    assert "<last-name>Рымин</last-name>" in result and '<sequence name="К Вершине"/>' in result
    assert "<genre>Фантастика</genre>" in result and "?" * 4 not in result
    assert result.count("Текст книги в UTF-8.") == 299


def test_copy_of_book_already_in_place_is_dropped(lib, tmp_path):
    """«Константин Каин - Замысел»: испорченная копия в «Без жанра», та же
    книга уже лежит в «Фантастике» — остаётся одна."""
    kept = _book(lib / "Фантастика/Константин Каин/Замысел/Константин Каин - Замысел.fb2.zip", enc="utf-8",
                 genre="Фантастика", author="Каин", title="Замысел", text=_TEXT * 60 + "Конец.")
    copy = _book(lib / "Без жанра/Каин/Замысел/Каин - Замысел.fb2.zip", enc="latin-1", data_enc="utf-8",
                 genre="??????????", author="????", title="???????", text=_TEXT * 60)
    before = kept.read_bytes()
    fixes = _fixes(tmp_path, (copy.relative_to(lib).as_posix(), kept.relative_to(lib).as_posix(),
                              "Каин Константин", "Замысел", "Замысел", "Фантастика"))
    report = _run("--part", "books", "--fixes", fixes, "--apply")
    assert "на месте версия не хуже" in report and "удаление лишней копии: 1" in report
    assert not copy.exists() and kept.read_bytes() == before


def test_waiting_and_missing_rows_change_nothing(lib, tmp_path):
    poem = _book(lib / "poetry/Трегубов Олег/Трегубов Олег - Душа нараспашку.fb2.zip", enc="utf-8", genre="poetry")
    fixes = _fixes(tmp_path, (poem.relative_to(lib).as_posix(), "?", "", "", "", "?"),
                   ("Без жанра/Нет Такого/Нет Такого - Книга.fb2.zip", "Фантастика/x.fb2.zip", "", "", "", ""))
    report = _run("--part", "books", "--fixes", fixes, "--apply")
    assert "ждут выбора: 1" in report and "нет файла: 1" in report
    assert poem.exists()


def test_too_long_target_path_is_reported_before_any_change(lib, tmp_path, monkeypatch):
    """Копия библиотеки 2026-11-02: перенос «Риордан Рик - Перси Джексон…» в
    глубокой папке упал на MAX_PATH уже после записи тегов."""
    from fb2parser_core import library_cleanup

    src = _book(lib / "Без жанра/Риордан Рик/Риордан Рик - [no title].fb2.zip", enc="utf-8", genre="Фантастика")
    before = src.read_bytes()
    monkeypatch.setattr(library_cleanup, "_path_too_long", lambda p: True)
    fixes = _fixes(tmp_path, (src.relative_to(lib).as_posix(), "Фантастика/Риордан Рик/Длинное.fb2.zip", "",
                              "Перси Джексон и похититель молний", "", ""))
    report = _run("--part", "books", "--fixes", fixes, "--apply")
    assert "путь назначения слишком длинный" in report and "ошибок: 1" in report
    assert src.read_bytes() == before
