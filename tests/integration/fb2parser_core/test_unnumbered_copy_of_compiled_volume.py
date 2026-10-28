"""Книга без номера, которая уже есть внутри компиляции серии
(docs/series-duplicates.md; сервер 2026-10-27: «Тор. Темный» рядом с «Принц
Тор (Тетралогия)», где «Темный» — том 2). Без номера она не «покрыта»
диапазоном компиляции, группа не собиралась, и копия лежала рядом навсегда.
Теперь при синхронизации (auto_compile_library) её текст сверяется с
компиляцией: копия — дубль, а книга без номера, которой в компиляции нет,
остаётся рядом (решение пользователя 2026-10-27).
"""
from pathlib import Path

import pytest

from fb2parser_core.auto_compile_service import auto_compile_library
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_web.fb2parser_bridge import _config_path
from tests.integration.fb2parser_core.test_third_party_omnibus import (
    _omnibus,
    _volume_sections,
)

pytestmark = pytest.mark.django_db
SERIES = "Принц Тор"


def test_unnumbered_copy_is_removed_unique_book_stays(tmp_path):
    folder = tmp_path / "Фантастика" / "Сахаров Василий" / SERIES
    folder.mkdir(parents=True)
    bodies = [(t, f"<body>{_volume_sections(v, True)}</body>")
              for v, t in enumerate(("Тор", "Темный", "Вице-адмирал", "Диверсант"), 1)]
    ours = folder / f"Сахаров Василий - {SERIES} (Тетралогия).fb2"
    ours.write_text(FB2CompilerService()._build_fb2("Сахаров Василий", SERIES, "Тетралогия", "sf", bodies),
                    encoding="utf-8")
    copy = _omnibus(folder / f"Сахаров Василий - {SERIES}. Темный.fb2", "Тор. Темный", "Сахаров Василий", [2])
    unique = _omnibus(folder / f"Сахаров Василий - {SERIES}. Рассказы.fb2", "Тор. Рассказы", "Сахаров Василий", [77])
    auto_compile_library(str(tmp_path), config_path=_config_path(),
                         filter_paths={str(tmp_path / "Фантастика" / "Сахаров Василий")})
    assert ours.exists() and not copy.exists() and unique.exists()
    assert sorted(p.name for p in Path(folder).iterdir()) == sorted([ours.name, unique.name])


def test_book_without_number_does_not_block_cleanup_of_covered_volumes(tmp_path):
    """Раньше одна книга без номера, которой нет в сборнике, срывала всю
    группу: покрытый том с номером оставался лежать рядом с компиляцией."""
    folder = tmp_path / "Фантастика" / "Сахаров Василий" / SERIES
    folder.mkdir(parents=True)
    bodies = [(t, f"<body>{_volume_sections(v, True)}</body>")
              for v, t in enumerate(("Тор", "Темный", "Вице-адмирал", "Диверсант"), 1)]
    ours = folder / f"Сахаров Василий - {SERIES} (Тетралогия).fb2"
    ours.write_text(FB2CompilerService()._build_fb2("Сахаров Василий", SERIES, "Тетралогия", "sf", bodies),
                    encoding="utf-8")
    vol3 = _omnibus(folder / f"Сахаров Василий - {SERIES} 3. Вице-адмирал.fb2", "Вице-адмирал", "Сахаров Василий", [3])
    story = _omnibus(folder / f"Сахаров Василий - {SERIES}. Рассказы.fb2", "Тор. Рассказы", "Сахаров Василий", [77])
    auto_compile_library(str(tmp_path), config_path=_config_path(),
                         filter_paths={str(tmp_path / "Фантастика" / "Сахаров Василий")})
    assert ours.exists() and not vol3.exists() and story.exists()
