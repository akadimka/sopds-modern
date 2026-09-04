"""Регрессия для `FB2CompilerService.compute_group_suffix()` (fb2_compiler.py)
— обнаружено пользователем на реальной группе "Иванович Юрий / Миры
Доставки" (4 дуги на диске: 1, 2, 3, 5 — дуга 4 отсутствует). Арки 1-3
слились в один compile-файл (n_top_arcs=3, "сырые" не предкомпилированные
дуги), арка 5 компилируется отдельно (n_top_arcs=1).

Одиночная дуга 5 честно получала суффикс "ч. 5 в 4 книгах" — виден номер
дуги. А слитые дуги 1-3 давали голое "в 9 книгах" — не видно, что это
именно дуги 1-3, а не вся серия целиком (при том что group.series_complete
уже корректно вычислен как False — есть разрыв в нумерации дуг). Раз
серия неполная, диапазон дуг несёт полезную информацию и должен
попадать в суффикс: "ч. 1-3 в 9 книгах" — как у одиночной дуги.
"""
from pathlib import Path
from types import SimpleNamespace

from fb2parser_core.fb2_compiler import CompilationBook, CompilationGroup, FB2CompilerService


def _book(path, sort_key, volume_label, sort_source="subseries_number"):
    return CompilationBook(
        record=SimpleNamespace(file_title=path, proposed_series="", series_number=""),
        abs_path=Path(path),
        sort_key=sort_key,
        sort_source=sort_source,
        order_ambiguous=False,
        volume_label=volume_label,
    )


class TestMergedArcsWithGapShowArcRangeInSuffix:
    AUTHOR = "Иванович Юрий"
    SERIES = "Миры Доставки"

    def _arcs_1_3_group(self):
        books = [
            _book("1. Дорога к звёздному престолу.fb2", (0, 1, 1, 0), "1.1"),
            _book("2. Нирвана.fb2", (0, 1, 2, 0), "1.2"),
            _book("3. Битва за Оилтон.fb2", (0, 1, 3, 0), "1.3"),
            _book("1. На древней земле.fb2", (0, 2, 1, 0), "2.1"),
            _book("2. Дорога между звезд.fb2", (0, 2, 2, 0), "2.2"),
            _book("3. На родном Оилтоне.fb2", (0, 2, 3, 0), "2.3"),
            _book("4. Торжество справедливости.fb2", (0, 2, 4, 0), "2.4"),
            _book("1. Неуемный консорт.fb2", (0, 3, 1, 0), "3.1"),
            _book("2. Непобедимые.fb2", (0, 3, 2, 0), "3.2"),
        ]
        return CompilationGroup(
            author=self.AUTHOR, series=self.SERIES, books=books,
            order_determined=True, volume_range="1-3", series_complete=False,
        )

    def _arc_5_group(self):
        books = [
            _book("1. Оскал фортуны.fb2", (0, 5, 1, 0), "5.1"),
            _book("2. Капризная Фортуна.fb2", (0, 5, 2, 0), "5.2"),
            _book("3. Жестокая фортуна.fb2", (0, 5, 3, 0), "5.3"),
            _book("4. Благосклонная фортуна.fb2", (0, 5, 4, 0), "5.4"),
        ]
        return CompilationGroup(
            author=self.AUTHOR, series=self.SERIES, books=books,
            order_determined=True, volume_range="5-5", series_complete=False,
        )

    def test_merged_incomplete_arcs_show_range(self):
        svc = FB2CompilerService()
        suffix, lo, hi = svc.compute_group_suffix(self._arcs_1_3_group())
        assert suffix == "ч. 1-3 в 9 книгах"
        assert (lo, hi) == (1, 3)

    def test_single_incomplete_arc_still_shows_its_own_number(self):
        svc = FB2CompilerService()
        suffix, lo, hi = svc.compute_group_suffix(self._arc_5_group())
        assert suffix == "ч. 5 в 4 книгах"
        assert (lo, hi) == (5, 5)

    def test_merged_complete_arcs_stay_plain(self):
        """Без разрыва в нумерации (серия завершена на этом run'е) —
        диапазон дуг не несёт новой информации, суффикс остаётся простым.
        """
        group = self._arcs_1_3_group()
        group.series_complete = True
        svc = FB2CompilerService()
        suffix, _lo, _hi = svc.compute_group_suffix(group)
        assert suffix == "в 9 книгах"
