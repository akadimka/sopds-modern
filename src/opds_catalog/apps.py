from django.apps import AppConfig


class OpdsCatalogConfig(AppConfig):
    name = "opds_catalog"

    def ready(self):
        from django.db.backends.signals import connection_created

        def _set_wal(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                connection.cursor().execute("PRAGMA journal_mode=WAL;")
                connection.cursor().execute("PRAGMA synchronous=NORMAL;")

        connection_created.connect(_set_wal)

        # Учёт владельцев файлов библиотеки для операций fb2parser
        # (fb2parser_core не зависит от Django — подключаем его отсюда).
        from fb2parser_core import ownership_hooks

        from .ownership import LibraryOwnershipHooks
        ownership_hooks.register(LibraryOwnershipHooks())
