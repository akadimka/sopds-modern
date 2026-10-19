"""Номер подцикла — только из имени файла, не из метаданных другой серии
(баг №164).

`Серия - «Эксклюзивная классика»`: «Фоллетт. Столпы земли. Том 1.fb2» и
«… Том 2.fb2» — одна книга в двух томах; в метаданных у обоих
<sequence name="Кингсбридж" number="1"/> (первая книга цикла). Серия
«Столпы земли» — из имени файла, а номер 1 — из метаданных «Кингсбриджа».
`Pass2SeriesFilename._split_numbered_subseries` увидел у двух книг
одинаковый номер и разные «Том N» — счёл 1 номером подцикла («Война
великого бога 2. Внутренняя война. Том 1/2») и дописал его: «Столпы земли
1», сборник «Фоллетт Кен - Столпы земли 1 (Дилогия)». Номер подцикла
засчитывается, только если он стоит в имени файла сразу после названия
серии.
"""
from pathlib import Path

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.logger import Logger
from fb2parser_core.passes.pass1_read_files import BookRecord
from fb2parser_core.passes.pass2_series_filename import Pass2SeriesFilename
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><author><first-name>Кен</first-name><last-name>Фоллетт</last-name></author>
<book-title>{title}</book-title></title-info></description>
<body><section><p>Текст книги {title}.</p></section></body></FictionBook>
"""


def _rec(name, author, series, sn, meta_series, title):
    return BookRecord(
        file_path=f"Серия - «Эксклюзивная классика»/{name}", file_title=title,
        metadata_authors=author, proposed_author=author, author_source="filename",
        metadata_series=meta_series, proposed_series=series, series_source="filename",
        series_number=sn, series_number_source="metadata",
    )


def _follett():
    return [_rec(f"Фоллетт. Столпы земли. Том {n}.fb2", "Фоллетт Кен", "Столпы земли", "1", "Кингсбридж",
                 f"Столпы земли. Том {n}") for n in (1, 2)]


def _split(records):
    Pass2SeriesFilename(Logger(), config_path=_config_path())._split_numbered_subseries(records)
    return records


def test_number_from_another_series_metadata_is_not_a_subseries():
    assert {r.proposed_series for r in _split(_follett())} == {"Столпы земли"}


def test_subseries_number_in_file_name_still_splits():
    recs = _split([
        _rec("Беляев Иван - Война великого бога 1. Седьмая казнь.fb2", "Беляев Иван",
             "Война великого бога", "1", "Война великого бога", "Седьмая казнь"),
        _rec("Беляев Иван - Война великого бога 2. Внутренняя война. Том 1.fb2", "Беляев Иван",
             "Война великого бога", "2", "Война великого бога", "Внутренняя война. Том 1"),
        _rec("Беляев Иван - Война великого бога 2. Внутренняя война. Том 2.fb2", "Беляев Иван",
             "Война великого бога", "2", "Война великого бога", "Внутренняя война. Том 2"),
    ])
    assert [r.proposed_series for r in recs] == ["Война великого бога 1", "Война великого бога 2",
                                                 "Война великого бога 2"]


def test_two_volumes_compile_as_one_dilogy(tmp_path):
    recs = _split(_follett())
    folder = tmp_path / "Серия - «Эксклюзивная классика»"
    folder.mkdir()
    for r in recs:
        (tmp_path / r.file_path).write_text(_FB2.format(title=r.file_title), encoding="utf-8")
    svc = FB2CompilerService()
    [g] = svc.find_groups(recs, tmp_path)
    assert [Path(b.abs_path).name for b in g.books] == [
        "Фоллетт. Столпы земли. Том 1.fb2", "Фоллетт. Столпы земли. Том 2.fb2"]
    assert not g.duplicate_paths and not g.excluded_paths
    assert svc._series_to_display(svc._clean_series_name(svc._group_series_for_naming(g))) == "Столпы земли"
    assert svc.compute_group_suffix(g)[0] == "Дилогия"
