"""Латинские имена и служебные папки коллекции — docs/quality-roadmap.md,
баги №176 и №177 (решения пользователя 2026-10-20).

№176: латинские имена из двух слов не переставляются («Ascold Flow»
оставался «Flow Ascold», «Get Lost» — «Lost Get», «Some people» — «People
Some»).

№177: «_Межавторские циклы_/Dragon Age/1. Украденный трон (Гейдер-Дэвид).fb2»
— служебная папка была автором («Циклы_ _Межавторские») у 14 книг. Решение
(вариант А): автор у каждой книги свой, серия — папка цикла; сборник
12 авторов — «Соавторство», а не первый из них.
"""
from fb2parser_core.passes.pass3_normalize import is_latin_name
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
{authors}
<book-title>{title}</book-title>
</title-info>
</description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""

_COLLECTION = ("СЕРИЯ.LitRPG", "Hеофициальная серия книг (разные издательства)")


def _person(first, last):
    return f"<author><first-name>{first}</first-name><last-name>{last}</last-name></author>"


def _write(root, *parts, authors):
    path = root.joinpath(*_COLLECTION, *parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FB2.format(authors=authors, title=path.stem), encoding="utf-8")


def _records(tmp_path):
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    return {r.file_path.replace("\\", "/").rsplit("/", 1)[1]: r for r in records}


def test_latin_names_keep_their_order(tmp_path):
    _write(tmp_path, "Ascold Flow", "Сломанный мир", "1. Ад.fb2", authors=_person("Ascold", "Flow"))
    _write(tmp_path, "Get Lost", "Путь", "1. Начало.fb2", authors="<author><nickname>Get Lost</nickname></author>")
    _write(tmp_path, "Semyon2 (Semyon Two)", "Эфир", "01. Введение.fb2",
           authors="<author><nickname>Semyon2</nickname></author>")
    recs = _records(tmp_path)
    assert recs["1. Ад.fb2"].proposed_author == "Ascold Flow"
    assert recs["1. Начало.fb2"].proposed_author == "Get Lost"
    assert recs["01. Введение.fb2"].proposed_author == "Semyon Two"


def test_is_latin_name():
    assert is_latin_name("Ascold Flow") and is_latin_name("L. M. Kerr") and is_latin_name("Toika, Toy Car")
    assert not is_latin_name("Toika") and not is_latin_name("Кавахара Рэки") and not is_latin_name("Fuse, Вах")


def test_inter_author_cycles_folder_is_not_an_author(tmp_path):
    base = ("_Межавторские циклы_", "Dragon Age")
    _write(tmp_path, *base, "1. Украденный трон (Гейдер-Дэвид).fb2", authors=_person("Дэвид", "Гейдер"))
    _write(tmp_path, *base, "2. Призыв (Гейдер-Дэвид).fb2", authors=_person("Дэвид", "Гейдер"))
    _write(tmp_path, *base, "4. Империя масок (Уикс-Патрик).fb2", authors=_person("Патрик", "Уикс"))
    names = ("Райан Кормье", "Сильвия Фекетекути", "Джон Эплер", "Патрик Уикс")
    _write(tmp_path, *base, "6. Тевинтерские ночи.fb2",
           authors="".join(_person(*n.split()) for n in names))
    recs = _records(tmp_path)
    assert recs["1. Украденный трон (Гейдер-Дэвид).fb2"].proposed_author == "Гейдер Дэвид"
    assert recs["4. Империя масок (Уикс-Патрик).fb2"].proposed_author == "Уикс Патрик"
    assert recs["6. Тевинтерские ночи.fb2"].proposed_author == "Соавторство"
    assert {r.proposed_series for r in recs.values()} == {"Dragon Age"}
