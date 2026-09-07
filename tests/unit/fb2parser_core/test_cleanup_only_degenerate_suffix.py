"""Регрессия для `FB2CompilerService.compute_group_suffix()` — docs/
quality-roadmap.md, баг №36.

Реальный случай (замечен пользователем в превью компиляции): "Алейникова
Юлия / Тайное сокровище Айвазовского" — дедуп cleanup_only-группы свёлся
к единственному выжившему файлу (второй — дубликат с более коротким
именем, помечен на удаление) БЕЗ настоящего диапазона томов. Превью
показывало "Алейникова Юлия - Тайное сокровище Айвазовского (в 0
книгах).fb2" — вырожденный, бессмысленный суффикс. Реальный
`compile_group()` в этом случае НИЧЕГО не переименовывает (файл остаётся
как есть) — превью расходилось с фактическим результатом.
"""
from fb2parser_core.fb2_compiler import CompilationGroup, FB2CompilerService


class TestCleanupOnlyWithoutRealRangeGetsNoSuffix:
    def test_no_volume_range_gives_empty_suffix(self):
        group = CompilationGroup(
            author="Алейникова Юлия", series="Тайное сокровище Айвазовского",
            books=[], order_determined=True, volume_range="",
            cleanup_only=True,
        )
        svc = FB2CompilerService()
        suffix, lo, hi = svc.compute_group_suffix(group)
        assert suffix == ""
        assert (lo, hi) == (0, 0)

    def test_real_volume_range_still_produces_suffix(self):
        # Sanity: настоящий диапазон томов по-прежнему даёт суффикс
        # (старое поведение для реальных cleanup_only-групп не сломано).
        group = CompilationGroup(
            author="Автор Тест", series="Серия Тест",
            books=[], order_determined=True, volume_range="1-3",
            cleanup_only=True,
        )
        svc = FB2CompilerService()
        suffix, lo, hi = svc.compute_group_suffix(group)
        assert suffix == "Трилогия"
        assert (lo, hi) == (1, 3)
