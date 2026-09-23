"""Регрессия для `server_restart()` — docs/quality-roadmap.md, баг №90,
и его последующее дополнение.

Найдено при архитектурном аудите: в продакшене (gunicorn,
`sopds.settings.gunicorn`: `reload = False`) касание `manage.py`
ничего не делает — gunicorn-воркеры не следят за изменениями файлов
на диске. View раньше ВСЕГДА возвращал "⟳ Перезагрузка..." и скрипт
`location.reload()`, создавая у администратора ложное впечатление,
что сервер реально перезапустился — первый фикс просто честно
сообщал "перезапусти вручную через systemctl".

Дополнение (по запросу пользователя): вместо просьбы выполнить
`systemctl restart` вручную по SSH, view теперь ДЕЙСТВИТЕЛЬНО его
выполняет — через узко ограниченное (одна точная команда без
аргументов, без wildcard) правило passwordless sudo для www-data
(/etc/sudoers.d/sopds-modern-restart на сервере, не в репозитории).
"Честность" бага №90 сохраняется: сообщение об успехе снова
показывается, но теперь оно СНОВА правдиво, а не потому что забыли
проверить SERVER_SOFTWARE.
"""
from unittest.mock import patch

from django.test import RequestFactory

from fb2parser_web.views import server_restart


class TestServerRestartHonestAboutGunicorn:
    def test_under_gunicorn_triggers_real_systemctl_restart(self, admin_user):
        rf = RequestFactory()
        request = rf.get(
            "/fb2parser/server-restart/",
            SERVER_SOFTWARE="gunicorn/23.0.0",
        )
        request.user = admin_user

        # Перезапуск идёт в фоновом потоке (иначе процесс, отправляющий
        # ответ, был бы убит собственной командой раньше, чем ответ уйдёт
        # браузеру) — подменяем threading.Thread, чтобы выполнить target
        # СИНХРОННО прямо здесь, а не гоняться за настоящим потоком.
        class _SyncThread:
            def __init__(self, target=None, daemon=None, **kw):
                self._target = target

            def start(self):
                self._target()

        with patch("subprocess.run") as mock_run, \
             patch("time.sleep"), \
             patch("threading.Thread", _SyncThread):
            response = server_restart(request)

        content = response.content.decode("utf-8")
        assert "location.reload()" in content
        assert "Перезапуск" in content

        mock_run.assert_called_once_with(
            ["sudo", "-n", "systemctl", "restart", "sopds-modern"],
            check=False,
        )

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
