"""`sopds_watch` при точечной пересборке папки удалял пропавшие книги из БД
всегда физически, игнорируя SOPDS_DELETE_LOGICAL (полный скан эту
настройку учитывает). Папка, временно пропавшая с диска (переименование,
отключённый диск), стирала из каталога все свои книги.
"""
import os
import shutil

import pytest

from opds_catalog import opdsdb
from opds_catalog.management.commands.sopds_watch import Command
from opds_catalog.models import Book
from opds_catalog.sopdscan import opdsScanner

pytestmark = pytest.mark.django_db

_FB2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "data", "262001.fb2")


@pytest.fixture
def library(tmp_path, override_config):
    folder = tmp_path / "Жанр" / "Автор"
    folder.mkdir(parents=True)
    shutil.copyfile(_FB2, folder / "book.fb2")
    opdsdb.clear_all()
    with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
        opdsScanner().scan_all()
        assert Book.objects.filter(filename="book.fb2", avail__gt=0).count() == 1
        yield folder


@pytest.mark.parametrize("vanish", ["file", "folder"])
def test_logical_delete_hides_vanished_books(library, override_config, vanish):
    if vanish == "file":
        (library / "book.fb2").unlink()
    else:
        shutil.rmtree(library)
    with override_config(SOPDS_DELETE_LOGICAL=True):
        Command()._flush([str(library)], opdsScanner())
    assert Book.objects.get(filename="book.fb2").avail == 0


def test_physical_delete_removes_vanished_books(library, override_config):
    (library / "book.fb2").unlink()
    with override_config(SOPDS_DELETE_LOGICAL=False):
        Command()._flush([str(library)], opdsScanner())
    assert not Book.objects.filter(filename="book.fb2").exists()
