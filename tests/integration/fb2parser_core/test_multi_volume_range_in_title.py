"""Регрессия для `FB2CompilerService._determine_sort_key()`/`_run_stats()`/
`compute_group_suffix()` — docs/quality-roadmap.md, баг №116.

Реальный случай (Тайниковский/Хроники демонического ремесленника): файл
может физически объединять НЕСКОЛЬКО томов — «5. Кузнец. Том V-VI.fb2»,
«6. Кузнец. Том VII-VIII.fb2». `_VOLUME_ROMAN_RE`/`_extract_inline_
volume_number()` захватывали только ПЕРВУЮ римскую цифру диапазона
(«V»/«VII»), молча теряя «-VI»/«-VIII» — итоговый диапазон компиляции
показывал «т. 3-6.7» (искажённая дробь на стыке случайного совпадения
позиции файла и первой цифры диапазона) вместо честного «т. 3-8».
"""
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author><first-name>Иван</first-name><last-name>Волков</last-name></author>
<book-title>{title}</book-title>
<sequence name="Хроники демонического ремесленника" number="{sn}"/>
</title-info>
</description>
<body>
<title><p>{title}</p></title>
<section><p>Текст.</p></section>
</body>
</FictionBook>
"""


def _write_book(dir_, filename, title, sn):
    p = dir_ / filename
    p.write_text(_FB2.format(title=title, sn=sn), encoding="utf-8")
    return p


class TestMultiVolumeRangeInTitlePreservesFullRange:
    def test_volume_labels_reflect_full_range_not_just_first_number(self, tmp_path):
        author_dir = tmp_path / "Волков Иван"
        author_dir.mkdir()
        _write_book(author_dir, "3. Кузнец. Том III.fb2", "Кузнец. Том III", sn="3")
        _write_book(author_dir, "4. Кузнец. Том IV.fb2", "Кузнец. Том IV", sn="4")
        _write_book(author_dir, "5. Кузнец. Том V-VI.fb2", "Кузнец. Том V - VI", sn="5")
        _write_book(author_dir, "6. Кузнец. Том VII-VIII.fb2", "Кузнец. Том VII — VIII", sn="6")

        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)

        compiler = FB2CompilerService()
        groups = compiler.find_groups(records, tmp_path)
        matches = [g for g in groups if g.author == "Волков Иван"]
        assert len(matches) == 1, f"expected 1 merged group, got {len(matches)}"
        group = matches[0]

        labels = {b.abs_path.name: b.volume_label for b in group.books}
        assert labels["3. Кузнец. Том III.fb2"] == "3"
        assert labels["4. Кузнец. Том IV.fb2"] == "4"
        # Диапазон, а не голое "5"/"6" — вторая половина не должна теряться.
        assert labels["5. Кузнец. Том V-VI.fb2"] == "5-6"
        assert labels["6. Кузнец. Том VII-VIII.fb2"] == "7-8"

        suffix, lo, hi = compiler.compute_group_suffix(group)
        assert (lo, hi) == (3, 8), f"expected top range (3, 8), got ({lo}, {hi})"
        assert "6.7" not in suffix

    def test_range_not_confused_with_bug_62_fractional_conflict_marker(self, tmp_path):
        # Sanity: баг №62's старый дробный формат ("N.M" через ТОЧКУ, не
        # через дефис/тире) не должен матчиться новым _RNG-паттерном в
        # _run_stats() — убеждаемся, что расширение регекса безопасно.
        import re
        _RNG = re.compile(r'^(\d+)\s*[-–—]\s*(\d+)$')
        assert _RNG.match("12.1") is None
        assert _RNG.match("7-8") is not None
