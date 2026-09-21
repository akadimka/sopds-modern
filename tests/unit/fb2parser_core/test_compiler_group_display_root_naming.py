"""Регрессия для `FB2CompilerService.find_groups()`/`_group_series_for_naming()`
— docs/quality-roadmap.md, баг №109 (продолжение — display_root в имени).

Пользователь заметил на экране предпросмотра компиляции: группа "Михайлов
Руслан / Кроу" и итоговое имя "Михайлов Руслан - Кроу (т. 2-5).fb2" не
показывают, что "Кроу" — подсерия мира "Мир Вальдиры". Само поле
`proposed_series` намеренно плоское (баг №109, п.1 — иначе ломается
нумерация позиций), но человеку, читающему превью или итоговое имя файла,
эта информация нужна.

`BookRecord.series_display_root` уже несёт этот организационный корень
(используется synchronization.py для физического пути) — здесь он
дополнительно подхватывается в `CompilationGroup.display_root` и
подставляется ТОЛЬКО в текст для отображения/именования
(`_group_series_for_naming()` + `_series_to_display()`), не в сам
`group.series` — группировка/дедуп/поиск precompiled-диапазонов её не
видят.
"""
from pathlib import Path

from fb2parser_core.fb2_compiler import CompilationBook, FB2CompilerService
from fb2parser_core.passes.pass1_read_files import BookRecord

_FB2_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Руслан</first-name><last-name>Михайлов</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><title><p>{title}</p></title><section><p>{text}</p></section></body></FictionBook>
"""


def _write_book(dir_: Path, filename: str, title: str, unique_text: str) -> Path:
    dir_.mkdir(parents=True, exist_ok=True)
    p = dir_ / filename
    p.write_text(_FB2_TMPL.format(title=title, text=unique_text * 20), encoding="utf-8")
    return p


def _rec(file_path, title, series_number, unique_text, display_root):
    return BookRecord(
        file_path=file_path, file_title=title,
        metadata_authors="Руслан Михайлов", proposed_author="Михайлов Руслан",
        author_source="folder_dataset", metadata_series="", proposed_series="Кроу",
        series_source="folder_dataset", series_number=series_number,
        series_number_source="filename_prefix",
        series_display_root=display_root,
    )


class TestFindGroupsPropagatesDisplayRootFromRecords:
    def test_group_gets_display_root_from_books(self, tmp_path):
        kroy_dir = tmp_path / "Мир Вальдиры" / "Кроу (КРОУ)"
        recs = []
        for n, title in ((2, "Суровые земли"), (3, "Азы мастерства")):
            fname = f"Кроу {n}. {title}.fb2"
            _write_book(kroy_dir, fname, title, f"Уникальный текст Кроу {n}.")
            recs.append(_rec(
                f"Мир Вальдиры\\Кроу (КРОУ)\\{fname}", title, str(n),
                f"Уникальный текст Кроу {n}.", "Мир Вальдиры",
            ))

        groups = FB2CompilerService().find_groups(recs, tmp_path)
        matches = [g for g in groups if g.author == "Михайлов Руслан"]
        assert len(matches) == 1
        assert matches[0].display_root == "Мир Вальдиры"

    def test_group_without_display_root_stays_empty(self, tmp_path):
        # Sanity: обычная серия без организационного корня не меняется.
        series_dir = tmp_path / "Обычная серия"
        recs = []
        for n, title in ((1, "Начало"), (2, "Продолжение")):
            fname = f"{n}. {title}.fb2"
            _write_book(series_dir, fname, title, f"Уникальный текст {n}.")
            recs.append(_rec(
                f"Обычная серия\\{fname}", title, str(n),
                f"Уникальный текст {n}.", "",
            ))

        groups = FB2CompilerService().find_groups(recs, tmp_path)
        matches = [g for g in groups if g.author == "Михайлов Руслан"]
        assert len(matches) == 1
        assert matches[0].display_root == ""


class TestGroupSeriesForNamingIncludesDisplayRoot:
    def _group(self, series, display_root):
        book = CompilationBook(
            record=None, abs_path=Path("x.fb2"), sort_key=(0, 1, 0, 0),
            sort_source="series_number", order_ambiguous=False,
        )
        from fb2parser_core.fb2_compiler import CompilationGroup
        return CompilationGroup(
            author="Михайлов Руслан", series=series, books=[book],
            order_determined=True, volume_range="2-5", display_root=display_root,
        )

    def test_display_root_prepended_for_naming(self):
        group = self._group("Кроу", "Мир Вальдиры")
        named = FB2CompilerService._group_series_for_naming(group)
        assert named == "Мир Вальдиры\\Кроу"
        assert FB2CompilerService._series_to_display(named) == "Мир Вальдиры. Кроу"

    def test_no_display_root_naming_unchanged(self):
        group = self._group("Кроу", "")
        named = FB2CompilerService._group_series_for_naming(group)
        assert named == "Кроу"
