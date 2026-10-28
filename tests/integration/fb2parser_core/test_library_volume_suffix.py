"""Наш суффикс тома «… т. N» в имени файла библиотеки (synchronization пишет
его книге с номером) — номер, не часть серии. Сервер 2026-10-28: «Орунья
Мария - Когда мы были непобедимы т. 3.fb2» (книга без серии в папке автора)
при повторном разборе получала серию «Когда мы были непобедимы т».
"""
from pathlib import Path

import pytest

from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

pytestmark = pytest.mark.django_db
NS = 'xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"'


def _book(path: Path, title: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<?xml version="1.0" encoding="utf-8"?><FictionBook {NS}><description><title-info><genre>det</genre>'
        f'<author><first-name>Мария</first-name><last-name>Орунья</last-name></author>'
        f'<book-title>{title}</book-title><lang>ru</lang></title-info></description>'
        f'<body><section><p>текст</p></section></body></FictionBook>', encoding="utf-8")


def test_volume_suffix_is_not_part_of_the_series(tmp_path):
    author = tmp_path / "Приключения" / "Орунья Мария"
    _book(author / "Орунья Мария - Когда мы были непобедимы т. 3.fb2", "Когда мы были непобедимы")
    _book(author / "Орунья Мария - Тайна. Книга о море т. 2.fb2", "Книга о море")
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    series = {Path(r.file_path).name: r.proposed_series for r in records}
    assert series["Орунья Мария - Когда мы были непобедимы т. 3.fb2"] == ""
    # «Серия. Название т. N»: корень — серия; без правки было «Тайна\Книга о море т»
    named = series["Орунья Мария - Тайна. Книга о море т. 2.fb2"]
    assert named.split("\\")[0] == "Тайна" and not named.endswith(" т")
