"""Регрессия для `FB2CompilerService.find_groups()`/`compute_group_suffix()`
— docs/quality-roadmap.md, баги №46 и №47.

Реальный случай (замечен пользователем в превью компилятора): Житинский
Александр / "Младший научный сотрудник Петр Верлухин" — 6 самостоятельных
рассказов БЕЗ series_number вообще (пустая строка, не даже placeholder
"0") — `_determine_sort_key()` не находит НИКАКОЙ позиции и возвращает
полностью ambiguous `(9, 0, 0, 0)` для всех шести (`order_ambiguous=True`
у каждой). Превью показывало суффикс "Гексалогия" — как будто это
последовательная 6-томная серия, хотя порядок рассказов полностью
неизвестен (Source="unknown" у всех) — это баг №46, суффикс исправлен
на честное "в N книгах".

При обсуждении с пользователем решено пойти дальше (баг №47): если
ПОЛНОСТЬЮ ни одна книга в группе не имеет реальной позиции, компилировать
такую группу вообще не стоит — единственный детерминированный порядок
(алфавитный по названию) не отражает никакого реального порядка чтения.
Если это на самом деле пронумерованная серия с нераспознанным номером
(а не сборник самостоятельных рассказов) — молчаливая компиляция в
произвольном порядке хуже, чем оставить файлы нетронутыми. Итоговое
поведение: `find_groups()` вообще НЕ формирует компиляцию для такого
бакета (файлы остаются как есть).

Отличие от Бага №45 (та же серия суффиксов, другой механизм, до сих пор
КОМПИЛИРУЕТСЯ по решению пользователя): там у книг БЫЛ sort_key[0]==0
(уровень "series_number"), просто со значением 0 у всех, и
`order_ambiguous=False` — кто-то ЯВНО пытался пронумеровать, просто
неудачно. Здесь sort_key[0]==9 (ambiguous), `order_ambiguous=True` —
номера нет вовсе, ни малейшего намёка на порядок.
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import CompilationBook, CompilationGroup, FB2CompilerService


def _rec(title):
    return BookRecord(
        file_path=f"Житинский Александр - {title}.fb2", file_title=title,
        metadata_authors="Александр Житинский", proposed_author="Житинский Александр",
        author_source="folder_dataset",
        metadata_series="Младший научный сотрудник Петр Верлухин",
        proposed_series="Младший научный сотрудник Петр Верлухин",
        series_source="metadata", series_number="",
    )


class TestFullyAmbiguousOrderGroupNotCompiled:
    def test_six_unordered_stories_produce_no_compilation_group(self):
        titles = [
            "Глагол «инженер»", "Подданный Бризании", "Сено-солома",
            "Страсти по Прометею", "Типичный представитель", "Эффект Брумма",
        ]
        records = [_rec(t) for t in titles]
        svc = FB2CompilerService()
        groups = svc.find_groups(records, Path("."))

        assert groups == []


class TestSeriesNumberZeroStillCompiles:
    def test_bag45_placeholder_zero_still_forms_a_group(self):
        # Sanity: баг №45 (series_number="0" — попытка нумерации была,
        # просто неудачная) по-прежнему компилируется — только баг №47
        # (ПОЛНОСТЬЮ отсутствующий номер, order_ambiguous=True у всех)
        # исключается из компиляции.
        titles = ["Головастик", "Обряд", "Поклонение"]
        records = [
            BookRecord(
                file_path=f"Белаш Александр - {t}.fb2", file_title=t,
                metadata_authors="Александр Белаш", proposed_author="Белаш Александр",
                author_source="folder_dataset", metadata_series="Рассказы", proposed_series="Рассказы",
                series_source="metadata", series_number="0", series_number_source="metadata",
            )
            for t in titles
        ]
        svc = FB2CompilerService()
        groups = svc.find_groups(records, Path("."))
        assert len(groups) == 1
        assert len(groups[0].books) == 3


class TestRealSubseriesUnderSingleParentSlotStillGetsSeriesWord:
    def test_three_subbooks_under_slot_one_still_named_trilogy(self):
        # Sanity: настоящая подсерия, целиком лежащая под родительским
        # слотом 1 (has_subseries=True, top_lo=top_hi=1, n_volumes=3) —
        # фикс НЕ должен трогать этот легитимный случай, sort_key[2]
        # разный (1,2,3) доказывает реальный порядок внутри подсерии.
        records = []
        for n in (1, 2, 3):
            records.append(BookRecord(
                file_path=f"Автор Тест - Серия 1. Подсерия {n}.fb2", file_title=f"Подсерия {n}",
                metadata_authors="Автор Тест", proposed_author="Автор Тест",
                author_source="filename", metadata_series="", proposed_series="Серия 1\\Подсерия",
                series_source="filename_named_arc", series_number="1",
            ))
        svc = FB2CompilerService()
        groups = svc.find_groups(records, Path("."))
        assert len(groups) == 1
        main = groups[0]

        suffix, lo, hi = svc.compute_group_suffix(main)
        assert suffix == "Трилогия"


class TestComputeGroupSuffixFallbackForAmbiguousBooksUnitLevel:
    """Баг №46: если `compute_group_suffix()` всё же вызван на группе с
    полностью неопределённым порядком (например, из другого места кода,
    не через `find_groups()`) — суффикс всё равно должен быть честным
    "в N книгах", а не "N-логия"."""

    def test_ambiguous_books_get_plain_count_not_series_word(self, tmp_path):
        svc = FB2CompilerService()
        books = []
        for i, title in enumerate(["A", "B", "C", "D", "E", "F"]):
            p = tmp_path / f"{title}.fb2"
            p.write_text("<x/>", encoding="utf-8")
            rec = _rec(title)
            books.append(CompilationBook(
                record=rec, abs_path=p, sort_key=(9, 0, 0, 0),
                sort_source="unknown", order_ambiguous=True,
            ))
        group = CompilationGroup(
            author="Житинский Александр", series="Младший научный сотрудник Петр Верлухин",
            books=books, order_determined=False, volume_range="",
        )
        suffix, lo, hi = svc.compute_group_suffix(group)
        assert suffix == "в 6 книгах"
        assert "Гексалогия" not in suffix
