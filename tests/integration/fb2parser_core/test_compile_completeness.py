"""Регрессионный тест для fb2_compiler.compile_group: выбор "лучшей"
предкомпиляции не должен ориентироваться только на ширину номинального
диапазона томов — обнаружено на реальной библиотеке (Земляной Андрей,
Орлов Борис / "Странник"): урезанное издание "Пенталогия" (2010,
номинально тома 1-5) выбиралось поверх двух более полных переиздований
"1-3" (2021) + "4-5" (2022), которые вместе почти вдвое больше по
размеру — реальный текст части книг терялся бы при компиляции.

tests/data/compile_completeness/ — синтетическая fixture, построенная
scripts/build_regen_fixtures.py-подобным способом (см. код там же в
истории репозитория): те же имена/структура паттерна, что и в реальном
случае, но с фиктивным текстом.
"""
from pathlib import Path

import pytest

from fb2parser_core import regen_csv
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_web.fb2parser_bridge import _config_path

LIBRARY_ROOT = Path(__file__).resolve().parents[2] / "data" / "compile_completeness"


@pytest.fixture(scope="module")
def group():
    service = regen_csv.RegenCSVService(_config_path())
    records = service.generate_csv(str(LIBRARY_ROOT), output_csv_path=None)
    svc = FB2CompilerService()
    groups = svc.find_groups(records, LIBRARY_ROOT)
    matches = [g for g in groups if g.author == "Автор Тест"]
    assert len(matches) == 1
    return matches[0]


def test_smaller_wide_range_precompile_is_not_kept(group):
    kept_names = {p.name for p in (group.kept_paths or [])}
    assert "Автор Тест - Серия Тест (Серия Тест. Пенталогия).fb2" not in kept_names


def test_fuller_narrower_precompiles_are_used_as_sources(group):
    book_names = {b.abs_path.name for b in group.books}
    assert "Автор Тест - Серия Тест (Серия Тест 1-3).fb2" in book_names
    assert "Автор Тест - Серия Тест (Серия Тест 4-5).fb2" in book_names


def test_smaller_wide_range_precompile_marked_duplicate(group):
    dup_names = {p.name for p in (group.duplicate_paths or [])}
    assert "Автор Тест - Серия Тест (Серия Тест. Пенталогия).fb2" in dup_names
