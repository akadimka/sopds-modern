"""Регрессия для `FB2CompilerService.compute_group_suffix()` — обнаружено
на реальной группе "Бэккер Ричард / Второй Апокалипсис" (2 подсерии-дуги:
"Второй Апокалипсис 1 \\ Князь пустоты" — 3 сырых книги, "Второй
Апокалипсис 2 \\ Аспект-Император" — 4 сырых книги, обе дуги завершены,
разрыва в нумерации нет).

Слияние нескольких "сырых" (не предкомпилированных) дуг в одну книгу
серии раньше ВСЕГДА давало голое "в N книгах" без словесной формы — даже
когда серия полностью завершена и разрыва в нумерации дуг нет. Само
название подсерий ("Второй Апокалипсис 1"/"Второй Апокалипсис 2")
однозначно называет число дуг — 2, "Дилогия" — поэтому словесная форма
здесь безопасна и информативнее голого счёта книг: docs/quality-roadmap.md,
баг №22.

См. также tests/integration/fb2parser_core/test_compute_group_suffix_arc_gap.py
— там же закреплено, что при РАЗРЫВЕ в нумерации дуг (серия неполная)
словесная форма по-прежнему не используется (замаскировала бы, какие
именно дуги в файле).
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


class TestBakkerSecondApocalypseTwoCompleteArcsGetDuologyWord:
    AUTHOR = "Бэккер Ричард"
    SERIES = "Второй Апокалипсис"

    def _group(self):
        books = [
            _book("Второй Апокалипсис 1. Князь пустоты 1. Тьма прежних времен.fb2", (0, 1, 1, 0), "1.1"),
            _book("Второй Апокалипсис 1. Князь пустоты 2. Воин-Пророк.fb2", (0, 1, 2, 0), "1.2"),
            _book("Второй Апокалипсис 1. Князь пустоты 3. Тысячекратная Мысль.fb2", (0, 1, 3, 0), "1.3"),
            _book("Второй Апокалипсис 2. Аспект-Император 1. Око Судии.fb2", (0, 2, 1, 0), "2.1"),
            _book("Второй Апокалипсис 2. Аспект-Император 2. Воин Доброй Удачи.fb2", (0, 2, 2, 0), "2.2"),
            _book("Второй Апокалипсис 2. Аспект-Император 3. Великая Ордалия.fb2", (0, 2, 3, 0), "2.3"),
            _book("Второй Апокалипсис 2. Аспект-Император 4. Нечестивый Консульт.fb2", (0, 2, 4, 0), "2.4"),
        ]
        return CompilationGroup(
            author=self.AUTHOR, series=self.SERIES, books=books,
            order_determined=True, volume_range="1-2", series_complete=True,
        )

    def test_complete_two_arcs_get_duology_word(self):
        svc = FB2CompilerService()
        suffix, lo, hi = svc.compute_group_suffix(self._group())
        assert suffix == "Дилогия в 7 книгах"
        assert (lo, hi) == (1, 2)
