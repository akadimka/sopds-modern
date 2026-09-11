"""Регрессия для `FB2CompilerService.compute_group_suffix()`/`find_groups()`
— docs/quality-roadmap.md, баг №65.

Реальный случай (Калинин Даниил / "Злая Русь. Князь Фёдор"): именованная
дуга-подсерия занимает позиции 6-8 родительской серии "Злая Русь", но сама
состоит ровно из 3 книг. Компилятор раньше показывал итоговое имя файла
"(т. 6-8)" — глобальную позицию в родительской серии — хотя, раз дуга
компилируется в СВОЮ ОТДЕЛЬНУЮ группу (а не сливается с основной серией),
пользователь ожидает диапазон ВНУТРИ самой дуги ("т. 1-3"/"Трилогия"):
иначе выглядит так, будто у дуги отсутствуют тома 1-5, которых никогда не
было.
"""
from pathlib import Path
from types import SimpleNamespace

from fb2parser_core.fb2_compiler import CompilationBook, CompilationGroup, FB2CompilerService


def _named_arc_book(global_pos: int, local_pos: int, title: str) -> CompilationBook:
    record = SimpleNamespace(
        proposed_series="Злая Русь\\Князь Федор",
        series_source="filename_named_arc",
        file_title=title,
        series_number=str(global_pos),
        metadata_series="",
    )
    return CompilationBook(
        record=record,
        abs_path=Path(f"Калинин Д. Злая Русь {global_pos}. Князь Фёдор {local_pos}. {title}.fb2"),
        sort_key=(0, global_pos, local_pos, 0),
        sort_source="series_number",
        order_ambiguous=False,
        volume_label=str(global_pos),
    )


class TestNamedArcSubseriesGetsLocalRange:
    def test_suffix_uses_local_position_not_parent_series_position(self):
        books = [
            _named_arc_book(6, 1, "Куликовская сеча"),
            _named_arc_book(7, 2, "Русь и Орда"),
            _named_arc_book(8, 3, "Меч Тамерлана"),
        ]
        group = CompilationGroup(
            author="Калинин Даниил", series="Злая Русь\\Князь Федор",
            books=books, order_determined=True, volume_range="1-3",
        )
        svc = FB2CompilerService()
        suffix, lo, hi = svc.compute_group_suffix(group)
        assert suffix == "Трилогия"
        assert (lo, hi) == (1, 3)

    def test_sort_key_global_position_left_untouched(self):
        # Локальная нумерация — только для отображения; sort_key[1] должен
        # остаться глобальной позицией родительской серии (используется для
        # межгруппового учёта позиций/дедупа в find_groups()).
        books = [_named_arc_book(6, 1, "Куликовская сеча")]
        assert books[0].sort_key == (0, 6, 1, 0)


class TestOrdinarySubseriesRangeUnaffected:
    def test_non_named_arc_subseries_keeps_ordinary_stats(self):
        # Sanity: подсерия БЕЗ filename_named_arc (обычная locally-numbered
        # подсерия, sort_key[1] уже локальный номер) — не должна попасть
        # в новую ветку и продолжает работать через обычный _run_stats().
        record = SimpleNamespace(
            proposed_series="Отряд Сигма\\Такер Уэйн",
            series_source="filename",
            file_title="T",
            series_number="",
            metadata_series="",
        )
        books = [
            CompilationBook(
                record=record, abs_path=Path(f"{n}.fb2"),
                sort_key=(0, n, 0, 0), sort_source="filename",
                order_ambiguous=False, volume_label=str(n),
            )
            for n in (1, 2)
        ]
        group = CompilationGroup(
            author="Автор", series="Отряд Сигма\\Такер Уэйн",
            books=books, order_determined=True, volume_range="1-2",
        )
        svc = FB2CompilerService()
        suffix, lo, hi = svc.compute_group_suffix(group)
        assert suffix == "Дилогия"
        assert (lo, hi) == (1, 2)
