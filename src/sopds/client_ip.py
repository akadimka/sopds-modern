"""Адрес клиента за обратными прокси (для django-axes).

За цепочкой прокси (например, прокси провайдера → Apache → gunicorn)
REMOTE_ADDR у всех запросов — адрес последнего прокси, и axes, блокирующий
по IP, считал бы неудачные входы всех посетителей вместе.

X-Forwarded-For разбирается справа налево: каждый доверенный прокси
дописывает в конец адрес, от которого получил запрос, поэтому первый
справа адрес, не входящий в SOPDS_TRUSTED_PROXIES, — настоящий клиент.
Левее него значения мог подставить сам клиент — им не доверяем.
"""
from django.conf import settings


def client_ip(request) -> str:
    remote = request.META.get("REMOTE_ADDR", "")
    trusted = set(getattr(settings, "SOPDS_TRUSTED_PROXIES", ()) or ())
    if remote not in trusted:
        return remote
    chain = [
        part.strip()
        for part in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")
        if part.strip()
    ]
    for addr in reversed(chain):
        if addr not in trusted:
            return addr
    return chain[0] if chain else remote
