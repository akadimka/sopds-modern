"""`sopds_watch` пересканирует большую папку по частям — у каждой своя
транзакция (баг №163).

Сервер 2, 2026-10-19: после первого копирования библиотеки «изменилась»
вся библиотека — `_dedup_nested` свёл набор к одной папке (корню), и её
пересборка шла одной транзакцией 15 минут: база в режиме rollback-журнала
была закрыта и для чтения, сайт не отвечал. На основном сервере (WAL) та же
длинная транзакция не давала писать другим процессам (№161).
"""
import os
import shutil

import pytest
from django.db import transaction

from opds_catalog import opdsdb
from opds_catalog.management.commands import sopds_watch
from opds_catalog.management.commands.sopds_watch import Command, _split_scopes
from opds_catalog.models import Book
from opds_catalog.sopdscan import opdsScanner

pytestmark = pytest.mark.django_db

_FB2 = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "data", "262001.fb2")


@pytest.fixture
def library(tmp_path, override_config):
    for author in ("Автор А", "Автор Б", "Автор В"):
        folder = tmp_path / "Жанр" / author
        folder.mkdir(parents=True)
        shutil.copyfile(_FB2, folder / f"{author}.fb2")
    opdsdb.clear_all()
    with override_config(SOPDS_ROOT_LIB=str(tmp_path), SOPDS_DELETE_LOGICAL=False):
        opdsScanner().scan_all()
        assert Book.objects.count() == 3
        yield tmp_path


def test_root_is_split_into_folders_with_books(library):
    scopes = _split_scopes(str(library), str(library))
    assert sorted(os.path.relpath(s, library) for s in scopes) == [
        os.path.join("Жанр", a) for a in ("Автор А", "Автор Б", "Автор В")]


def test_vanished_folder_is_a_scope_and_its_books_go(library, monkeypatch):
    shutil.rmtree(library / "Жанр" / "Автор Б")
    assert os.path.join(str(library), "Жанр", "Автор Б") in _split_scopes(str(library), str(library))

    atomics = []
    real_atomic = transaction.atomic
    monkeypatch.setattr(sopds_watch.transaction, "atomic",
                        lambda *a, **k: atomics.append(1) or real_atomic(*a, **k))
    Command()._flush([str(library)], opdsScanner())

    assert sorted(Book.objects.values_list("filename", flat=True)) == ["Автор А.fb2", "Автор В.fb2"]
    assert len(atomics) >= 3          # по транзакции на часть (+ чистка сирот), а не одна на всё
