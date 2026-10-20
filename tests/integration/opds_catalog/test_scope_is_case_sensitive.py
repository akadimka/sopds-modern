"""Охват папки при точечной пересборке учитывает регистр (баг №165).

Сервер 2, 2026-10-20: в библиотеке две папки автора, отличающиеся только
регистром, — «Фантастика/Allig_Eri» (4 книги) и «Фантастика/Allig_eri»
(1 книга). Охват папки строился через `path__startswith` (LIKE), а LIKE в
SQLite не различает регистр латиницы: пересборка «Allig_eri» захватывала и
книги «Allig_Eri», не находила их в своей папке и с физическим удалением
стирала (вместе с рейтингами). На основном сервере они потом завелись
заново с новыми id, на втором — пропали из каталога.

Файловая система Windows регистр не различает — две такие папки здесь не
создать, поэтому вторая «папка» — только запись в базе.
"""
import os
import shutil

import pytest

from opds_catalog import opdsdb
from opds_catalog.models import Book
from opds_catalog.sopdscan import opdsScanner

pytestmark = pytest.mark.django_db

_FB2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "data", "262001.fb2")


def test_rescan_of_one_case_variant_keeps_the_other(tmp_path, override_config):
    for series, name in (("Затмение", "a.fb2"), ("Игра престолов", "b.fb2")):
        folder = tmp_path / "Фантастика" / "Allig_Eri" / series
        folder.mkdir(parents=True)
        shutil.copyfile(_FB2, folder / name)
    opdsdb.clear_all()
    with override_config(SOPDS_ROOT_LIB=str(tmp_path), SOPDS_DELETE_LOGICAL=False):
        opdsScanner().scan_all()
        # вторая книга — «в папке» Allig_eri (на диске её нет)
        Book.objects.filter(filename="b.fb2").update(path=os.path.join("Фантастика", "Allig_eri", "Игра престолов"))

        rel = os.path.join("Фантастика", "Allig_eri")
        opdsdb.avail_check_prepare_scoped(rel)
        opdsdb.books_del_scoped(rel, logical=False)

    assert list(Book.objects.values_list("filename", flat=True)) == ["a.fb2"]


def test_path_under_is_case_sensitive_and_does_not_leak_to_siblings():
    j = os.path.join   # пути в базе — с разделителем ОС (os.path.relpath в сканере)
    q_paths = [j("Ф", "Allig_Eri"), j("Ф", "Allig_Eri", "С"), j("Ф", "Allig_eri", "С"),
               j("Ф", "Allig_Eri2", "С"), j("Ф", "Allig_Eri.x", "С")]
    from opds_catalog.models import Catalog
    cat = Catalog.objects.create(cat_name=".", path=".", cat_type=0)
    for i, p in enumerate(q_paths):
        Book.objects.create(filename=f"{i}.fb2", path=p, catalog=cat, filesize=1, format="fb2",
                            docdate="", lang="", title=str(i), search_title=str(i), annotation="")
    under = set(Book.objects.filter(opdsdb.path_under_q(j("Ф", "Allig_Eri"))).values_list("path", flat=True))
    assert under == {j("Ф", "Allig_Eri", "С")}
    scope = set(Book.objects.filter(opdsdb._path_scope_q(j("Ф", "Allig_Eri"))).values_list("path", flat=True))
    assert scope == {j("Ф", "Allig_Eri"), j("Ф", "Allig_Eri", "С")}
