"""Номер тома только в названии книги (docs/series-duplicates.md; сервер
2026-10-27: «Холгер Мунк и Миа Крюгер #4 - Белая как снег» лежала рядом с
трилогией серии — ни имя файла, ни <sequence> номера не дают, и том не
попадал в компиляцию). RegenCSVService._postcheck_series_number_from_title
берёт число, стоящее вплотную после названия уже найденной серии; число в
начале названия или после фамилии — нет («1984», «451 градус…»).
"""
from pathlib import Path

import pytest

from fb2parser_core.fb2_compiler import FB2CompilerService
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path
from tests.integration.fb2parser_core.test_third_party_omnibus import _text

pytestmark = pytest.mark.django_db
NS = 'xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"'
SERIES = "Холгер Мунк и Миа Крюгер"


def _book(path: Path, title: str, volume: int, number: str = ""):
    seq = f'<sequence name="{SERIES}" number="{number}"/>' if number else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<?xml version="1.0" encoding="utf-8"?><FictionBook {NS}><description><title-info><genre>det</genre>'
        f'<author><first-name>Самюэль</first-name><last-name>Бьорк</last-name></author>'
        f'<book-title>{title}</book-title>{seq}<lang>ru</lang></title-info></description>'
        f'<body><section><p>{_text(volume)}</p></section></body></FictionBook>', encoding="utf-8")


def test_volume_number_from_title_joins_the_series(tmp_path):
    folder = tmp_path / "Детектив" / "Бьорк Самюэль" / SERIES
    for n, title in ((1, "Я путешествую одна"), (2, "Совенок"), (3, "Мальчик в свете фар")):
        _book(folder / f"Бьорк Самюэль - {SERIES} {n}. {title}.fb2", title, n, str(n))
    _book(folder / f"Бьорк Самюэль - {SERIES}. Белая как снег.fb2", f"{SERIES} #4 - Белая как снег", 4)
    _book(folder / f"Бьорк Самюэль - {SERIES}. Метро.fb2", "Метро 2033", 5)
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    numbers = {Path(r.file_path).name: r.series_number for r in records}
    assert numbers[f"Бьорк Самюэль - {SERIES}. Белая как снег.fb2"] == "4"
    assert numbers[f"Бьорк Самюэль - {SERIES}. Метро.fb2"] != "2033"
    [group] = [g for g in FB2CompilerService().find_groups(records, tmp_path) if g.series == SERIES]
    assert sorted(b.volume_label for b in group.books if b.volume_label.isdigit())[:4] == ["1", "2", "3", "4"]
