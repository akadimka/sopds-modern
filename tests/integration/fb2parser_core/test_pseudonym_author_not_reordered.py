"""Русские псевдонимы из LitRPG не переставляются как «Фамилия Имя» (баг №158).

Аудит «СЕРИЯ.LitRPG» (docs/litrpg-audit.md): «Оро Призывающий» →
«Призывающий Оро», «Северный Лис» → «Лис Северный»… — перестановка в
«Фамилия Имя» нужна настоящим именам, псевдонимам — нет.

Автоматический признак («ни одного слова из словарей имён») проверен на
библиотеке и отвергнут: под него попали 1 186 папок авторов — в основном
иностранные имена кириллицей («Гоббс Томас», «Кант Иммануил»), которых в
русских словарях нет. Решение пользователя 2026-10-16: явный список в
`author_surname_conversions` (app_settings.json) — 28 русских псевдонимов
LitRPG; латинские псевдонимы не трогаем.
"""
import pytest

from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>sf</genre><author>{author}</author>
<book-title>{title}</book-title></title-info></description>
<body><section><p>Текст.</p></section></body></FictionBook>
"""
ROOT = ("Серия - «LitRPG»", "Hеофициальная серия книг (разные издательства)")
AUTHORS = {
    "Оро Призывающий": "<first-name>Оро</first-name><last-name>Призывающий</last-name>",
    "Северный Лис": "<first-name>Северный</first-name><last-name>Лис</last-name>",
    "Кириллов-Сергей": "<first-name>Сергей</first-name><last-name>Кириллов</last-name>",
    # не псевдонимы, хотя слов нет в (русских) словарях имён — как раньше:
    "Leon Hart": "<first-name>Leon</first-name><last-name>Hart</last-name>",
}


@pytest.fixture(scope="module")
def authors(tmp_path_factory):
    root = tmp_path_factory.mktemp("pseudo").joinpath(*ROOT)
    for folder, author in AUTHORS.items():
        d = root / folder / "Мир"
        d.mkdir(parents=True)
        for n in (1, 2):
            (d / f"{n}. Книга {n}.fb2").write_text(_FB2.format(author=author, title=f"Книга {n}"),
                                                    encoding="utf-8")
    out = root.parent / "regen.csv"
    records = RegenCSVService(_config_path()).generate_csv(str(root), output_csv_path=str(out))
    by_folder = {}
    for r in records:
        folder = next(f for f in AUTHORS if f in r.file_path)
        by_folder.setdefault(folder, set()).add(r.proposed_author)
    return by_folder


def test_listed_pseudonym_keeps_folder_order(authors):
    assert authors["Оро Призывающий"] == {"Оро Призывающий"}
    assert authors["Северный Лис"] == {"Северный Лис"}


def test_real_name_still_surname_first(authors):
    assert authors["Кириллов-Сергей"] == {"Кириллов Сергей"}


def test_latin_names_not_reordered(authors):
    # Баг №176 (решение пользователя 2026-10-20): латинские имена не
    # переставляются вообще — раньше здесь ожидалось «Hart Leon».
    assert authors["Leon Hart"] == {"Leon Hart"}
