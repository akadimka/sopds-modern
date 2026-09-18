"""Регрессия — docs/quality-roadmap.md, баг №93.

Найдено при архитектурном аудите: `martyrs_delete`, `broken_files_delete`
и `duplicates_delete` принимают список путей от клиента (JSON-тело
POST) и удаляли их через `os.remove()` без проверки, что путь лежит
внутри отсканированной папки — `norm_job["folder"]` использовался
только ПОСЛЕ удаления, для чистки опустевших родительских папок.
Подменённый/подделанный `paths` в теле запроса позволял бы удалить
любой файл, доступный процессу, а не только файлы внутри библиотеки.
"""
import json

import pytest
from django.test import RequestFactory

from fb2parser_web.views import (
    _delete_paths_confined,
    _is_within_folder,
    broken_files_delete,
    duplicates_delete,
    martyrs_delete,
    norm_job,
)


class TestIsWithinFolder:
    def test_file_inside_folder_is_within(self, tmp_path):
        folder = tmp_path / "library"
        folder.mkdir()
        f = folder / "book.fb2"
        f.write_text("x")
        assert _is_within_folder(str(f), str(folder)) is True

    def test_file_outside_folder_is_not_within(self, tmp_path):
        folder = tmp_path / "library"
        folder.mkdir()
        outside = tmp_path / "elsewhere" / "secret.txt"
        outside.parent.mkdir()
        outside.write_text("x")
        assert _is_within_folder(str(outside), str(folder)) is False

    def test_traversal_via_dotdot_is_not_within(self, tmp_path):
        folder = tmp_path / "library"
        folder.mkdir()
        outside = tmp_path / "elsewhere" / "secret.txt"
        outside.parent.mkdir()
        outside.write_text("x")
        traversal_path = str(folder / ".." / "elsewhere" / "secret.txt")
        assert _is_within_folder(traversal_path, str(folder)) is False

    def test_no_folder_configured_rejects_everything(self, tmp_path):
        f = tmp_path / "book.fb2"
        f.write_text("x")
        assert _is_within_folder(str(f), "") is False


class TestDeletePathsConfined:
    def test_outside_path_not_deleted(self, tmp_path):
        folder = tmp_path / "library"
        folder.mkdir()
        outside = tmp_path / "elsewhere" / "secret.txt"
        outside.parent.mkdir()
        outside.write_text("x")

        deleted, errors = _delete_paths_confined([str(outside)], str(folder))

        assert deleted == 0
        assert outside.exists()
        assert len(errors) == 1

    def test_inside_path_deleted_normally(self, tmp_path):
        folder = tmp_path / "library"
        folder.mkdir()
        f = folder / "book.fb2"
        f.write_text("x")

        deleted, errors = _delete_paths_confined([str(f)], str(folder))

        assert deleted == 1
        assert not f.exists()
        assert errors == []


@pytest.fixture(autouse=True)
def _reset_norm_job():
    norm_job.reset()
    yield
    norm_job.reset()


@pytest.mark.parametrize("view", [martyrs_delete, broken_files_delete, duplicates_delete])
class TestDeleteViewsRefuseOutsidePaths:
    def test_path_outside_folder_refused(self, view, tmp_path, admin_user):
        folder = tmp_path / "library"
        folder.mkdir()
        outside = tmp_path / "elsewhere" / "secret.txt"
        outside.parent.mkdir()
        outside.write_text("x")
        norm_job.update(folder=str(folder))

        rf = RequestFactory()
        request = rf.post(
            "/fb2parser/normalize/whatever/delete/",
            data=json.dumps({"paths": [str(outside)]}),
            content_type="application/json",
        )
        request.user = admin_user
        response = view(request)
        data = json.loads(response.content)

        assert data["deleted"] == 0
        assert outside.exists()

    def test_path_inside_folder_deleted(self, view, tmp_path, admin_user):
        folder = tmp_path / "library"
        folder.mkdir()
        f = folder / "book.fb2"
        f.write_text("x")
        norm_job.update(folder=str(folder))

        rf = RequestFactory()
        request = rf.post(
            "/fb2parser/normalize/whatever/delete/",
            data=json.dumps({"paths": [str(f)]}),
            content_type="application/json",
        )
        request.user = admin_user
        response = view(request)
        data = json.loads(response.content)

        assert data["deleted"] == 1
        assert not f.exists()
