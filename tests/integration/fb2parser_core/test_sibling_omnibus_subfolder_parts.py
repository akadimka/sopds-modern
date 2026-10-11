"""Регрессия для `FB2CompilerService.find_groups()` — docs/quality-
roadmap.md, баг №130.

Реальный случай (папка "Серия - «Гиганты фантастики»" пользователя): рядом
лежат сборники "Перумов. Летописи Разлома 1. Летописи Разлома.fb2" … "… 4.
Война мага. Эндшпиль — Конец игры.fb2" и подпапки с их исходными томами:
"Перумов. Путь некроманта\\1. Хранитель Мечей…" … "4. …", "Перумов. Дочь
Некроманта. Война мага. Дебют - Миттельшпиль\\1. …" … "3. …" и т.д. У всех
серия "Летописи Разлома" из метаданных, номер — из префикса имени файла,
который считается внутри своей подпапки.

Две ошибки:
1. Тома трёх подпапок попадали в одну группу и перемешивались по номерам
   (1, 1, 1, 2, 2, 3, 3, 4).
2. Сборник 3 с несколькими заголовками в названии читался по содержимому
   (разделы "Книга 1/2/3") как тома 1-3 серии и поглощал сборники 1 и 2
   как дубли — потеря файлов при применении.

Фикс: тома подпапки, имя которой (без фамилии автора) совпадает с
названием сборника из родительской папки, считаются покрытыми этим
сборником; явная позиция "Серия N." в имени сборника весомее внутренней
нумерации его содержимого.
"""
from pathlib import Path

import pytest

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

ROOT = "Гиганты фантастики"
SERIES = "Летописи Разлома"

_FB2_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Ник</first-name><last-name>Перумов</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body>{sections}</body></FictionBook>
"""


def _write(path: Path, title: str, sections: list, repeat: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f"<section><title><p>{head}</p></title><p>{text * repeat}</p></section>"
        for head, text in sections
    )
    path.write_text(_FB2_TMPL.format(title=title, sections=body), encoding="utf-8")


def _rec(rel: str, title: str, number: str, number_source: str) -> BookRecord:
    return BookRecord(
        file_path=rel, file_title=title,
        metadata_authors="Ник Перумов", proposed_author="Перумов Ник",
        author_source="metadata_folder_confirmed", metadata_series=SERIES,
        proposed_series=SERIES, series_source="metadata",
        series_number=number, series_number_source=number_source,
    )


# (имя файла, название, имена книг внутри сборника)
_OMNIBUSES = [
    ("Перумов. Летописи Разлома 1. Летописи Разлома.fb2", "Летописи Разлома",
     ["Рождение мага"]),
    ("Перумов. Летописи Разлома 2. Путь некроманта.fb2", "Путь некроманта",
     ["Хранитель Мечей"]),
    ("Перумов. Летописи Разлома 3. Дочь Некроманта. Война мага. Дебют — Миттельшпиль.fb2",
     "Дочь Некроманта. Война мага. Дебют — Миттельшпиль",
     ["Книга 1. Дочь Некроманта", "Книга 2. Дебют", "Книга 3. Миттельшпиль"]),
    ("Перумов. Летописи Разлома 4. Война мага. Эндшпиль — Конец игры.fb2",
     "Война мага. Эндшпиль — Конец игры", ["Эндшпиль"]),
]

# (подпапка, [(имя файла, название)])
_PART_FOLDERS = [
    ("Перумов. Путь некроманта", [
        ("1. Хранитель Мечей. Странствия мага. Том 1.fb2", "Хранитель Мечей. Странствия мага. Том 1"),
        ("2. Хранитель Мечей. Странствия мага. Том 2.fb2", "Хранитель Мечей. Странствия мага. Том 2"),
        ("3. Хранитель Мечей. Одиночество мага. Том 1.fb2", "Хранитель Мечей. Одиночество мага. Том 1"),
        ("4. Хранитель Мечей. Одиночество мага. Том 2.fb2", "Хранитель Мечей. Одиночество мага. Том 2"),
    ]),
    ("Перумов. Дочь Некроманта. Война мага. Дебют - Миттельшпиль", [
        ("1. Дочь Некроманта.fb2", "Дочь Некроманта"),
        ("2. Дебют.fb2", "Война мага. Том 1. Дебют"),
        ("3. Миттельшпиль.fb2", "Война мага. Том 2. Миттельшпиль"),
    ]),
    # "2. Конец игры" по метаданным в другой серии — в группе один том подпапки.
    ("Перумов. Война мага. Эндшпиль - Конец игры", [
        ("1. Эндшпиль.fb2", "Война мага. Том 3: Эндшпиль"),
    ]),
]

_PART_NAMES = {name for _, parts in _PART_FOLDERS for name, _ in parts}
_OMNIBUS_NAMES = [name for name, _, _ in _OMNIBUSES]


def _build(tmp_path: Path, omnibus_repeat: int = 400) -> list:
    recs = []
    for n, (name, title, books) in enumerate(_OMNIBUSES, start=1):
        _write(tmp_path / ROOT / name, title,
               [(b, f"Текст сборника {n}, {b}. ") for b in books], omnibus_repeat)
        recs.append(_rec(f"{ROOT}\\{name}", title, str(n), "metadata"))
    for folder, parts in _PART_FOLDERS:
        for i, (name, title) in enumerate(parts, start=1):
            _write(tmp_path / ROOT / folder / name, title, [(title, f"Текст тома {title}. ")], 40)
            recs.append(_rec(f"{ROOT}\\{folder}\\{name}", title, str(i), "filename_prefix"))
    return recs


def _group(tmp_path: Path, recs: list):
    groups = [g for g in FB2CompilerService().find_groups(recs, tmp_path)
              if g.author == "Перумов Ник"]
    assert len(groups) == 1, [(g.series, g.volume_range) for g in groups]
    return groups[0]


@pytest.fixture
def group(tmp_path):
    return _group(tmp_path, _build(tmp_path))


class TestSubfolderPartsOfSiblingOmnibus:
    def test_no_omnibus_is_deleted(self, group):
        deleted = {p.name for p in (group.duplicate_paths or [])}
        assert not deleted & set(_OMNIBUS_NAMES), deleted

    def test_omnibuses_compile_in_series_order(self, group):
        names = [b.abs_path.name for b in group.books]
        assert names == _OMNIBUS_NAMES
        assert [b.volume_label for b in group.books] == ["1", "2", "3", "4"]

    def test_subfolder_parts_go_to_duplicates(self, group):
        deleted = {p.name for p in (group.duplicate_paths or [])}
        assert deleted == _PART_NAMES

    def test_abridged_omnibus_does_not_swallow_parts(self, tmp_path):
        """Сборник заметно меньше суммы томов подпапки — вероятно, урезанное
        издание: тома не убираем как покрытые им."""
        group = _group(tmp_path, _build(tmp_path, omnibus_repeat=1))
        kept = {b.abs_path.name for b in group.books}
        assert "1. Хранитель Мечей. Странствия мага. Том 1.fb2" in kept


class TestExplicitSeriesPositionBeatsInnerNumbering:
    def test_multi_title_omnibus_with_series_position_is_not_a_range(self, tmp_path):
        recs = _build(tmp_path)
        rec = next(r for r in recs if r.file_path.endswith(_OMNIBUS_NAMES[2]))
        compiler = FB2CompilerService()
        book = compiler._make_book(rec, tmp_path)
        assert compiler._precompiled_range(book, SERIES) == (0, 0)
