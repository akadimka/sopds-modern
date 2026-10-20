"""Автор из папки «Фамилия-Имя» — docs/quality-roadmap.md, баг №169.

В «СЕРИЯ.LitRPG» папки авторов названы «Фамилия-Имя». Дефис убирался,
только если второе слово есть в словаре имён («Кириллов-Сергей» →
«Кириллов Сергей»); 146 авторов (473 книги) оставались «Кавахара-Рэки»,
«Ютин-Макар», «Шер-Вениамин». Теперь дефис убирается, если метаданные
называют те же два слова раздельно.
"""
from fb2parser_core.regen_csv import RegenCSVService
from fb2parser_web.fb2parser_bridge import _config_path

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description>
<title-info>
<author>{author}</author>
<book-title>{title}</book-title>
</title-info>
</description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""

_COLLECTION = ("СЕРИЯ.LitRPG", "Hеофициальная серия книг (разные издательства)")


def _write(root, folder, series, filename, author_xml):
    d = root.joinpath(*_COLLECTION, folder, series)
    d.mkdir(parents=True, exist_ok=True)
    (d / filename).write_text(_FB2.format(author=author_xml, title=filename[4:-4]), encoding="utf-8")


def _authors(tmp_path):
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    return {r.file_path.replace("\\", "/").split("/")[2]: r.proposed_author for r in records}


def _name(first, last):
    return f"<first-name>{first}</first-name><last-name>{last}</last-name>"


def test_hyphen_split_when_metadata_names_both_words(tmp_path):
    _write(tmp_path, "Кавахара-Рэки", "Sword Art Online", "01. Айнкрад.fb2", _name("Рэки", "Кавахара"))
    _write(tmp_path, "Ютин-Макар", "Tower Defense", "01. Том 1.fb2", _name("Макар", "Ютин"))
    _write(tmp_path, "Кириллов-Сергей", "Дорога", "01. Дорога.fb2", "<nickname>NonSemper</nickname>")
    authors = _authors(tmp_path)
    assert authors["Кавахара-Рэки"] == "Кавахара Рэки"
    assert authors["Ютин-Макар"] == "Ютин Макар"
    assert authors["Кириллов-Сергей"] == "Кириллов Сергей"      # как раньше — по словарю имён


def test_one_confirming_book_is_enough_for_the_author(tmp_path):
    """Татуков-Карим: 8 книг под ником «loloking333», одна — «Карим Татуков»."""
    _write(tmp_path, "Татуков-Карим", "Эхо разрушений", "01. Этап семи небес.fb2", "<nickname>loloking333</nickname>")
    _write(tmp_path, "Татуков-Карим", "Эхо разрушений", "02. Закат царства.fb2", _name("Карим", "Татуков"))
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    assert {r.proposed_author for r in records} == {"Татуков Карим"}


def test_illustrator_from_metadata_not_added_as_coauthor(tmp_path):
    """Анеко Юсаги: в метаданных всех томов — ещё и иллюстратор Минами Сэйра."""
    meta = _name("Анеко", "Юсаги") + "</author><author>" + _name("Минами", "Сэйра")
    for n in (1, 2, 3):
        _write(tmp_path, "Анеко-Юсаги", "Восхождение героя щита", f"0{n}. Том {n}.fb2", meta)
    records = RegenCSVService(_config_path()).generate_csv(str(tmp_path), output_csv_path=None)
    assert {r.proposed_author for r in records} == {"Анеко Юсаги"}


def test_real_hyphenated_names_and_unconfirmed_kept(tmp_path):
    _write(tmp_path, "Wing-Span", "Небо", "01. Небо.fb2", "<nickname>Wing-Span</nickname>")
    _write(tmp_path, "Chu-Gong", "Solo Leveling", "01. Том 1.fb2", "<nickname>추공</nickname>")
    authors = _authors(tmp_path)
    assert authors["Wing-Span"] == "Wing-Span"
    assert authors["Chu-Gong"] == "Chu-Gong"
