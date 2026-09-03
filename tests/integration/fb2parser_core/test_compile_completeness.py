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


ANNOTATION_TOC_ROOT = Path(__file__).resolve().parents[2] / "data" / "compile_annotation_toc"


class TestAnnotationTableOfContentsRange:
    """Обнаружено на реальной библиотеке (Маханенко Василий / "Клан Медведя"):
    "Клан Медведя. Сборник.fb2" — уже готовая компиляция всех 5 книг серии,
    но не размечает тома структурно (нет <sequence>/заголовков секций "Книга
    N") — только вольным текстом в <annotation> ("Содержание: 1. <strong>
    Автор</strong>: Название ..."). Без разбора этого текста файл выглядел
    как "позиция неизвестна" и вместо распознавания как полная предкомпиляция
    смешивался наравне с отдельным файлом "05. Медведюк.fb2" (тоже уже
    содержащимся ВНУТРИ сборника) в отдельную, вторую группу компиляции —
    серия физически рвалась на 2 группы ("т. 1-2" и "т. 5") вместо одной
    "Пенталогии", и результат содержал бы дублированную пятую книгу.

    tests/data/compile_annotation_toc/ — синтетическая fixture: 3 отдельных
    файла (тома 1, 2, 5) + "Сборник" с тем же текстовым оглавлением (тома
    1-5), что и в реальном случае, но с фиктивным текстом.
    """

    @pytest.fixture(scope="class")
    def group(self):
        service = regen_csv.RegenCSVService(_config_path())
        records = service.generate_csv(str(ANNOTATION_TOC_ROOT), output_csv_path=None)
        svc = FB2CompilerService()
        groups = svc.find_groups(records, ANNOTATION_TOC_ROOT)
        matches = [g for g in groups if g.author == "Тест Автор3"]
        assert len(matches) == 1  # раньше падало бы на 2 — регрессия сюда
        return matches[0]

    def test_series_forms_one_group_not_two(self, group):
        assert group.series_complete is True
        assert group.volume_range == "1-5"

    def test_standalone_volumes_marked_duplicate_of_the_collection(self, group):
        kept_names = {p.name for p in (group.kept_paths or [])}
        dup_names = {p.name for p in (group.duplicate_paths or [])}
        assert kept_names == {"Серия Тест3. Сборник.fb2"}
        assert dup_names == {"01. Книга А.fb2", "02. Книга Б.fb2", "05. Книга Д.fb2"}

    def test_annotation_toc_scopes_to_matching_series_in_multi_series_bundle(self):
        """Если аннотация перечисляет НЕСКОЛЬКО серий под заголовками
        (как в "Vitovt"-сборниках вида "Циклы фантастических романов"),
        диапазон должен браться только из сегмента нужной серии — не должен
        расширяться на чужие тома или ошибочно давать (0, 0) из-за общей
        подписи "Содержание:", тоже обёрнутой в <strong> в некоторых файлах.
        """
        text = (
            '<annotation>'
            '<p><strong>Содержание:</strong></p>'
            '<p><strong>ПЕРВАЯ СЕРИЯ:</strong></p>'
            '<p>1. <strong>Автор</strong>: Título Один</p>'
            '<p>2. <strong>Автор</strong>: Título Два</p>'
            '<p><strong>ВТОРАЯ СЕРИЯ:</strong></p>'
            '<p>1. <strong>Автор</strong>: Título Три</p>'
            '<p>2. <strong>Автор</strong>: Título Четыре</p>'
            '<p>3. <strong>Автор</strong>: Título Пять</p>'
            '</annotation>'
        )
        svc = FB2CompilerService()
        assert svc._annotation_toc_range(text, "Первая серия") == (1, 2)
        assert svc._annotation_toc_range(text, "Вторая серия") == (1, 3)
        assert svc._annotation_toc_range(text, "Совсем другая серия") == (0, 0)
