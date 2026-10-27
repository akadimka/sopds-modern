"""Отчёт «Дубли серий» (opds_catalog/series_check.py, docs/series-duplicates.md):
что лежит рядом с нашей компиляцией серии у того же автора и что с этим
предлагается сделать. Только чтение — проверяется и то, что файлы и
каталог не меняются."""
import os

import pytest
from django.core.management import call_command

from fb2parser_core.fb2_compiler import FB2CompilerService
from opds_catalog import series_check
from opds_catalog.models import Author, Book, Catalog, Series, bauthor, bseries

pytestmark = pytest.mark.django_db
LONG = "слово " * 15000                      # ~90 тыс. знаков — полноценная книга, не рассказ


def _fb2(title, text=LONG):
    return ('<?xml version="1.0" encoding="utf-8"?><FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
            f'<description><title-info><book-title>{title}</book-title></title-info></description>'
            f'<body><section><p>{text}</p></section></body></FictionBook>')


@pytest.fixture
def library(tmp_path):
    cat = Catalog.objects.create(cat_name=".", path=".")
    series = Series.objects.create(ser="Хроники", search_ser="ХРОНИКИ", lang_code=1)
    author = Author.objects.create(full_name="Петров Иван", search_full_name="ПЕТРОВ ИВАН", lang_code=1)

    def book(genre, filename, title, xml, ser_no=0, in_series=True):
        folder = os.path.join(genre, "Петров Иван", "Хроники")
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
        (tmp_path / folder / filename).write_text(xml, encoding="utf-8")
        b = Book.objects.create(filename=filename, path=folder, catalog=cat, avail=2, format="fb2", docdate="",
                                lang="ru", title=title, search_title=title.upper(), annotation="", lang_code=1)
        bauthor.objects.create(book=b, author=author)
        if in_series:
            bseries.objects.create(book=b, ser=series, ser_no=ser_no)
        return b

    comp_xml = FB2CompilerService()._build_fb2(
        "Петров Иван", "Хроники", "Тетралогия", "sf",
        [(t, f"<body><section><p>{LONG}</p></section></body>") for t in ("Начало", "Середина", "Финал")])
    book("Фантастика", "Петров Иван - Хроники (Тетралогия).fb2", "Хроники (Тетралогия)", comp_xml)
    book("Фантастика", "Петров Иван - Хроники. Середина.fb2", "Середина", _fb2("Середина"))
    book("Фантастика", "Хроники 1-5.fb2", "Ник [Иван Петров]. Хроники 1-5", _fb2("Хроники 1-5", LONG * 5))
    book("Фантастика", "Петров Иван - Хроники. Иное издание т. 2.fb2", "Иное издание", _fb2("Иное"), ser_no=2)
    book("Фантастика", "Петров Иван - Хроники. Продолжение т. 5.fb2", "Продолжение", _fb2("Продолжение"), ser_no=5)
    book("Фантастика", "Петров Иван - Хроники. Рассказ.fb2", "Ночной рассказ", _fb2("Рассказ", "коротко"))
    book("Фантастика", "Петров Иван - Хроники. Предыстория т. 0.fb2", "Предыстория", _fb2("Предыстория"))
    book("Фантастика", "Петров Иван - Хроники. Кн. 7.fb2", "Хроники. Кн. 7", _fb2("Кн. 7"))
    book("Фантастика", "Петров Иван - Хроники. Без номера.fb2", "Без номера", _fb2("Без номера"))
    book("Детектив", "Петров Иван - Хроники. Где-то.fb2", "Где-то", _fb2("Где-то"))
    return tmp_path


def test_each_kind_of_extra_book(library):
    report = series_check.check(str(library))
    [row] = report["series"]
    assert (row["author"], row["series"], row["genre"]) == ("Петров Иван", "Хроники", "Фантастика")
    assert row["compilation"]["volumes"] == ["Начало", "Середина", "Финал"]
    assert row["issues"] == ["suffix_mismatch"]                     # «Тетралогия» — а томов 3
    kinds = {e["title"]: e["kind"] for e in row["extras"]}
    assert kinds == {
        "Середина": "in_compilation",
        "Ник [Иван Петров]. Хроники 1-5": "omnibus",
        "Иное издание": "probable_duplicate",
        "Продолжение": "beyond",
        "Ночной рассказ": "small",
        "Предыстория": "outside_numbering",
        "Хроники. Кн. 7": "number_in_title",
        "Без номера": "unnumbered",
        "Где-то": "other_genre",
    }
    assert report["totals"]["extra_books"] == 9 and report["kind_counts"]["omnibus"] == 1


def test_genre_split_and_pseudonym(library):
    report = series_check.check(str(library))
    [split] = report["genre_splits"]
    assert split["genres"] == [("Фантастика", 9), ("Детектив", 1)] and split["majority"] == "Фантастика"
    [pair] = report["pseudonyms"]
    assert (pair["alias"], pair["name"], pair["folders"]) == ("Ник", "Иван Петров", ["Петров Иван"])


def test_series_without_extras_is_not_listed(library):
    Book.objects.exclude(title="Хроники (Тетралогия)").update(avail=0)       # остальных нет в каталоге
    assert series_check.check(str(library))["series"] == []


def test_command_is_read_only(library, tmp_path_factory, override_config, capsys):
    before = sorted((p.relative_to(library), p.stat().st_mtime) for p in library.rglob("*") if p.is_file())
    books = Book.objects.count()
    out_csv = tmp_path_factory.mktemp("out") / "report.csv"
    with override_config(SOPDS_ROOT_LIB=str(library)):
        call_command("library_series_check", "--csv", str(out_csv))
    out = capsys.readouterr().out
    assert "extra books: 9" in out and "Report only — nothing was changed." in out
    assert "Ник = Иван Петров" in out
    assert out_csv.read_text(encoding="utf-8").count("\n") == 10           # заголовок + 9 книг
    assert sorted((p.relative_to(library), p.stat().st_mtime) for p in library.rglob("*") if p.is_file()) == before
    assert Book.objects.count() == books
