"""Регрессия для FB2CompilerService: внутренний номер тома ("Том N" в
заголовке), отдельный от внешнего номера части серии, должен честно
отражать неполноту на границах диапазона компиляции — но НЕ портить
словесную форму суффикса для настоящих, полностью завершённых
многотомных под-книг.

Реальный случай (docs/quality-roadmap.md, баг №62): "Кай Ханси / Вечная
Война" — "10. Катастрофа том 2.fb2" (нет пары "том 1" нигде в
библиотеке) и "12. Сфера Богов том 1.fb2" (нет пары "том 2") раньше
компилировались как обычные целые тома "т. 10-12", хотя каждый из них —
лишь половина внутренней под-книги, разбитой на несколько файлов. Тем же
временем "5. Бойня. том 1.fb2" + "6. Бойня. том 2.fb2" в ЭТОЙ ЖЕ серии —
настоящая, полностью завершённая дилогия (оба тома лежат на СОСЕДНИХ
внешних позициях), и должна остаться под обычным "т. 5-8" без дробей.

tests/data/cartesian_inner_tom/ — облегчённая fixture (текст/бинарники
вырезаны), построенная тем же способом, что и tests/data/regen_library
(см. scripts/build_regen_fixtures.py), из реальной папки
"Кай Ханси\\Вечная Война".
"""
from pathlib import Path

import pytest

from fb2parser_core import regen_csv
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_web.fb2parser_bridge import _config_path

LIBRARY_ROOT = Path(__file__).resolve().parents[2] / "data" / "cartesian_inner_tom"


@pytest.fixture(scope="module")
def groups():
    service = regen_csv.RegenCSVService(_config_path())
    records = service.generate_csv(str(LIBRARY_ROOT), output_csv_path=None)
    svc = FB2CompilerService()
    return svc.find_groups(records, LIBRARY_ROOT)


def _group_with_book(groups, filename_substring):
    matches = [
        g for g in groups
        if any(filename_substring in b.abs_path.name for b in g.books)
    ]
    assert len(matches) == 1, f"expected exactly 1 group containing {filename_substring!r}, got {len(matches)}"
    return matches[0]


class TestIncompleteBoundaryTomsGetFractionalRange:
    """"Катастрофа. Том 2" (нет "Том 1") и "Сфера Богов. Том 1" (нет "Том
    2") — оба на границах диапазона компиляции, оба честно помечены
    дробно.
    """

    def test_range_is_fractional_at_both_boundaries(self, groups):
        g = _group_with_book(groups, "Катастрофа")
        assert g.volume_range == "10.2-12.1"

    def test_suffix_reflects_fractional_range(self, groups):
        svc = FB2CompilerService()
        g = _group_with_book(groups, "Катастрофа")
        suffix, lo, hi = svc.compute_group_suffix(g)
        assert suffix == "т. 10.2-12.1"

    def test_middle_ordinary_book_keeps_plain_label(self, groups):
        g = _group_with_book(groups, "Барьер")
        book = next(b for b in g.books if "Барьер" in b.abs_path.name)
        assert book.volume_label == "11"


class TestCompleteAdjacentDuologyStaysPlain:
    """"Бойня" том 1 (том 5) + том 2 (том 6) — настоящая, завершённая
    дилогия на соседних позициях. Дробная метка не должна появляться,
    несмотря на то что оба файла тоже содержат "Том N" в заголовке.
    """

    def test_range_has_no_decimals(self, groups):
        g = _group_with_book(groups, "Бойня")
        assert g.volume_range == "5-8"

    def test_suffix_has_no_decimals(self, groups):
        svc = FB2CompilerService()
        g = _group_with_book(groups, "Бойня")
        suffix, lo, hi = svc.compute_group_suffix(g)
        assert suffix == "т. 5-8"

    def test_both_toms_get_plain_volume_labels(self, groups):
        g = _group_with_book(groups, "Бойня")
        tom1 = next(b for b in g.books if "том 1" in b.abs_path.name)
        tom2 = next(b for b in g.books if "том 2" in b.abs_path.name)
        assert tom1.volume_label == "5"
        assert tom2.volume_label == "6"
