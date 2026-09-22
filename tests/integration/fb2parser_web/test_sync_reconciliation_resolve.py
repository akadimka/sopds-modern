"""Регрессия/фича для `sync_reconciliation_resolve()` — действия на
"Requires manual reconciliation" панели (fb2parser_web/views.py,
fb2parser_web/templates/fb2parser/sync_status.html).

Раньше эта панель была чисто информационной (см.
tests/unit/fb2parser_core/test_sync_author_reconciliation.py) — файл,
требующий ручной сверки, оставался нетронутым в исходной папке НАВСЕГДА,
без единой кнопки действия. Добавлены три действия на каждую строку:
"Дубликат — удалить" (confirmed-delete через тот же confined-delete, что
у duplicates/broken-files), "Не дубликат — перенести" (создать новую,
отдельную запись в библиотеке под вычисленным автором) и "Пропустить"
(не трогать сейчас, но перестать поднимать именно эту пару на будущих
синхронизациях — см. TestLooseAuthorMatchAvoidsSilentDuplication.
test_skipped_pair_treated_as_ordinary_new_file в
tests/unit/fb2parser_core/test_sync_author_reconciliation.py для проверки
самого skip-листа внутри _build_folder_structure()).
"""
import json

import pytest
from django.test import RequestFactory

from fb2parser_web.views import sync_job, sync_reconciliation_resolve


def _note(incoming="incoming/файл.fb2", author="Лукьяненко Сергей, Перумов Ник",
          existing_author="Сергей,Перумов Лукьяненко", existing_path="старый/путь.fb2",
          series="Не время для драконов", genre="Фантастика", subseries=""):
    return {
        "incoming_file_path": incoming,
        "incoming_author": author,
        "existing_author": existing_author,
        "existing_file_path": existing_path,
        "series": series,
        "title": "Не время для драконов",
        "genre": genre,
        "subseries": subseries,
    }


def _post(note, action, admin_user):
    rf = RequestFactory()
    request = rf.post(
        "/fb2parser/sync/reconciliation/resolve/",
        data=json.dumps({"action": action, "note": note}),
        content_type="application/json",
    )
    request.user = admin_user
    return sync_reconciliation_resolve(request)


@pytest.fixture(autouse=True)
def _reset_sync_job():
    sync_job.reset()
    yield
    sync_job.reset()


class TestDeleteAction:
    def test_incoming_file_deleted_as_confirmed_duplicate(self, tmp_path, admin_user):
        scan_path = tmp_path / "staging"
        scan_path.mkdir()
        incoming = scan_path / "incoming" / "файл.fb2"
        incoming.parent.mkdir()
        incoming.write_bytes(b"stub")

        note = _note()
        sync_job.update(scan_path=str(scan_path), reconciliation_notes=[note])

        response = _post(note, "delete", admin_user)
        data = json.loads(response.content)

        assert response.status_code == 200
        assert data["ok"] is True
        assert not incoming.exists()
        assert sync_job.get()["reconciliation_notes"] == []

    def test_missing_incoming_file_reports_error(self, tmp_path, admin_user):
        scan_path = tmp_path / "staging"
        scan_path.mkdir()
        note = _note()
        sync_job.update(scan_path=str(scan_path), reconciliation_notes=[note])

        response = _post(note, "delete", admin_user)
        data = json.loads(response.content)

        assert response.status_code == 404
        assert "error" in data
        # Не разрешённую запись не убираем из списка — файл так и не найден.
        assert sync_job.get()["reconciliation_notes"] == [note]


class TestMoveAction:
    def test_incoming_file_moved_to_computed_author_folder(self, tmp_path, admin_user, monkeypatch):
        scan_path = tmp_path / "staging"
        library_path = tmp_path / "library"
        scan_path.mkdir()
        library_path.mkdir()
        incoming = scan_path / "incoming" / "файл.fb2"
        incoming.parent.mkdir()
        incoming.write_bytes(b"stub content")

        import fb2parser_web.fb2parser_bridge as bridge_module

        class _FakeSvc:
            def __init__(self):
                self.library_path = library_path
                self.last_scan_path = scan_path
            _shorten_filename_for_path_limit = staticmethod(lambda target_dir, name: name)

        monkeypatch.setattr(bridge_module, "get_sync_service", lambda: _FakeSvc())

        note = _note(genre="Фантастика", author="Лукьяненко Сергей, Перумов Ник", series="Не время для драконов")
        sync_job.update(scan_path=str(scan_path), reconciliation_notes=[note])

        response = _post(note, "move", admin_user)
        data = json.loads(response.content)

        assert response.status_code == 200, data
        assert data["ok"] is True
        assert not incoming.exists()
        moved = library_path / "Фантастика" / "Лукьяненко Сергей, Перумов Ник" / "Не время для драконов" / "файл.fb2"
        assert moved.exists()
        assert moved.read_bytes() == b"stub content"
        assert sync_job.get()["reconciliation_notes"] == []

    def test_refuses_to_move_outside_library(self, tmp_path, admin_user, monkeypatch):
        scan_path = tmp_path / "staging"
        library_path = tmp_path / "library"
        scan_path.mkdir()
        library_path.mkdir()
        incoming = scan_path / "файл.fb2"
        incoming.write_bytes(b"stub")

        import fb2parser_web.fb2parser_bridge as bridge_module

        class _FakeSvc:
            def __init__(self):
                self.library_path = library_path
                self.last_scan_path = scan_path
            _shorten_filename_for_path_limit = staticmethod(lambda target_dir, name: name)

        monkeypatch.setattr(bridge_module, "get_sync_service", lambda: _FakeSvc())

        # genre содержит traversal-попытку — не должно вырваться из library_path.
        note = _note(incoming="файл.fb2", genre="..", author="..", series="")
        sync_job.update(scan_path=str(scan_path), reconciliation_notes=[note])

        response = _post(note, "move", admin_user)
        data = json.loads(response.content)

        assert response.status_code == 400
        assert "error" in data
        assert incoming.exists()


class TestSkipAction:
    def test_file_left_untouched_and_removed_from_notes(self, tmp_path, admin_user, monkeypatch):
        import fb2parser_core.synchronization as sync_module

        skip_path = tmp_path / ".reconciliation_skip.json"
        monkeypatch.setattr(sync_module, "_RECONCILIATION_SKIP_PATH", skip_path)

        scan_path = tmp_path / "staging"
        scan_path.mkdir()
        incoming = scan_path / "файл.fb2"
        incoming.write_bytes(b"stub")

        note = _note(incoming="файл.fb2")
        sync_job.update(scan_path=str(scan_path), reconciliation_notes=[note])

        response = _post(note, "skip", admin_user)
        data = json.loads(response.content)

        assert response.status_code == 200
        assert data["ok"] is True
        assert incoming.exists()  # файл не трогаем
        assert sync_job.get()["reconciliation_notes"] == []
        assert "файл.fb2" in sync_module._load_reconciliation_skip_set()
