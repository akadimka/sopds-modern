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


def test_web_preview_name_matches_real_compiled_name(tmp_path):
    """fb2parser_web._serialize_compiler_group() строила суффикс имени файла
    отдельной (урезанной) формулой вместо вызова той же логики, что и
    реальная сборка — обнаружено на реальной группе "Злотников Роман,
    Николаев Андрей / Мир вечного": превью показывало "Мир вечного
    (Дилогия)", а реальный скомпилированный файл — "Мир вечного (Дилогия
    в 8 книгах)" (2 арки, каждая — уже готовая тетралогия из 4 книг).

    Обе стороны теперь зовут FB2CompilerService.compute_group_suffix() —
    этот тест использует ту же fixture-группу (compile_completeness), что
    и тесты выше, чтобы гарантировать: превью и реальный результат не
    могут разойтись физически.

    ВАЖНО: `compile_group()` безусловно удаляет `group.duplicate_paths`
    (не только при `delete_sources=True`) — работаем на КОПИИ fixture во
    временной директории, а не на закоммиченных файлах напрямую, иначе
    прогон теста стирает "Пенталогия"-файл из tests/data/.
    """
    import re
    import shutil
    from fb2parser_core import regen_csv
    from fb2parser_core.fb2_compiler import FB2CompilerService
    from fb2parser_web.fb2parser_bridge import _config_path

    work_dir = tmp_path / "compile_completeness"
    shutil.copytree(LIBRARY_ROOT, work_dir)

    service = regen_csv.RegenCSVService(_config_path())
    records = service.generate_csv(str(work_dir), output_csv_path=None)
    svc = FB2CompilerService()
    groups = svc.find_groups(records, work_dir)
    matches = [g for g in groups if g.author == "Автор Тест"]
    assert len(matches) == 1
    group = matches[0]

    suffix, _lo, _hi = svc.compute_group_suffix(group)
    clean_s = FB2CompilerService._clean_series_name(group.series)
    safe_a = re.sub(r'[\\/:*?"<>|]', '_', group.author)
    safe_s = re.sub(r'[/:*?"<>|]', '_', FB2CompilerService._series_to_display(clean_s))
    if suffix:
        suffix = svc._suppress_redundant_suffix(safe_s, suffix)
    preview_name = f"{safe_a} - {safe_s} ({suffix}).fb2" if suffix else f"{safe_a} - {safe_s}.fb2"

    out_dir = tmp_path / "out"
    result = svc.compile_group(group, output_dir=out_dir, delete_sources=False)
    assert result.success
    assert preview_name == result.output_path.name


class TestRunStatsDuplicatePositionCount:
    """Обнаружено на реальной библиотеке (Лисина Александра / "Артур Рэйш"):
    превью показывало "Тетралогия в 10 книгах" для группы из 5 файлов с
    диапазоном 1-9 — 10 не может быть верным ни для диапазона 1-9, ни для
    5 файлов.

    Причина в `FB2CompilerService._run_stats()`: диапазонные книги
    ("01-02.fb2" → volume_label="1-2") корректно объединялись через
    set-union (пересечения не задваиваются), а книги БЕЗ диапазона
    ("09. Название.fb2", volume_label="9") считались через отдельный
    счётчик `_individual += 1` — БЕЗ проверки, что эта же позиция уже
    занята диапазоном или другой такой же "одиночной" книгой. В реальных
    данных "09. Последний бог.fb2" и "09.Проклятие королей.fb2" — два
    РАЗНЫХ файла с ОДИНАКОВЫМ series_number=9 (ошибка нумерации/дубль
    исходника) — оба считались как +1, раздувая n_volumes с 9 до 10.

    Реальная сборка (`compile_group()`) дедуплицирует такие коллизии по
    `covered_hi` при мёрже контента и на выходе даёт ровно 9 томов —
    `_run_stats()` должен считать так же, чтобы превью не расходилось
    с результатом (тот же принцип, что и в тесте выше).
    """

    @staticmethod
    def _book(volume_label: str, sort_key: tuple) -> "CompilationBook":
        from fb2parser_core.fb2_compiler import CompilationBook
        return CompilationBook(
            record=None, abs_path=Path("dummy.fb2"),
            sort_key=sort_key, sort_source="filename_prefix",
            order_ambiguous=False, volume_label=volume_label,
        )

    def test_duplicate_flat_position_not_double_counted(self):
        from fb2parser_core.fb2_compiler import FB2CompilerService
        books = [
            self._book("1-2", (0, 1, 0, 0)),
            self._book("3-4", (0, 3, 0, 0)),
            self._book("9", (0, 9, 0, 0)),
            self._book("9", (0, 9, 0, 0)),  # дубль позиции 9 — не должен считаться дважды
        ]
        top_lo, top_hi, n_volumes, has_subseries, n_top_arcs = FB2CompilerService._run_stats(books)
        assert n_volumes == 5  # {1,2,3,4} ∪ {9}, не 6

    def test_non_overlapping_flat_positions_still_all_counted(self):
        """Разные позиции (не дубли) по-прежнему считаются каждая отдельно."""
        from fb2parser_core.fb2_compiler import FB2CompilerService
        books = [
            self._book("1-2", (0, 1, 0, 0)),
            self._book("5", (0, 5, 0, 0)),
            self._book("6", (0, 6, 0, 0)),
        ]
        top_lo, top_hi, n_volumes, has_subseries, n_top_arcs = FB2CompilerService._run_stats(books)
        assert n_volumes == 4  # {1,2} ∪ {5} ∪ {6}

    def test_books_without_position_still_counted_individually(self):
        """Книги без определённой позиции (sort_key[1]==0) — дедуплицировать
        не по чему, каждая по-прежнему считается отдельно (старое поведение)."""
        from fb2parser_core.fb2_compiler import FB2CompilerService
        books = [
            self._book("", (0, 0, 0, 0)),
            self._book("", (0, 0, 0, 0)),
        ]
        top_lo, top_hi, n_volumes, has_subseries, n_top_arcs = FB2CompilerService._run_stats(books)
        assert n_volumes == 2
