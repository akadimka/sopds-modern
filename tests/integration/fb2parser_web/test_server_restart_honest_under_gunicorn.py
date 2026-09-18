"""Регрессия для `server_restart()` — docs/quality-roadmap.md, баг №90.

Найдено при архитектурном аудите: в продакшене (gunicorn,
`sopds.settings.gunicorn`: `reload = False`) касание `manage.py`
ничего не делает — gunicorn-воркеры не следят за изменениями файлов
на диске. Но view ВСЕГДА возвращал "⟳ Перезагрузка..." и скрипт
`location.reload()`, создавая у администратора ложное впечатление,
что сервер реально перезапустился.
"""
from django.test import RequestFactory

from fb2parser_web.views import server_restart


class TestServerRestartHonestAboutGunicorn:
    def test_under_gunicorn_reports_unavailable_instead_of_fake_success(self, admin_user):
        rf = RequestFactory()
        request = rf.get(
            "/fb2parser/server-restart/",
            SERVER_SOFTWARE="gunicorn/23.0.0",
        )
        request.user = admin_user
        response = server_restart(request)
        content = response.content.decode("utf-8")

        assert "location.reload()" not in content
        assert "Перезагрузка" not in content
        assert "systemctl restart sopds-modern" in content

    def test_under_dev_server_still_reports_success(self, admin_user):
        rf = RequestFactory()
        request = rf.get(
            "/fb2parser/server-restart/",
            SERVER_SOFTWARE="WSGIServer/0.2 CPython/3.13.14",
        )
        request.user = admin_user
        response = server_restart(request)
        content = response.content.decode("utf-8")

        assert "location.reload()" in content
        assert "Перезагрузка" in content
