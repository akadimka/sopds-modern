"""Регрессия для `FB2CompilerService.find_groups()`'s "POST-PASS: подавить
compile-группы, полностью покрытые другой группой" (Случай A, parent-
child) — docs/quality-roadmap.md, баг №109.

Реальный случай (Test1, Эльтеррус Иар (Тертышный Игорь)): подсерия
"Русский Сонм\\Горькие травы" (т. 1-3, книги "Лунное стекло"/"Священный
метод"/"Дар") и родительская серия "Русский Сонм" (ТОЖЕ т. 1-3, но
СОВЕРШЕННО ДРУГИЕ книги "Огонь и ветер"/"Игры морока"/"Демиурги. Полигон
богов") — два независимых произведения одной авторской вселенной, у
каждого своя локальная нумерация 1-3. Раньше числовое совпадение
диапазонов (без проверки содержимого) считалось ДОСТАТОЧНЫМ признаком,
что подсерия уже "покрыта" родителем — помечало ВСЕ 3 файла подсерии как
дубликаты с `kept_paths=[]` (ничего не остаётся взамен!) — при
применении в UI это удалило бы книги безвозвратно, не заменяя их ничем.

Починено: теперь требуется полное совпадение по `content_hash` (не
числовой диапазон) — то же условие, что уже использовалось как fallback
и как единственное условие в Случае B/C рядом. Пример из докстрока кода
("Рубеж\\Сирийский рубеж (т. 5-8) ⊂ Рубеж (1-11)") по-прежнему работает
корректно: там подсерия и родитель реально делят одни и те же файлы,
поэтому хэши совпадают.
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import FB2CompilerService

_STUB_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Тест</first-name><last-name>Автор</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><title><p>{title}</p></title><section><p>Текст.</p></section></body>
</FictionBook>
"""


def _write(tmp_path: Path, rel_path: str, title: str) -> str:
    p = tmp_path / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_STUB_FB2.format(title=title), encoding="utf-8")
    return rel_path


def _rec(file_path, title, series, series_number, content_hash):
    return BookRecord(
        file_path=file_path, file_title=title, metadata_authors="Автор Тест",
        proposed_author="Автор Тест", author_source="folder_dataset",
        metadata_series=series, proposed_series=series, series_source="filename_named_arc",
        series_number=series_number, series_number_source="filename_prefix",
        content_hash=content_hash,
    )


class TestParentChildSuppressionRequiresContentHashMatch:
    def test_unrelated_subseries_with_coincidentally_matching_range_not_suppressed(self, tmp_path):
        # "Русский Сонм" (родитель, т. 1-3) — hash h1/h2/h3
        parent_recs = [
            _rec(_write(tmp_path, f"Русский Сонм\\{n}. П{n}.fb2", f"П{n}"),
                 f"П{n}", "Русский Сонм", str(n), f"parent-hash-{n}")
            for n in (1, 2, 3)
        ]
        # "Русский Сонм\Горькие травы" (подсерия, ТОЖЕ т. 1-3) — hash x1/x2/x3, НЕ пересекаются
        child_recs = [
            _rec(_write(tmp_path, f"Русский Сонм\\Горькие травы\\{n}. Д{n}.fb2", f"Д{n}"),
                 f"Д{n}", "Русский Сонм\\Горькие травы", str(n), f"child-hash-{n}")
            for n in (1, 2, 3)
        ]

        svc = FB2CompilerService()
        groups = svc.find_groups(parent_recs + child_recs, tmp_path)

        child_group = next(g for g in groups if g.series == "Русский Сонм\\Горькие травы")
        assert child_group.cleanup_only is False
        assert len(child_group.books) == 3
        assert child_group.duplicate_paths == []

    def test_genuine_shared_content_subseries_still_suppressed(self, tmp_path):
        # "Рубеж" (родитель, т. 1-3) — hash shared-hash-1/2/3.
        parent_recs = [
            _rec(_write(tmp_path, f"Рубеж\\{n}. Р{n}.fb2", f"Р{n}"),
                 f"Р{n}", "Рубеж", str(n), f"shared-hash-{n}")
            for n in (1, 2, 3)
        ]
        # "Рубеж\Сирийский рубеж" (подсерия, своя локальная нумерация 5-6,
        # НЕ пересекается с позициями родителя — чтобы не сработал ДРУГОЙ,
        # более ранний механизм слияния "гостевой подсерии" по общей
        # позиции) — но её файлы РЕАЛЬНО те же, что и часть родительских
        # (общий content_hash: shared-hash-2/shared-hash-3) — та самая
        # ситуация из докстрока кода ("Рубеж\Сирийский рубеж (т. 5-8) ⊂
        # Рубеж (1-11)": подсерия физически содержит те же файлы, что и
        # часть родителя, просто под другим именем/нумерацией).
        child_recs = [
            _rec(_write(tmp_path, f"Рубеж\\Сирийский рубеж\\{n}. С{n}.fb2", f"С{n}"),
                 f"С{n}", "Рубеж\\Сирийский рубеж", str(n), f"shared-hash-{n - 3}")
            for n in (5, 6)
        ]

        svc = FB2CompilerService()
        groups = svc.find_groups(parent_recs + child_recs, tmp_path)

        assert len(groups) == 2
        compile_group = next(g for g in groups if not g.cleanup_only)
        suppressed_group = next(g for g in groups if g.cleanup_only)

        assert len(compile_group.books) == 3
        assert suppressed_group.books == []
        assert len(suppressed_group.duplicate_paths) == 2
        assert suppressed_group.kept_paths == []
