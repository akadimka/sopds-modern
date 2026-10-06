from django.apps import AppConfig


class SopdsWebBackendConfig(AppConfig):
    name = "sopds_web_backend"

    def ready(self):
        from . import signals  # noqa: F401 — регистрация обработчиков
