"""Регрессия для `_compute_folder_series()` в regen_csv.py.

Реальный случай (пользователь, библиотека "Пехов Алексей"): Normalize/
Compiler, запущенные ПРЯМО на папке автора ("...\\Пехов Алексей -
Сборник"), теряли proposed_series для КАЖДОГО файла в однократно
вложенной подпапке ("Мантикора", "Синее пламя", "Вселенная Изнанки" и
т.д.) — Compiler находил только 1 из 4+ реально существующих групп,
требующих объединения.

Причина: PRECACHE отдельно распознаёт case "work_dir сам — папка автора"
("[CACHE] Work_dir is AUTHOR: ..."), но `_compute_folder_series()` об
этом не знал — искал имя автора КАК СЕГМЕНТ ВНУТРИ relative parent_parts
пути файла. Когда work_dir уже "съел" сегмент с именем автора (сканирование
запущено прямо в его папке, а не на уровень выше, откуда видна папка
автора целиком), найти его там снова невозможно в принципе — и
`author_folder_index` навсегда оставался -1, из-за чего однократно
вложенные подпапки без ПОДТВЕРЖДАЮЩЕЙ metadata (folder_meta_consensus —
отдельный, не связанный механизм) не получали серию вообще никак.

Сканирование той же структуры на уровень выше (родительская папка
библиотеки, автор — часть relative-пути) работало верно всегда — баг
проявлялся только при сканировании, ограниченном самой папкой автора.
"""
from pathlib import Path

import pytest

from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook>
<description>
<title-info>
<genre>фантастика</genre>
<author><first-name>Алексей</first-name><last-name>Пехов</last-name></author>
<book-title>{title}</book-title>
</title-info>
</description>
<body><section><p>Текст</p></section></body>
</FictionBook>
"""


def _write_fb2(path: Path, title: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FB2_TEMPLATE.format(title=title), encoding="utf-8")


def _build_author_folder(root: Path) -> Path:
    """root/Пехов Алексей - Сборник/Мантикора/{2 файла без своей позиции}."""
    author_dir = root / "Пехов Алексей - Сборник"
    _write_fb2(author_dir / "Мантикора" / "Под знаком мантикоры.fb2", "Под знаком мантикоры")
    _write_fb2(author_dir / "Мантикора" / "Наранья.fb2", "Наранья")
    return author_dir


class TestFolderSeriesDetectedWhenScanRootIsTheAuthorFolder:
    def test_series_detected_scanning_directly_at_author_folder(self, tmp_path):
        author_dir = _build_author_folder(tmp_path)

        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(author_dir), output_csv_path=None)

        by_name = {Path(r.file_path).name: r for r in records}
        assert len(by_name) == 2
        for r in by_name.values():
            assert r.proposed_series == "Мантикора", r.file_path
            assert r.series_source == "folder_dataset"

    def test_same_result_scanning_from_parent_library_folder(self, tmp_path):
        """Контрольная проверка: та же структура, но со сканированием на
        уровень выше (автор виден как сегмент relative-пути) — уже и до
        фикса давала верный результат; нужна, чтобы не потерять этот режим.
        """
        _build_author_folder(tmp_path)

        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)

        by_name = {Path(r.file_path).name: r for r in records}
        assert len(by_name) == 2
        for r in by_name.values():
            assert r.proposed_series == "Мантикора", r.file_path
            assert r.series_source == "folder_dataset"
