"""Переименовать автора, уже лежащего в библиотеке.

    python manage.py library_rename_author "Призывающий Оро" "Оро Призывающий"
    python manage.py library_rename_author --pairs pairs.tsv          # «старое<TAB>новое» в строке
    python manage.py library_rename_author --pairs pairs.tsv --apply  # без --apply — только отчёт

Нужна, когда меняется то, как пайплайн пишет имя автора (баг №158: русские
псевдонимы в author_surname_conversions) — иначе следующая синхронизация
положит новые книги автора в другую папку. Папка, имена файлов «Автор - …»,
тег <author>, БД библиотеки; каталог OPDS подхватит новые пути при плановом
сканировании. Под блокировкой синхронизации.
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Переименовать автора в библиотеке (папка, имена файлов, <author>, БД библиотеки)."

    def add_arguments(self, parser):
        parser.add_argument("old", nargs="?")
        parser.add_argument("new", nargs="?")
        parser.add_argument("--pairs", help="файл со строками «старое<TAB>новое»")
        parser.add_argument("--apply", action="store_true", help="выполнить (без него — только отчёт)")

    def handle(self, *args, **options):
        from fb2parser_core.sync_lock import SyncBusy, sync_lock
        from fb2parser_core.synchronization import SynchronizationService
        from fb2parser_web.fb2parser_bridge import _config_path, _sync_lock_path

        pairs = []
        if options["pairs"]:
            with open(options["pairs"], encoding="utf-8") as f:
                pairs = [tuple(line.rstrip("\n").split("\t")) for line in f if "\t" in line]
        if options["old"] and options["new"]:
            pairs.append((options["old"], options["new"]))
        if not pairs:
            raise CommandError("Нужна пара «старое новое» или --pairs")

        service = SynchronizationService(_config_path())
        apply = options["apply"]
        self.stdout.write(f"Библиотека: {service.library_path}  ({'ВЫПОЛНЕНИЕ' if apply else 'только отчёт'})")
        total = {}
        try:
            with sync_lock(_sync_lock_path(), "library_rename_author"):
                for old, new in pairs:
                    stats = service.rename_library_author(old, new, apply=apply, log=self.stdout.write)
                    for k, v in stats.items():
                        total[k] = total.get(k, 0) + v
        except SyncBusy as exc:
            raise CommandError(f"Идёт синхронизация ({exc.owner}) — повторите позже") from None
        self.stdout.write(self.style.SUCCESS(
            f"Файлов: {total.get('files', 0)}, перенесено{'' if apply else ' бы'}: {total.get('moved', 0)}, "
            f"конфликтов: {total.get('conflicts', 0)}, ошибок: {total.get('errors', 0)}, "
            f"записей БД: {total.get('db_rows', 0)}"))
