"""Каталог OPDS повторяет библиотеку: книги, которых больше нет на диске,
удаляются из базы (SOPDS_DELETE_LOGICAL=False) — но не когда отвалилась
вся библиотека (баг №160).

Сервер 2026-10-16: 22 516 записей при 18 466 доступных — 4 050 «мёртвых»
(логическое удаление: тома, заменённые сборниками, удалённые дубли,
переименования). С физическим удалением недоступный диск/сетевая папка
стёрли бы весь каталог со всеми рейтингами — `opdsdb.deletion_guard`.
"""
import shutil

import pytest

from opds_catalog import opdsdb
from opds_catalog.models import Book, SamlibRating
from opds_catalog.sopdscan import opdsScanner

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>sf</genre>
<author><first-name>Иван</first-name><last-name>Иванов</last-name></author>
<book-title>{title}</book-title><lang>ru</lang></title-info></description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""


def _library(root, n):
    folder = root / "Фантастика" / "Иванов Иван"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (folder / f"Книга {i}.fb2").write_text(_FB2.format(title=f"Книга {i}"), encoding="utf-8")
    return folder


@pytest.mark.django_db
def test_missing_and_dead_books_are_removed(tmp_path, override_config):
    opdsdb.clear_all()
    folder = _library(tmp_path, 4)
    with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
        opdsScanner().scan_all()                       # логическое удаление (по умолчанию)
        (folder / "Книга 0.fb2").unlink()
        opdsScanner().scan_all()
        assert Book.objects.filter(avail=0).count() == 1   # «мёртвая» запись осталась

        (folder / "Книга 1.fb2").unlink()
        with override_config(SOPDS_DELETE_LOGICAL=False):
            scanner = opdsScanner()
            scanner.scan_all()

    assert sorted(Book.objects.values_list("filename", flat=True)) == ["Книга 2.fb2", "Книга 3.fb2"]
    assert scanner.books_deleted


@pytest.mark.django_db
def test_unavailable_library_deletes_nothing(tmp_path, override_config):
    opdsdb.clear_all()
    lib = tmp_path / "lib"
    _library(lib, 3)
    with override_config(SOPDS_ROOT_LIB=str(lib), SOPDS_DELETE_LOGICAL=False):
        opdsScanner().scan_all()
        SamlibRating.objects.create(book=Book.objects.first(), rating=7.0)
        shutil.rmtree(lib)                             # «диск не смонтирован»
        opdsScanner().scan_all()

    assert Book.objects.count() == 3 and SamlibRating.objects.count() == 1


@pytest.mark.django_db
def test_most_of_library_missing_deletes_nothing(tmp_path, override_config, monkeypatch):
    opdsdb.clear_all()
    monkeypatch.setattr(opdsdb, "DELETION_GUARD_MIN_BOOKS", 2)
    folder = _library(tmp_path, 6)
    with override_config(SOPDS_ROOT_LIB=str(tmp_path), SOPDS_DELETE_LOGICAL=False):
        opdsScanner().scan_all()
        for i in range(4):                             # пропали 4 из 6 разом
            (folder / f"Книга {i}.fb2").unlink()
        opdsScanner().scan_all()
        assert Book.objects.count() == 6

        (folder / "Книга 0.fb2").write_text(_FB2.format(title="Книга 0"), encoding="utf-8")
        for i in (1, 2, 3):
            (folder / f"Книга {i}.fb2").write_text(_FB2.format(title=f"Книга {i}"), encoding="utf-8")
        (folder / "Книга 5.fb2").unlink()              # обычная пропажа одной книги — удаляется
        opdsScanner().scan_all()

    assert Book.objects.count() == 5
