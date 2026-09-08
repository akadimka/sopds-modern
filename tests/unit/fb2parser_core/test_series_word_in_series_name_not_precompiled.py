"""Регрессия для `FB2CompilerService._precompiled_range()` и связанной
контекстной коррекции в `find_groups()` — docs/quality-roadmap.md, баг
№51.

Реальный случай (замечен пользователем в превью компилятора): Чакраборти
Шеннон А. / "Трилогия Дэвабада" — 3 обычных, разных тома (1. Латунный
город, 2. Медное королевство, 3. Золотая империя) + отдельный сборник
рассказов без номера ("Серебряная река"). Слово "Трилогия" в имени
файла КАЖДОГО тома распозналось как служебный маркер "это N-в-одном
файле" (как в "Орёл (Тетралогия)"), хотя оно здесь — часть СОБСТВЕННОГО
названия серии ("Трилогия Дэвабада"), а не признак того, что отдельный
файл тома 1 сам по себе содержит все три тома. Итог: дедуп по
"предкомпиляциям" решил, что все 4 файла — избыточные копии одного и
того же диапазона 1-3, и вычистил 3 из 4 как "дубликаты", оставив
только том 1.
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import FB2CompilerService


def _rec(n, title, series_number=""):
    fname = f"Чакраборти Шеннон - Трилогия Дэвабада {n}. {title}.fb2" if n else \
        f"Чакраборти Шеннон - Трилогия Дэвабада. {title}.fb2"
    return BookRecord(
        file_path=fname, file_title=title,
        metadata_authors="Шеннон А. Чакраборти", proposed_author="Чакраборти Шеннон А.",
        author_source="filename", metadata_series="Трилогия Дэвабада",
        proposed_series="Трилогия Дэвабада", series_source="filename+meta_confirmed",
        series_number=series_number,
    )


class TestSeriesWordEmbeddedInSeriesNameNotTreatedAsPrecompilation:
    def test_precompiled_range_ignores_series_own_name(self):
        svc = FB2CompilerService()
        rec = _rec(1, "Латунный город", series_number="1")
        book = svc._make_book(rec, Path("."))
        lo, hi = svc._precompiled_range(book, "Трилогия Дэвабада")
        assert (lo, hi) == (0, 0)

    def test_all_four_volumes_survive_find_groups(self):
        records = [
            _rec(1, "Латунный город", series_number="1"),
            _rec(2, "Медное королевство", series_number="2"),
            _rec(3, "Золотая империя", series_number="3"),
            _rec(0, "Серебряная река (рассказы)"),
        ]
        svc = FB2CompilerService()
        groups = svc.find_groups(records, Path("."))

        trilogy_groups = [g for g in groups if g.author == "Чакраборти Шеннон А."]
        assert len(trilogy_groups) == 1, [g.series for g in trilogy_groups]
        main = trilogy_groups[0]

        assert not getattr(main, "cleanup_only", False)
        assert len(main.books) == 3
        assert not (main.duplicate_paths or [])
        kept_names = {b.abs_path.name for b in main.books}
        assert kept_names == {
            "Чакраборти Шеннон - Трилогия Дэвабада 1. Латунный город.fb2",
            "Чакраборти Шеннон - Трилогия Дэвабада 2. Медное королевство.fb2",
            "Чакраборти Шеннон - Трилогия Дэвабада 3. Золотая империя.fb2",
        }

    def test_genuine_service_word_precompilation_still_detected(self):
        # Sanity: настоящая предкомпиляция ("Стрела (Тетралогия)" — слово
        # НЕ входит в название серии "Стрела") по-прежнему распознаётся.
        svc = FB2CompilerService()
        rec = BookRecord(
            file_path="Автор Тест - Стрела (Тетралогия).fb2", file_title="Стрела",
            metadata_authors="Автор Тест", proposed_author="Автор Тест",
            author_source="filename", metadata_series="Стрела", proposed_series="Стрела",
            series_source="filename", series_number="",
        )
        book = svc._make_book(rec, Path("."))
        lo, hi = svc._precompiled_range(book, "Стрела")
        assert (lo, hi) == (1, 4)
