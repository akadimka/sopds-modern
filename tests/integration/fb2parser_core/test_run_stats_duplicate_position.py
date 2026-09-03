"""Регрессионные тесты для FB2CompilerService._run_stats(): подсчёт
n_volumes/has_subseries не должен путать НАСТОЯЩИЙ дубль/опечатку номера
тома (два РАЗНЫХ файла на одной и той же позиции) с ЛЕГИТИМНОЙ
подсерией/частью (несколько разных файлов на одной top-позиции, но с
разной под-позицией sort_key[2] или sort_key[3]).

Обнаружено на реальной библиотеке (Лисина Александра / "Артур Рэйш"):
5-файловая группа (диапазоны 1-4, 5-6, 7-8 + два РАЗНЫХ файла, оба
претендующих на позицию 9 — "09. Последний бог.fb2" и "09.Проклятие
королей.fb2") давала суффикс "Тетралогия в 10 книгах" — внутренне
противоречивое число (не совпадает ни с числом файлов, ни с реальным
диапазоном 1-9). Причина: диапазоны объединялись в множество (без
задвоения пересечений), а одиночные позиции считались отдельным
счётчиком БЕЗ дедупа — и `len(_top_pos_list) > len(set(...))` (сравнение
списка с дублем позиции 9 против множества) ошибочно трактовалось как
"несколько дуг" (аналогично реальной подсерии), давая слово "Тетралогия"
вместо честного "Ноналогия" (9 позиций).

Использует CompilationBook напрямую (без реальных fb2-файлов) — тест
чистой логики подсчёта, файлы на диск не нужны.
"""
from pathlib import Path
from types import SimpleNamespace

from fb2parser_core.fb2_compiler import CompilationBook, FB2CompilerService


def _book(path, sort_key, volume_label, sort_source="filename_range"):
    return CompilationBook(
        record=SimpleNamespace(file_title=path),
        abs_path=Path(path),
        sort_key=sort_key,
        sort_source=sort_source,
        order_ambiguous=False,
        volume_label=volume_label,
    )


class TestDuplicateFlatPositionNotCountedTwice:
    """"Артур Рэйш" — два РАЗНЫХ файла (разное содержание, судя по
    названиям) оба претендуют на позицию 9 из-за опечатки в номере тома
    у одного из них. Должны считаться ОДНОЙ позицией, а не двумя.
    """

    def test_duplicate_position_gives_correct_total_and_plain_word(self):
        books = [
            _book("Артур Рэйш (Сборник, кн. 1-4).fb2", (0, 1, 0, 0), "1-4"),
            _book("05-06. Жнец.fb2", (0, 5, 0, 0), "5-6"),
            _book("07-08. Темный маг.fb2", (0, 7, 0, 0), "7-8"),
            _book("09. Последний бог.fb2", (0, 9, 0, 0), "9", sort_source="series_number"),
            _book("09.Проклятие королей.fb2", (0, 9, 0, 0), "9", sort_source="series_number"),
        ]
        top_lo, top_hi, n_volumes, has_subseries, n_top_arcs = FB2CompilerService._run_stats(books)
        assert (top_lo, top_hi, n_volumes) == (1, 9, 9)
        assert has_subseries is False
        assert n_top_arcs is None

        suffix = FB2CompilerService()._series_suffix(n_volumes, top_lo, top_hi, 0, series_complete=True)
        assert suffix == "Ноналогия"


class TestGenuineSubseriesBySecondIndexStillDetected:
    """"Иванович Юрий / Миры Доставки" — 4 РАЗНЫХ файла на одной и той же
    top-позиции (5), но с разным sort_key[2] (1,2,3,4) — настоящая
    подсерия из 4 книг, не дубль. Должна остаться has_subseries=True с
    правильным числом книг (4), а не схлопнуться в 1 книгу.
    """

    def test_different_sub_ordinals_counted_separately(self):
        books = [
            _book("1. Оскал фортуны.fb2", (0, 5, 1, 0), "5.1", sort_source="subseries_number"),
            _book("2. Капризная Фортуна.fb2", (0, 5, 2, 0), "5.2", sort_source="subseries_number"),
            _book("3. Жестокая фортуна.fb2", (0, 5, 3, 0), "5.3", sort_source="subseries_number"),
            _book("4. Благосклонная фортуна.fb2", (0, 5, 4, 0), "5.4", sort_source="subseries_number"),
        ]
        top_lo, top_hi, n_volumes, has_subseries, n_top_arcs = FB2CompilerService._run_stats(books)
        assert (top_lo, top_hi, n_volumes) == (5, 5, 4)
        assert has_subseries is True
        assert n_top_arcs == 1


class TestGenuineSubseriesByThirdIndexStillDetected:
    """"Hawk1 / Фарт 2" — 2 РАЗНЫХ файла на одной top-позиции (2) с
    одинаковым sort_key[2]=0, но разным sort_key[3] (1,2 — "inline"-части
    вида "Часть 1"/"Часть 2"). Тоже настоящая подсерия, не дубль —
    отличается в ТРЕТЬЕЙ координате, не во второй.
    """

    def test_different_inline_parts_counted_separately(self):
        books = [
            _book("hawk1. Фарт 2. По следам друзей. Часть 1.fb2", (0, 2, 0, 1), "2.1", sort_source="inline_title"),
            _book("hawk1. Фарт 2. По следам друзей. Часть 2.fb2", (0, 2, 0, 2), "2.2", sort_source="inline_title"),
        ]
        top_lo, top_hi, n_volumes, has_subseries, n_top_arcs = FB2CompilerService._run_stats(books)
        assert (top_lo, top_hi, n_volumes) == (2, 2, 2)
        assert has_subseries is True
        assert n_top_arcs == 1
