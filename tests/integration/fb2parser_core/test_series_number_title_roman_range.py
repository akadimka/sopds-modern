"""Регрессия для `Pass2SeriesFilename._correct_series_number_from_filename()`
(Правило 8) — docs/quality-roadmap.md, баг №117.

Реальный случай (Тайниковский/Хроники демонического ремесленника): файл
может физически объединять НЕСКОЛЬКО томов — «5. Кузнец. Том V-VI.fb2»,
«6. Кузнец. Том VII-VIII.fb2». Правило 1 (голая позиция файла в папке)
ставило series_number='5'/'6' — вторая половина диапазона, видная только
из заголовка книги, терялась. Компиляция (fb2_compiler.py, баг №116) уже
показывала честный "5-6"/"7-8" через volume_label, но само поле
series_number в CSV регена оставалось голой позицией файла.
"""
from fb2parser_core.regen_csv import RegenCSVService
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


class TestSeriesNumberReflectsTitleRomanRange:
    def test_series_number_upgraded_to_title_roman_range(self, tmp_path):
        author_dir = tmp_path / "Волков Иван"
        author_dir.mkdir()
        _write_book(author_dir, "3. Кузнец. Том III.fb2", "Кузнец. Том III", sn="3")
        _write_book(author_dir, "5. Кузнец. Том V-VI.fb2", "Кузнец. Том V - VI", sn="5")
        _write_book(author_dir, "6. Кузнец. Том VII-VIII.fb2", "Кузнец. Том VII — VIII", sn="6")

        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)
        by_name = {r.file_path.split("\\")[-1]: r for r in records}

        # Одиночный том — без диапазона в заголовке, не трогаем.
        assert by_name["3. Кузнец. Том III.fb2"].series_number == "3"
        assert by_name["3. Кузнец. Том III.fb2"].series_number_source == "filename_prefix"

        # Диапазон из заголовка, а не голая позиция файла в папке.
        r5 = by_name["5. Кузнец. Том V-VI.fb2"]
        assert r5.series_number == "5-6"
        assert r5.series_number_source == "filename_prefix_title_roman_range"

        # Позиция файла (6) и реальный номер тома (7-8) расходятся —
        # берём диапазон из заголовка, не пытаемся согласовать с позицией.
        r6 = by_name["6. Кузнец. Том VII-VIII.fb2"]
        assert r6.series_number == "7-8"
        assert r6.series_number_source == "filename_prefix_title_roman_range"

    def test_more_specific_source_not_overridden(self, tmp_path):
        # Диапазон в скобках (Правило 3) надёжнее позиции файла — не должен
        # перезаписываться Правилом 8, даже если в заголовке тоже есть "Том".
        author_dir = tmp_path / "Волков Иван"
        author_dir.mkdir()
        _write_book(
            author_dir,
            "1. Кузнец (т. 1-2). Том I-II.fb2",
            "Кузнец. Том I - II",
            sn="1",
        )
        service = RegenCSVService(_config_path())
        records = service.generate_csv(str(tmp_path), output_csv_path=None)
        rec = records[0]
        assert rec.series_number == "1-2"
        assert rec.series_number_source == "filename_bracket_range"
