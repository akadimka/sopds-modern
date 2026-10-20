"""Сверка владения файлами библиотеки с диском и каталогом
(opds_catalog.ownership.reconcile, docs/multiuser-plan.md шаг 1.3).

    manage.py library_owners_check            # только отчёт
    manage.py library_owners_check --apply    # применить
"""
from django.core.management.base import BaseCommand, CommandError

from opds_catalog import ownership

SAMPLE = 10


class Command(BaseCommand):
    help = "Reconcile library file ownership with the files on disk and the OPDS catalog."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Apply the changes (default: report only).")
        parser.add_argument("--force", action="store_true",
                            help="Apply even if most owned files are missing on disk (library not mounted?).")

    def handle(self, *args, apply=False, force=False, **options):
        from fb2parser_web.fb2parser_bridge import _memory_path
        try:
            report = ownership.reconcile(apply=apply, force=force, memory_db=_memory_path())
        except ValueError as e:
            raise CommandError(str(e)) from e
        out = self.stdout.write
        out(f"Files on disk: {report['files_on_disk']}, owned paths: {report['owned_paths']}")
        out(f"Moved files found by fingerprint: {len(report['relinked'])}")
        for gone, found in list(report["relinked"].items())[:SAMPLE]:
            out(f"  {gone} -> {found}")
        out(f"Files without owner (-> administrator): {len(report['to_admin'])}")
        for rel in report["to_admin"][:SAMPLE]:
            out(f"  {rel}")
        out(f"Owned paths missing on disk (ownership dropped): {len(report['dropped'])}")
        for rel in report["dropped"][:SAMPLE]:
            out(f"  {rel}")
        if report["guard"]:
            self.stderr.write(f"NOT APPLIED: {report['guard']}. Use --force if this is expected.")
        if report["applied"]:
            out(f"Catalog owner links added: {report['book_links_added']}, "
                f"removed: {report['book_links_removed']}")
            out(self.style.SUCCESS("Applied."))
        elif not apply:
            out("Report only; run with --apply to apply.")
