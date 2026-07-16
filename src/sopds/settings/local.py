import socket

from .base import *  # noqa: F403

DEBUG = True

# Локальный dev-сервер — один процесс (runserver), так что нет гонки между
# worker-процессами, ради которой memcached обязателен в проде (см. base.py).
# LocMemCache работает без внешних зависимостей — не нужно ставить и
# поднимать memcached только чтобы запустить сайт локально.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    },
}

INSTALLED_APPS += ("debug_toolbar",)  # noqa: F405

MIDDLEWARE += [  # noqa: F405
    "debug_toolbar.middleware.DebugToolbarMiddleware",
]

ROOT_URLCONF = "sopds.urls.local"

INTERNAL_IPS = [
    "127.0.0.1",
]
ip = socket.gethostbyname(socket.gethostname())
INTERNAL_IPS += [ip[:-1] + "1"]
