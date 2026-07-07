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
