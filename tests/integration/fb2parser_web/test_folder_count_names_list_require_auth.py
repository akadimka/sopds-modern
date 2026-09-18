"""Регрессия — docs/quality-roadmap.md, баг №91.

Найдено при архитектурном аудите: `folder_count` и `names_list` —
единственные два view-функции в `fb2parser_web/views.py` (из ~65),
у которых отсутствовал `@staff_member_required`, хотя весь `/fb2parser/`
не защищён никакой блокирующей auth-мидлварой на уровне URLconf.
`folder_count` вдобавок принимает произвольный `path` и рекурсивно
обходит файловую систему — без авторизации это открытый для всех
`os.walk()` по любому пути.
"""
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

from fb2parser_web.views import folder_count, names_list


class TestFolderCountRequiresAuth:
    def test_anonymous_user_redirected_not_served(self, tmp_path):
        (tmp_path / "book.fb2").write_text("x")
        rf = RequestFactory()
        request = rf.get("/fb2parser/folder-count/", {"path": str(tmp_path)})
        request.user = AnonymousUser()
        response = folder_count(request)
        assert response.status_code == 302
        assert "/web/login/" in response["Location"]

    def test_staff_user_gets_real_response(self, tmp_path, admin_user):
        (tmp_path / "book.fb2").write_text("x")
        rf = RequestFactory()
        request = rf.get("/fb2parser/folder-count/", {"path": str(tmp_path)})
        request.user = admin_user
        response = folder_count(request)
        assert response.status_code == 200
        assert b"1 fb2" in response.content


class TestNamesListRequiresAuth:
    def test_anonymous_user_redirected_not_served(self):
        rf = RequestFactory()
        request = rf.get("/fb2parser/normalize/names/")
        request.user = AnonymousUser()
        response = names_list(request)
        assert response.status_code == 302
