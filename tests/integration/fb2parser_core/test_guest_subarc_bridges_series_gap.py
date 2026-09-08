"""Регрессия для `FB2CompilerService.find_groups()` — docs/quality-
roadmap.md, баг №37.

Реальный случай (замечен пользователем в CSV+превью компиляции):
Роллинс Джеймс / "Отряд «Сигма»" — основная серия томов 1-17, но позиция
8 занята отдельно скомпилированной тетралогией "Такер Уэйн" (4 книги-
спин-оффа с ДРУГИМ соавтором на части томов, все с series_number="8").

Без учёта этого "гостевого" однослотового под-цикла основная серия
физически рвалась на ДВА отдельных, неполных куска: "т. 1-7" и "т.
9-17". По явному запросу пользователя итог должен быть ОДНОЙ серией
"Отряд «Сигма»" 1-17, где позиция 8 (тетралогия) входит ВНУТРЬ как
физическая часть той же компиляции — не отдельным файлом.

Использует РЕАЛЬНЫЕ файлы на диске (не просто BookRecord с
несуществующими путями) — `_dedup_by_content()` читает содержимое книг
для проверки на дубли, и с одинаковым (пустым/нечитаемым) содержимым
разные тома Такера Уэйна ложно распознавались бы как дубли друг друга.
"""
from pathlib import Path

import pytest

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import FB2CompilerService

_FB2_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Джеймс</first-name><last-name>Роллинс</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><title><p>{title}</p></title><section><p>{text}</p></section></body></FictionBook>
"""

_FLAT_TITLES = {
    1: "Песчаный дьявол", 2: "Кости волхвов", 3: "Чёрный орден",
    4: "Печать Иуды", 5: "Последний оракул", 6: "Ключ Судного дня",
    7: "Дьявольская колония", 9: "Глаз Бога", 10: "Шестое вымирание",
    11: "Костяной лабиринт", 12: "Седьмая казнь", 13: "Венец демона",
    14: "Пекло", 15: "Последняя одиссея", 16: "Царство костей",
    17: "Волна огня",
}
_TUCKER_TITLES = ["Ночная охота", "Линия крови", "Убийцы смерти", "Ястребы войны"]


def _write_book(dir_: Path, filename: str, title: str, unique_text: str) -> Path:
    p = dir_ / filename
    p.write_text(_FB2_TMPL.format(title=title, text=unique_text * 20), encoding="utf-8")
    return p


@pytest.fixture
def records(tmp_path):
    recs = []
    for n, title in _FLAT_TITLES.items():
        fname = f"Роллинс Джеймс - Отряд Сигма {n}. {title}.fb2"
        _write_book(tmp_path, fname, title, f"Текст книги {n} про {title}, уникальное содержание {n}.")
        recs.append(BookRecord(
            file_path=fname, file_title=title, metadata_authors="Джеймс Роллинс",
            proposed_author="Роллинс Джеймс", author_source="filename",
            metadata_series="Отряд «Сигма»", proposed_series="Отряд «Сигма»",
            series_source="filename", series_number=str(n),
        ))
    for i, title in enumerate(_TUCKER_TITLES, 1):
        fname = f"Роллинс Джеймс - Отряд Сигма 08. Такер Уэйн {i}. {title}.fb2"
        _write_book(tmp_path, fname, title, f"Такер Уэйн эпизод {i}: {title}, совсем другой текст номер {i}.")
        recs.append(BookRecord(
            file_path=fname, file_title=title, metadata_authors="Ребекка Кантрелл; Джеймс Роллинс",
            proposed_author="Роллинс Джеймс", author_source="filename",
            metadata_series="Отряд «Сигма»\\Такер Уэйн", proposed_series="Отряд «Сигма»\\Такер Уэйн",
            series_source="filename_named_arc", series_number="8",
        ))
    return recs


class TestGuestSubArcMergesIntoSingleParentSeries:
    def test_single_group_covers_all_20_books(self, records, tmp_path):
        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)

        matches = [g for g in groups if g.author == "Роллинс Джеймс"]
        assert len(matches) == 1, [g.series for g in matches]
        main = matches[0]

        assert len(main.books) == 20
        assert main.volume_range == "1-17"
        assert main.series_complete is True
        assert not (main.duplicate_paths or [])

        suffix, lo, hi = svc.compute_group_suffix(main)
        assert (lo, hi) == (1, 17)
        assert "т." not in suffix  # не считается неполной серией

    def test_tucker_wayne_books_keep_distinct_internal_positions(self, records, tmp_path):
        svc = FB2CompilerService()
        groups = svc.find_groups(records, tmp_path)
        main = next(g for g in groups if g.author == "Роллинс Джеймс")

        tucker_books = [b for b in main.books if "Такер Уэйн" in b.abs_path.stem]
        assert len(tucker_books) == 4
        # Все делят родительский слот 8 (sort_key[1]=8), но у каждого — СВОЯ
        # внутренняя позиция (sort_key[2]) — иначе dedup спутал бы их с
        # дублями друг друга.
        assert {b.sort_key for b in tucker_books} == {
            (0, 8, 1, 0), (0, 8, 2, 0), (0, 8, 3, 0), (0, 8, 4, 0),
        }


class TestRealGapWithoutGuestCoverageStillIncomplete:
    def test_gap_without_covering_subarc_still_splits(self):
        # Sanity: пробел, который НИЧЕМ не закрыт (нет гостевого под-цикла
        # с тем же номером), по-прежнему честно рвёт серию на два куска —
        # мостование/слияние срабатывает только когда пробел объяснён.
        records = [
            BookRecord(
                file_path=f"Автор Тест - Серия {n}. Т{n}.fb2", file_title=f"Т{n}",
                metadata_authors="Автор Тест", proposed_author="Автор Тест",
                author_source="filename", metadata_series="Серия", proposed_series="Серия",
                series_source="filename", series_number=str(n),
            )
            for n in [1, 2, 3, 5, 6, 7]
        ]
        svc = FB2CompilerService()
        groups = svc.find_groups(records, Path("."))
        assert len(groups) == 2
        assert all(not g.series_complete for g in groups)
