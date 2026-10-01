"""Эндпоинты, меняющие состояние, не должны срабатывать на GET: при
SameSite=Lax cookie сессии уходит и с обычной ссылки/редиректа с чужого
сайта, а CSRF-токен проверяется только для POST. `server_restart`
по GET перезапускал systemd-сервис (DoS ссылкой), `compress_stop`
останавливал сжатие библиотеки.
"""
import pytest
from django.test import RequestFactory

from fb2parser_web.views import compress_stop, server_restart


@pytest.mark.parametrize("view, url", [
    (server_restart, "/fb2parser/server-restart/"),
    (compress_stop, "/fb2parser/compress/stop/"),
])
def test_get_is_rejected(view, url, admin_user, monkeypatch):
    import threading
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self))
    request = RequestFactory().get(url)
    request.user = admin_user
    assert view(request).status_code == 405
    assert started == []
