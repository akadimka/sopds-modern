"""Регрессия: за обратными прокси (прокси админов → Apache → gunicorn) у
каждого запроса REMOTE_ADDR = 127.0.0.1, и django-axes, блокирующий по IP,
складывал неудачные входы всех посетителей в один счётчик — пять чужих
ошибок блокировали вход всем.
"""
import pytest
from django.test import RequestFactory, override_settings

from sopds.client_ip import client_ip

PROXIES = ["127.0.0.1", "192.168.51.213"]


def _req(remote, xff=None):
    extra = {"REMOTE_ADDR": remote}
    if xff is not None:
        extra["HTTP_X_FORWARDED_FOR"] = xff
    return RequestFactory().get("/", **extra)


@override_settings(SOPDS_TRUSTED_PROXIES=PROXIES)
@pytest.mark.parametrize(
    "remote, xff, expected",
    [
        # Обычный путь: прокси админов дописал клиента, Apache — себя
        ("127.0.0.1", "203.0.113.7, 192.168.51.213", "203.0.113.7"),
        # Клиент сам прислал X-Forwarded-For: левые записи не доверяем
        ("127.0.0.1", "6.6.6.6, 203.0.113.7, 192.168.51.213", "203.0.113.7"),
        # Пробелы и пустые элементы не мешают
        ("127.0.0.1", " 203.0.113.7 ,, 192.168.51.213 ", "203.0.113.7"),
        # В цепочке только доверенные прокси — берём самый левый
        ("127.0.0.1", "192.168.51.213", "192.168.51.213"),
        # Доверенный прокси без заголовка
        ("127.0.0.1", None, "127.0.0.1"),
        # Запрос не от прокси: заголовок мог подделать сам клиент
        ("198.51.100.9", "6.6.6.6", "198.51.100.9"),
    ],
)
def test_client_ip_behind_trusted_proxies(remote, xff, expected):
    assert client_ip(_req(remote, xff)) == expected


@override_settings(SOPDS_TRUSTED_PROXIES=[])
def test_without_trusted_proxies_header_is_ignored():
    assert client_ip(_req("198.51.100.9", "6.6.6.6, 127.0.0.1")) == "198.51.100.9"


@override_settings(SOPDS_TRUSTED_PROXIES=PROXIES)
def test_axes_uses_client_ip():
    from axes.helpers import get_client_ip_address

    request = _req("127.0.0.1", "203.0.113.7, 192.168.51.213")
    assert get_client_ip_address(request) == "203.0.113.7"
