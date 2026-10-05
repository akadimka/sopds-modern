"""Автосинхронизация папки наблюдения — см. docs/watch-folder-autosync-design.md.

    python manage.py autosync --dry-run [--folder ПАПКА]

Сейчас доступен только пробный режим: отчёт «что бы я сделал» по каждой
порции, файлы не трогаются.
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from fb2parser_core.autosync_service import (
    STATUS_DOWNLOADING,
    STATUS_EMPTY,
    STATUS_LOOSE,
    STATUS_NO_RECORDS,
)

_STATUS_TEXT = {
    STATUS_DOWNLOADING: "ещё качается или менялась недавно — ждёт следующего запуска",
    STATUS_EMPTY: "книг нет",
    STATUS_LOOSE: "книги прямо в корне папки наблюдения — положите их в подпапку",
    STATUS_NO_RECORDS: "разбор (regen) не вернул ни одной книги — подробности с --verbose",
}


class Command(BaseCommand):
    help = "Автосинхронизация папки наблюдения (пока только --dry-run: отчёт без изменений)."

    def add_arguments(self, parser):
        parser.add_argument("--folder", help="Папка наблюдения (по умолчанию — из настроек autosync).")
        parser.add_argument("--dry-run", action="store_true", help="Только отчёт, файлы не трогаются.")
        parser.add_argument("--verbose", action="store_true", help="Подробный лог разбора (regen).")

    def handle(self, *args, **options):
        if not options["dry_run"]:
            raise CommandError("Автоматический режим ещё не реализован — используйте --dry-run.")
        from fb2parser_web.fb2parser_bridge import get_autosync_service

        if not options["verbose"]:
            logging.getLogger("fb2parser_core").setLevel(logging.WARNING)

        svc = get_autosync_service()
        try:
            reports = svc.preview(options.get("folder"), progress=lambda m: self.stdout.write(m))
        except FileNotFoundError as e:
            raise CommandError(str(e)) from e

        total_auto = total_pending = 0
        for r in reports:
            title = r.name or "(корень)"
            if r.status in _STATUS_TEXT:
                self.stdout.write(f"\n■ {title}: {r.books} кн. — {_STATUS_TEXT[r.status]}")
                continue
            total_auto += r.auto_books
            total_pending += r.pending_books
            self.stdout.write(f"\n■ {title}: {r.books} кн., авто {r.auto_books}, на решение {r.pending_books}")
            for d in sorted(r.decisions, key=lambda d: (d.auto, -len(d.files))):
                self.stdout.write(("   ✓ " if d.auto else "   ? ") + d.describe())
        self.stdout.write(f"\nИтого: авто {total_auto} кн., на решение {total_pending} кн.")
