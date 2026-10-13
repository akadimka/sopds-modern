"""Добавить в БД библиотеки записи для файлов, у которых их нет.

    python manage.py library_reindex

БД библиотеки (fb2parser_core/.library_cache.db) нужна синхронизации, чтобы
распознавать дубли уже лежащих в библиотеке книг. Из-за UNIQUE-конфликта
при записи прогоны синхронизации не вносили в неё ни одной записи (баг
№143) — команда восстанавливает недостающие по самим файлам библиотеки.
Уже записанные файлы не трогает; запускать можно повторно.
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Добавить в БД библиотеки записи для файлов библиотеки, у которых их нет."

    def handle(self, *args, **options):
        from fb2parser_core.synchronization import SynchronizationService
        from fb2parser_web.fb2parser_bridge import _config_path

        service = SynchronizationService(_config_path())
        if not service.library_path or not service.library_path.is_dir():
            raise CommandError(f"Библиотека не найдена: {service.library_path}")
        self.stdout.write(f"Библиотека: {service.library_path}\nБД: {service.db_path}")
        stats = service.index_missing_library_files(log=self.stdout.write)
        self.stdout.write(self.style.SUCCESS(
            f"Готово: добавлено {stats['added']} записей, ошибок {stats['errors']}"))
