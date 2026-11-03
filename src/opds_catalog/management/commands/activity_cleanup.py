"""Удалить записи журнала действий старше срока хранения.

Сроки — настройки суперпользователя (вкладка «Журнал»): вход, безопасность
и изменения — `activity_keep_security_days`, чтение и скачивание —
`activity_keep_reading_days`; 0 — бессрочно. Плановый сканер
(`sopds_scanner`) делает это сам раз в сутки (docs/activity-log-design.md).
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Remove activity log records older than the retention set on the Journal tab"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="only count, do not delete")

    def handle(self, *args, **options):
        from sopds_web_backend import activity

        keep = activity.keep_days()
        removed = activity.cleanup(dry_run=options["dry_run"])
        verb = "would be removed" if options["dry_run"] else "removed"
        for group in ("security", "reading"):
            days = keep[group]
            limit = f"{days} days" if days else "kept forever"
            self.stdout.write(f"{group}: {removed[group]} {verb} ({limit})")
