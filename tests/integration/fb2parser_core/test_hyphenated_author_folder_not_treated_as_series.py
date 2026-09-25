"""Регрессия для `RegenCSVService._compute_folder_series()` (PUBLISHER/
COLLECTION-ветка) — docs/quality-roadmap.md, баг №115.

Реальный случай (Test1, `Серия - «LitRPG»\\Hеофициальная серия книг
(разные издательства)\\`): при структуре `<АвторПапка>\\<СерияПапка>\\
файл.fb2` под издательской корневой папкой, для авторов с папкой вида
«Фамилия-Имя» (частый паттерн самиздата — «Мантикор-Артемис»,
«Синицын-Владимир», «Тихонов-Михаил» и т.п.) папка автора ошибочно
принималась за папку серии и приклеивалась к реальной серии через
`\\`: `proposed_series = "Мантикор-Артемис\\Мир Мельхиора"` вместо
чистого `"Мир Мельхиора"`.

Причина: `_is_author_variant` (regen_csv.py, внутри
`_compute_folder_series`) сравнивал нормализованные строки без учёта
дефиса как разделителя слов — «мозолевский-павел» (один токен) не
совпадал с «мозолевский павел» (два слова) ни подстрочно, ни через
`issubset` множеств слов. Авторы БЕЗ дефиса в имени папки (`ALLA`,
`Dagonil`) багу не подвержены — оттого он долго не проявлялся широко.

ПРИМЕЧАНИЕ: сама `_normalize_name_for_comparison()` НЕ тронута (у неё
есть другие вызывающие места — фикс её глобально сломал бы отдельный,
не связанный 3-уровневый сценарий на реальных данных, см. golden-
снапшот и docs/quality-roadmap.md). Фикс — точечный, только внутри
сравнения `_is_author_variant`.
"""
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author><first-name>{first}</first-name><last-name>{last}</last-name></author>
<book-title>{title}</book-title>
</title-info>
</description>
<body>
<title><p>{title}</p></title>
<section><p>Текст.</p></section>
</body>
</FictionBook>
"""


def _write_book(dir_, filename, first, last, title):
    p = dir_ / filename
    p.write_text(_FB2.format(first=first, last=last, title=title), encoding="utf-8")
    return p


class TestHyphenatedAuthorFolderNotTreatedAsSeries:
    def test_author_folder_with_hyphenated_name_excluded_from_series(self, tmp_path):
        # Корень классифицируется как PUBLISHER штатным префиксом "серия - «".
        publisher_root = tmp_path / "Серия - «LitRPG»"
        author_dir = publisher_root / "Мантикор-Артемис (Артемис Мантикор)"
        series_dir = author_dir / "Мир Мельхиора"
        series_dir.mkdir(parents=True)
        _write_book(series_dir, "1. Ужасающий рай.fb2", "Артемис", "Мантикор", "Ужасающий рай")
        _write_book(series_dir, "2. Марш неудачников.fb2", "Артемис", "Мантикор", "Марш неудачников")

        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(publisher_root), output_csv_path=None)

        assert len(records) == 2
        for rec in records:
            assert rec.proposed_series == "Мир Мельхиора", rec.proposed_series
            assert "\\" not in rec.proposed_series

    def test_author_folder_without_hyphen_still_works(self, tmp_path):
        # Sanity: обычная папка автора (без дефисного паттерна) — не
        # регрессирует. "Мозолевский Павел" (пробел) — тоже не должно
        # ломаться, хотя этот случай и раньше был не подвержен багу.
        publisher_root = tmp_path / "Серия - «LitRPG»"
        author_dir = publisher_root / "Волков Тим"
        series_dir = author_dir / "Дуэлянт"
        series_dir.mkdir(parents=True)
        _write_book(series_dir, "1. Дуэлянт.fb2", "Тим", "Волков", "Дуэлянт")

        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(publisher_root), output_csv_path=None)

        assert len(records) == 1
        assert records[0].proposed_series == "Дуэлянт"
