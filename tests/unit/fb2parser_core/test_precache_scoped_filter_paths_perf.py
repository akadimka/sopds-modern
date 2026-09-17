"""Регрессия для `Precache.execute(filter_paths=...)` — docs/quality-roadmap.md.

Реальный случай: авто-компиляция серий после синхронизации ограничивает
работу папками, куда РЕАЛЬНО переместился хоть один файл (`touched_dirs`,
см. `fb2parser_web.views._run_compile_pass`) — задумано как оптимизация:
"5-10 файлов одного автора — сотни, а не тысячи файлов на прогон". На
реальной библиотеке (папка "Книжная полка Дозора", 2082 файла раскиданы
по ~2000 разным авторам внутри одной genre-папки "Фантастика") сообщённое
пользователем "авто-компиляция висит больше 2 часов" оказалось НЕ
зависанием, а катастрофически неэффективным сканированием:

Precache.execute() для каждой целевой (затронутой) папки проходит цепочку
родителей вверх до work_dir, чтобы определить контекст (genre/author),
передавая `_no_recurse=True`. Но `scan_folder_hierarchy()` для такого
родителя всё равно делает ПОЛНЫЙ `folder.iterdir()` + `is_file()` по ВСЕМ
соседям ради проверки `has_fb2_files` — а поскольку общая родительская
genre-папка ("Фантастика") ОДНА на все ~2000 затронутых авторов, эта
дорогая проверка выполнялась ПОВТОРНО на каждую цель, превращая
"просканировать N затронутых папок" в число операций, растущее как
N × размер общей родительской папки (в реальности — миллионы лишних
сетевых stat()-вызовов вместо тысяч).

Фикс: `execute()` дедуплицирует вызовы для одного и того же родителя —
`_visited_ancestors` гарантирует, что общий предок сканируется на весь
набор filter_paths только один раз, а не по разу на каждую цель.
"""
from pathlib import Path

import pytest

from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

# Реальные имена (мужской словарь), чтобы _contains_valid_name() распознавал
# папки как авторские — так же, как в реальной библиотеке.
_AUTHOR_NAMES = [
    "Иванов Иван", "Петров Петр", "Сидоров Семен", "Кузнецов Кузьма",
    "Смирнов Сергей", "Попов Павел", "Соколов Степан", "Волков Виктор",
]

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook>
<description>
<title-info>
<genre>детектив</genre>
<author><first-name>Тест</first-name></author>
<book-title>Книга</book-title>
</title-info>
</description>
<body><section><p>Текст</p></section></body>
</FictionBook>
"""


def _build_library(root: Path, author_count: int) -> list[Path]:
    """Одна genre-папка с author_count авторскими подпапками (по одному fb2
    в каждой) — имитирует реальную "Фантастика" с тысячами авторов внутри.
    """
    genre_dir = root / "Genre"
    genre_dir.mkdir()
    author_dirs = []
    for i in range(author_count):
        name = _AUTHOR_NAMES[i % len(_AUTHOR_NAMES)] + f" {i}"
        author_dir = genre_dir / name
        author_dir.mkdir()
        (author_dir / "book.fb2").write_text(_FB2_TEMPLATE, encoding="utf-8")
        author_dirs.append(author_dir)
    return author_dirs


class TestPrecacheDeduplicatesSharedAncestorAcrossFilterPaths:
    def test_shared_genre_ancestor_scanned_once_not_per_target(self, tmp_path, monkeypatch):
        author_dirs = _build_library(tmp_path, author_count=50)
        # Затронуты только 5 из 50 авторов — как touched_dirs после
        # синхронизации небольшой партии файлов в большую библиотеку.
        touched = author_dirs[:5]

        import pathlib
        genre_dir = tmp_path / "Genre"
        iterdir_calls_on_genre_dir = []
        original_iterdir = pathlib.Path.iterdir

        def _counting_iterdir(self):
            if self == genre_dir:
                iterdir_calls_on_genre_dir.append(1)
            return original_iterdir(self)

        monkeypatch.setattr(pathlib.Path, "iterdir", _counting_iterdir)

        service = RegenCSVService(_config_path())
        records = service.generate_csv(
            str(tmp_path), output_csv_path=None,
            filter_paths={str(p) for p in touched},
        )

        # Родительская genre-папка ("Genre") должна сканироваться РОВНО один
        # раз на весь набор filter_paths, а не по разу на каждую из 5 целей.
        assert len(iterdir_calls_on_genre_dir) == 1

        # Корректность не должна была пострадать: обработаны только
        # затронутые 5 авторов, не все 50.
        touched_names = {p.name for p in touched}
        processed_names = {Path(r.file_path).parent.name for r in records}
        assert processed_names == touched_names

    def test_untouched_sibling_authors_not_scanned_at_all(self, tmp_path, monkeypatch):
        author_dirs = _build_library(tmp_path, author_count=20)
        touched = author_dirs[:2]
        untouched = author_dirs[2:]

        import pathlib
        visited_folders = set()
        original_iterdir = pathlib.Path.iterdir

        def _tracking_iterdir(self):
            visited_folders.add(self)
            return original_iterdir(self)

        monkeypatch.setattr(pathlib.Path, "iterdir", _tracking_iterdir)

        service = RegenCSVService(_config_path())
        service.generate_csv(
            str(tmp_path), output_csv_path=None,
            filter_paths={str(p) for p in touched},
        )

        for untouched_dir in untouched:
            assert untouched_dir not in visited_folders, (
                f"Незатронутая папка {untouched_dir} не должна была сканироваться вообще"
            )
