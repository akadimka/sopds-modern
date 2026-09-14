"""Регрессия для `FB2CompilerService.find_groups()` — docs/quality-roadmap.md,
баг №67.

Реальный случай (Клеванский Кирилл / "Сердце Дракона", продолжение
анализа бага №66): хвостовая пометка «(Автор)» в конце серии у части
файлов не давала им попасть в один бакет компиляции с остальными файлами
той же серии без пометки — `_series_group_key()`/`_punct_norm()` в
`find_groups()` не стриговал эту пометку (в отличие от
`_strip_author_suffix()` в `pass4_consensus.py`, используемого для
отображаемого значения серии, а не для ключа группировки компилятора).
"""
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord


def _rec(path, series, num, author="Автор Тест", title=None):
    return BookRecord(
        file_path=path, file_title=title or path, metadata_authors=author,
        proposed_author=author, author_source="metadata",
        metadata_series=series, proposed_series=series, series_source="metadata",
        series_number=num, series_number_source="metadata",
    )


class TestSeriesGroupKeyIgnoresTrailingAuthorSuffix:
    def test_trailing_author_suffix_merges_into_same_group(self, tmp_path):
        records = [
            _rec("Автор Тест - Огнебор 1.fb2", "Огнебор", "1"),
            _rec("Автор Тест - Огнебор 2.fb2", "Огнебор", "2"),
            _rec("Автор Тест - Огнебор 3.fb2", "Огнебор (Автор Тест)", "3"),
            _rec("Автор Тест - Огнебор 4.fb2", "Огнебор (Автор Тест)", "4"),
        ]

        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        matches = [g for g in groups if g.author == "Автор Тест"]
        assert len(matches) == 1, f"expected 1 merged group, got {len(matches)}"
        assert len(matches[0].books) == 4
