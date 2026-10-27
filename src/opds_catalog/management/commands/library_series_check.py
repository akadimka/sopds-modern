"""Дубли и «хвосты» серий рядом с компиляциями (opds_catalog.series_check,
docs/series-duplicates.md). Только отчёт — ничего не меняет.

    manage.py library_series_check                  # сводка и примеры
    manage.py library_series_check --csv out.csv    # полный список книг
"""
import csv

from django.core.management.base import BaseCommand
from django.utils import translation

from opds_catalog import series_check
from opds_catalog.sopds_config import sopds_cfg as config

SAMPLE = 5


class Command(BaseCommand):
    help = "Report extra books of a series lying next to its compilation (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--csv", dest="csv_path", help="Write every extra book to this CSV file (UTF-8).")

    def handle(self, *args, csv_path=None, **options):
        translation.activate("en")
        report = series_check.check(config.SOPDS_ROOT_LIB)
        out = self.stdout.write
        t = report["totals"]
        out(f"Books: {t['books']}; series with a compilation: {t['series_with_compilation']}; "
            f"with extra books: {t['series_with_extras']}; extra books: {t['extra_books']}")
        for kind, (label, action) in series_check.KINDS.items():
            n = report["kind_counts"].get(kind, 0)
            if not n:
                continue
            out(f"\n{n:5}  {label} — {action}")
            shown = 0
            for row in report["series"]:
                for e in row["extras"]:
                    if e["kind"] == kind and shown < SAMPLE:
                        out(f"       {row['author']} / {row['series']}: {e['title']}  [{e['path']}]")
                        shown += 1
        issues = [(r, i) for r in report["series"] for i in r["issues"]]
        if issues:
            out(f"\nCompilations with problems: {len(issues)}")
            for r, i in issues[:SAMPLE * 2]:
                out(f"       {r['compilation']['path']}: {series_check.ISSUES[i]}")
        out(f"\nSeries in several genres: {len(report['genre_splits'])}")
        for r in report["genre_splits"][:SAMPLE * 2]:
            genres = ", ".join(f"{g} ({n})" for g, n in r["genres"])
            out(f"       {r['author']} / {r['series']}: {genres} -> {r['majority'] or 'tie'}")
        out(f"\nPseudonym / real name candidates: {len(report['pseudonyms'])}")
        for r in report["pseudonyms"][:SAMPLE * 2]:
            out(f"       {r['alias']} = {r['name']} ({r['count']}; folders: {', '.join(r['folders'])})")
        if csv_path:
            self._write_csv(csv_path, report)
            out(f"\nCSV: {csv_path}")
        out("\nReport only — nothing was changed.")

    @staticmethod
    def _write_csv(path, report):
        with open(path, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["author", "series", "compilation", "kind", "action", "book_id", "title",
                             "path", "genre", "added", "compilation_issues"])
            for r in report["series"]:
                issues = "; ".join(str(series_check.ISSUES[i]) for i in r["issues"])
                for e in r["extras"]:
                    label, action = series_check.KINDS[e["kind"]]
                    writer.writerow([r["author"], r["series"], r["compilation"]["path"], label, action, e["id"],
                                     e["title"], e["path"], e["genre"], e["added"].strftime("%Y-%m-%d"), issues])
