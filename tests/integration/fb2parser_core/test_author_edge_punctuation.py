"""Имя автора со знаком на краю слова (сервер 2026-10-28: «Осинская Татьяна,
Эльба .Ирина» — точка из метаданных) → то же имя без знака, но только если
оно точно известно: есть у другой книги прогона (решение пользователя
2026-10-28). Иначе — как было («_N.B._»).
"""
from pathlib import Path

import pytest

from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

pytestmark = pytest.mark.django_db
NS = 'xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"'


def _book(path: Path, authors, title: str, seq: str = ""):
    path.parent.mkdir(parents=True, exist_ok=True)
    tags = "".join(f'<author><first-name>{f}</first-name><last-name>{last}</last-name></author>' for f, last in authors)
    path.write_text(
        f'<?xml version="1.0" encoding="utf-8"?><FictionBook {NS}><description><title-info><genre>sf</genre>'
        f'{tags}<book-title>{title}</book-title>{seq}<lang>ru</lang></title-info></description>'
        f'<body><section><p>текст</p></section></body></FictionBook>', encoding="utf-8")


def _authors(tmp_path):
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    return {Path(r.file_path).name: r.proposed_author for r in records}


def test_punctuated_name_takes_the_clean_spelling_from_the_same_batch(tmp_path):
    authors = [("Татьяна", "Осинская"), ("Ирина", "Эльба")]
    trilogy = "Ирина Эльба, Осинская Татьяна - Школа сказок (Трилогия).fb2"
    vol4 = "Осинская Татьяна, Эльба .Ирина - Школа сказок. Тень Кощеева т. 4.fb2"
    _book(tmp_path / "Фантастика" / "Осинская Татьяна, Эльба Ирина" / "Школа сказок" / trilogy, authors,
          "Школа сказок (Трилогия)")
    _book(tmp_path / "Фантастика" / "Осинская Татьяна, Эльба .Ирина" / "Школа сказок" / vol4, authors,
          "Тень Кощеева")
    found = _authors(tmp_path)
    assert found[vol4] == found[trilogy] == "Ирина Эльба, Осинская Татьяна"


def test_punctuated_name_without_a_clean_twin_is_left_as_is(tmp_path):
    _book(tmp_path / "Книга.fb2", [(".Ирина", "Эльба")], "Книга")
    assert ".Ирина" in _authors(tmp_path)["Книга.fb2"]
