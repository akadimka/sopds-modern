"""Регрессия для `FB2CompilerService._precompiled_range()` — привязка
диапазона тома к серии через название СОСЕДНЕЙ книги группы, а не только
через имя самой серии.

Реальный случай (Гамильтон Питер / "Пришествие Ночи"): "Дисфункция
реальности 1-2 (альт. издание).fb2" покрывает содержимое томов "Дисфункция
реальности: Увертюра"(2) и "...Угроза"(3), и диапазон "1-2" явно написан в
имени файла. Но имя серии-франшизы "Пришествие Ночи" не встречается в имени
файла вовсе — тома трилогии называются по именам романов ("Дисфункция
реальности", "Нейтронный Алхимик", "Обнажённый Бог"), а не по названию
серии-зонтика. Проверка "привязки к серии" в _precompiled_range() до фикса
учитывала только слова из ИМЕНИ СЕРИИ — диапазон "1-2" в этом файле
игнорировался целиком, и файл попадал в компиляцию как обычный одиночный
том вместо признанной предкомпиляции, дублируя содержимое.
"""
from pathlib import Path

from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.fb2_compiler import FB2CompilerService


def _rec(file_path, title, series_number="", series="Пришествие Ночи"):
    return BookRecord(
        file_path=file_path, file_title=title,
        metadata_authors="Питер Гамильтон", proposed_author="Гамильтон Питер",
        author_source="folder_dataset", metadata_series=series, proposed_series=series,
        series_source="folder_dataset", series_number=series_number,
        series_number_source="filename_prefix" if series_number else "",
    )


class TestSiblingTitleLinksAmbiguousRange:
    def test_range_ignored_without_sibling_titles(self):
        # Sanity: без sibling_titles ведёт себя как раньше — диапазон
        # игнорируется, серия-франшиза в имени файла отсутствует.
        svc = FB2CompilerService()
        rec = _rec("Дисфункция реальности 1-2 (альт. издание).fb2",
                    "Дисфункция реальности", series_number="1")
        book = svc._make_book(rec, Path("."))
        lo, hi = svc._precompiled_range(book, "Пришествие Ночи")
        assert (lo, hi) == (0, 0)

    def test_range_recognized_via_sibling_base_title(self):
        svc = FB2CompilerService()
        rec = _rec("Дисфункция реальности 1-2 (альт. издание).fb2",
                    "Дисфункция реальности", series_number="1")
        book = svc._make_book(rec, Path("."))
        sibling_titles = [
            "Дисфункция реальности: Увертюра",
            "Дисфункция реальности: Угроза",
            "Нейтронный Алхимик: Консолидация",
        ]
        lo, hi = svc._precompiled_range(book, "Пришествие Ночи", sibling_titles)
        assert (lo, hi) == (1, 2)

    def test_short_sibling_base_not_used(self):
        # База короче 8 символов после нормализации не используется —
        # слишком высок риск случайного совпадения с посторонним текстом.
        svc = FB2CompilerService()
        rec = _rec("Атлас 1-2 (сборник).fb2", "Атлас", series_number="1")
        book = svc._make_book(rec, Path("."))
        lo, hi = svc._precompiled_range(book, "Пришествие Ночи", ["Атлас: Восток"])
        assert (lo, hi) == (0, 0)

    def test_full_group_deduplicates_covered_volume(self):
        records = [
            _rec("2. Дисфункция реальности. Увертюра.fb2",
                 "Дисфункция реальности: Увертюра", series_number="2"),
            _rec("3. Дисфункция реальности. Угроза.fb2",
                 "Дисфункция реальности: Угроза", series_number="3"),
            _rec("Дисфункция реальности 1-2 (альт. издание).fb2",
                 "Дисфункция реальности", series_number="1"),
        ]
        svc = FB2CompilerService()
        groups = svc.find_groups(records, Path("."))
        assert len(groups) == 1
        group = groups[0]

        # Альт-издание теперь распознано как диапазон 1-2 и вытеснило
        # покрытый им отдельный том "Увертюра" (позиция 2) в дубликаты.
        alt_edition = next(
            b for b in group.books
            if "альт. издание" in b.abs_path.name
        )
        assert alt_edition.volume_label == "1-2"
        dup_names = {p.name for p in group.duplicate_paths}
        assert "2. Дисфункция реальности. Увертюра.fb2" in dup_names
