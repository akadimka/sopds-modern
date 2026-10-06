"""Автосинхронизация папки наблюдения — см. docs/watch-folder-autosync-design.md.

    python manage.py autosync                      # по режиму из настроек
    python manage.py autosync --dry-run [--folder ПАПКА]   # только отчёт

Режим (`off` / `dry_run` / `auto`) и папка наблюдения задаются в настройках
(раздел autosync config.json, страница настроек SOPDS).
"""
import logging

from django.core.management.base import BaseCommand, CommandError

from fb2parser_core.autosync_service import (
    RUN_BUSY,
    RUN_ERROR,
    RUN_OFF,
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
    help = "Автосинхронизация папки наблюдения (по режиму из настроек; --dry-run — только отчёт)."

    def add_arguments(self, parser):
        parser.add_argument("--folder", help="Папка наблюдения для --dry-run (по умолчанию — из настроек).")
        parser.add_argument("--dry-run", action="store_true", help="Только отчёт, файлы не трогаются.")
        parser.add_argument("--verbose", action="store_true", help="Подробный лог разбора и синхронизации.")

    def handle(self, *args, **options):
        from fb2parser_web.fb2parser_bridge import get_autosync_service

        if not options["verbose"]:
            logging.getLogger("fb2parser_core").setLevel(logging.WARNING)
        svc = get_autosync_service()
        say = self.stdout.write

        if options["dry_run"]:
            try:
                reports = svc.preview(options.get("folder"), progress=say)
            except FileNotFoundError as e:
                raise CommandError(str(e)) from e
            self._print_reports(reports)
            return

        result = svc.run(progress=say)
        if result.status == RUN_OFF:
            say("Автосинхронизация выключена (режим off в настройках).")
            return
        if result.status == RUN_BUSY:
            say(f"Пропущено: {result.error}")
            return
        self._print_reports(result.reports)
        if result.status == RUN_ERROR:
            raise CommandError(f"Сбой автосинхронизации: {result.error}")
        say(f"\nРежим {result.mode}: перемещено {len(result.moved)}, ждут решения {result.pending}, "
            f"оставлены синхронизацией {result.kept}, удалены как дубликаты {result.removed}.")

    def _print_reports(self, reports):
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
        self.stdout.write(f"\nИтого по решениям: авто {total_auto} кн., на решение {total_pending} кн.")
