"""Регрессия для `fb2parser_web.views._run_scan_thread()` —
docs/quality-roadmap.md, баг №77.

Реальный случай: пользователь отмечал галочкой конкретную подпапку в
дереве (например "The Big Book") и нажимал "▶ Скан", ожидая точечного
пересканирования только этой подпапки. На деле результат никогда не
менялся — `scan_all()` полностью игнорирует переданный `root_path`
(используя его только для оценки прогресс-бара) и всегда сканирует
`config.SOPDS_ROOT_LIB` целиком, независимо от того, какая папка была
выбрана.
"""
import shutil

import pytest

from opds_catalog.models import Book

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_folder_library(tmp_path, override_config):
    """Библиотека из 2 подпапок, в каждой — копия тестового FB2-файла."""
    from opds_catalog import opdsdb

    import os
    # tests/integration/fb2parser_web/test_main_scan_scoped.py -> tests/data/262001.fb2
    src_fb2 = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "262001.fb2",
    )

    folder_a = tmp_path / "folderA"
    folder_b = tmp_path / "folderB"
    folder_a.mkdir()
    folder_b.mkdir()
    shutil.copy(src_fb2, folder_a / "book_a.fb2")
    shutil.copy(src_fb2, folder_b / "book_b.fb2")

    opdsdb.clear_all()
    with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
        yield str(tmp_path), str(folder_a), str(folder_b)


class TestScopedFolderScan:
    def test_scanning_a_subfolder_does_not_touch_other_folders(self, two_folder_library):
        from fb2parser_web.views import _run_scan_thread

        root, folder_a, folder_b = two_folder_library

        # Полный скан — обе книги попадают в БД.
        _run_scan_thread(root)
        assert Book.objects.filter(path="folderA").count() == 1
        assert Book.objects.filter(path="folderB").count() == 1

        # Файл в folderA исчезает с диска — но точечный скан запускается
        # только для folderB.
        shutil.rmtree(folder_a)
        _run_scan_thread(folder_b)

        # Книга folderA должна остаться нетронутой (точечный скан не должен
        # был её касаться вовсе) — без фикса весь этот вызов на самом деле
        # пересканировал бы ВСЮ библиотеку, включая уже удалённую folderA,
        # и физически удалил бы её запись как отсутствующую.
        assert Book.objects.filter(path="folderA").count() == 1
        assert Book.objects.filter(path="folderB").count() == 1

    def test_scanning_the_library_root_still_does_a_full_scan(
        self, two_folder_library, override_config
    ):
        from fb2parser_web.views import _run_scan_thread

        root, folder_a, folder_b = two_folder_library

        _run_scan_thread(root)
        assert Book.objects.filter(path="folderA").count() == 1
        assert Book.objects.filter(path="folderB").count() == 1

        # Файл в folderA исчезает, но сканируется КОРЕНЬ библиотеки целиком —
        # это должно по-прежнему быть полным пересканированием (старое
        # поведение не должно измениться для этого случая). Явно
        # фиксируем физическое удаление (баг №89: по умолчанию
        # SOPDS_DELETE_LOGICAL=true — исчезнувшая книга мягко скрывается
        # (avail=0), а не удаляется — эта проверка про сам факт полного
        # пересканирования, а не про режим удаления, поэтому пин явный).
        shutil.rmtree(folder_a)
        with override_config(SOPDS_DELETE_LOGICAL=False):
            _run_scan_thread(root)

        assert Book.objects.filter(path="folderA").count() == 0
        assert Book.objects.filter(path="folderB").count() == 1
