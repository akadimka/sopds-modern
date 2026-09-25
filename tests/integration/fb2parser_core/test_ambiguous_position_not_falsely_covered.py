"""Регрессия для `FB2CompilerService._determine_sort_key()` — docs/quality-
roadmap.md, баг №118.

Реальный случай (Сухов Лео - Сборник\\Тьма\\): «Тьма. Том 1 и 2.fb2» —
физическое издание, объединяющее тома 1 и 2 (предкомпиляция, диапазон
1-2). «Тьма. Том 3.fb2» — отдельный, уникальный том 3, но его FB2
metadata `<sequence name="Тьма [Сухов]" number="2">` считает ИЗДАННЫЕ
КНИГИ, а не ТОМА («Том 1 и 2» уже книга №1, поэтому «Том 3» получает
meta_num=2) — конфликтует с честным заголовком («Том 3» → 3). Название
серии в metadata ("Тьма [Сухов]") содержит лишнее слово по сравнению с
proposed_series ("Тьма") — это делает `_series_ok=False`, направляя
запись в "Источник Б" (`_series_ok_val=False`), ветку, изначально
написанную для случая «12. Сфера Богов том 1.fb2» (meta_num=12,
ПОДТВЕРЖДЁННЫЙ ведущим числовым префиксом имени файла, roman_inline=1 —
номер ВНУТРЕННЕГО тома той же позиции). Но код никогда не проверял,
действительно ли meta_num подтверждён в имени файла — просто брал
metadata sn как есть. Для «Тьма. Том 3.fb2» meta_num=2 нигде в имени
файла не встречается — код всё равно доверял ему как «внешней позиции»,
из-за чего find_groups() считал позицию 2 уже покрытой диапазоном «1-2»
и помечал уникальный том 3 на удаление (duplicate_paths) — риск потери
данных при подтверждении в UI.
"""
from pathlib import Path

from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author><first-name>Иван</first-name><last-name>Волков</last-name></author>
<book-title>{title}</book-title>
{sequence}
</title-info>
</description>
<body>
<title><p>{title}</p></title>
<section><p>Текст.</p></section>
</body>
</FictionBook>
"""


def _write_book(dir_, filename, title, seq_name=None, sn=None):
    seq = f'<sequence name="{seq_name}" number="{sn}"/>' if sn is not None else ""
    p = dir_ / filename
    p.write_text(_FB2.format(title=title, sequence=seq), encoding="utf-8")
    return p


class TestAmbiguousPositionNotFalselyMarkedDuplicate:
    def test_conflicting_meta_num_not_treated_as_covered_by_precompiled_range(self, tmp_path):
        author_dir = tmp_path / "Волков Иван"
        author_dir.mkdir()
        # Издание, объединяющее тома 1 и 2 в один файл.
        _write_book(author_dir, "Тьма. Том 1 и 2.fb2", "Тьма. Том 1 и 2")
        # Отдельный том 3 — metadata series_name с лишним словом
        # ("[Волков]", как в реальных данных "Тьма [Сухов]") даёт
        # _series_ok=False → "Источник Б". Metadata number (счётчик
        # КНИГ) говорит "2", заголовок честно говорит "3" — конфликт,
        # и "2" нигде не встречается в имени файла.
        _write_book(
            author_dir, "Тьма. Том 3.fb2", "Тьма. Том 3",
            seq_name="Тьма [Волков]", sn="2",
        )
        # Том 4 — тот же конфликт, но его позиция (3, из заголовка) не
        # пересекается с диапазоном предкомпиляции (1-2), поэтому и
        # раньше не удалялся — сохраняем как sanity-проверку.
        _write_book(
            author_dir, "Тьма. Том 4.fb2", "Тьма. Том 4",
            seq_name="Тьма [Волков]", sn="3",
        )

        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)

        rec3 = next(r for r in records if r.file_path.endswith("Том 3.fb2"))
        assert rec3.metadata_series == "Тьма [Волков]"
        assert rec3.proposed_series == "Тьма"

        compiler = FB2CompilerService()
        groups = compiler.find_groups(records, tmp_path)
        matches = [g for g in groups if g.author == "Волков Иван"]
        assert len(matches) == 1, f"expected 1 group, got {len(matches)}"
        group = matches[0]

        dup_names = {p.name for p in (group.duplicate_paths or [])}
        assert "Тьма. Том 3.fb2" not in dup_names, (
            "Том 3 — уникальная книга со спорной (не покрытой) позицией, "
            "не должна молча помечаться на удаление"
        )

        kept_names = {b.abs_path.name for b in group.books}
        assert "Тьма. Том 3.fb2" in kept_names
        assert "Тьма. Том 1 и 2.fb2" in kept_names
        assert "Тьма. Том 4.fb2" in kept_names

    def test_legitimate_inner_tom_confirmed_by_filename_prefix_unaffected(self, tmp_path):
        # Sanity: «12. Сфера Богов том 1.fb2» — meta_num=12 ДЕЙСТВИТЕЛЬНО
        # подтверждён ведущим префиксом имени файла — эта, легитимная,
        # ветка (meta_num = внешняя позиция, roman_inline = внутренний
        # том) не должна измениться фиксом бага №118.
        author_dir = tmp_path / "Волков Иван"
        author_dir.mkdir()
        _write_book(
            author_dir, "12. Сфера Богов том 1.fb2", "Сфера Богов том 1",
            seq_name="Сфера Богов [Волков]", sn="12",
        )
        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)
        rec = records[0]
        compiler = FB2CompilerService()
        sort_key, source, ambiguous, label = compiler._determine_sort_key(
            rec, Path(rec.file_path)
        )
        assert sort_key == (0, 12, 1, 0)
        assert source == "series_number_inner_tom"
        assert label == "12.1"
