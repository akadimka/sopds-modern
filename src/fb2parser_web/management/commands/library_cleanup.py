"""Разовая чистка библиотеки (шаг 3 docs/series-duplicates.md).

    python manage.py library_cleanup --part genres --out genres.txt
    python manage.py library_cleanup --part genres --genre-choices choices.tsv --apply
    python manage.py library_cleanup --part compile --out compile.txt
    python manage.py library_cleanup --part compile --apply
    python manage.py library_cleanup --part compile --skip skip.txt --apply
    python manage.py library_cleanup --part books --fixes book_fixes.tsv --out books.txt
    python manage.py library_cleanup --part books --fixes book_fixes.tsv --apply

Без --apply — только отчёт, на диске ничего не меняется. Порядок — сначала
жанры, потом компилятор (после переноса серии в один жанр группы
компилятора меняются, их отчёт снимается заново). Выбор жанра для серий с
равным числом книг — файл «жанр<TAB>автор / серия» (ключ — как в отчёте);
серии без выбора не переносятся. Книги — по файлу «откуда<TAB>куда<TAB>
автор<TAB>название<TAB>серия<TAB>жанр» (`library_cleanup.read_book_fixes`).
Группы компилятора, где есть файл из --skip (путь на строку, «папка/» — всё
в папке), не выполняются — в отчёте «ПРОПУСК».
Под блокировкой синхронизации; каталог OPDS подхватит новые пути при
плановом сканировании.
"""
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = ("Чистка библиотеки: серии в нескольких жанрах, решения компилятора, книги по файлу решений "
            "(без --apply — отчёт).")

    def add_arguments(self, parser):
        parser.add_argument("--part", choices=("genres", "compile", "books"), required=True)
        parser.add_argument("--genre-choices", help="файл «жанр<TAB>автор / серия» для серий с равным числом книг")
        parser.add_argument("--fixes", help="файл решений для --part books")
        parser.add_argument("--skip", help="--part compile: не трогать группы с этими файлами (путь на строку)")
        parser.add_argument("--out", help="записать отчёт в файл (иначе — на экран)")
        parser.add_argument("--apply", action="store_true", help="выполнить (без него — только отчёт)")

    def handle(self, *args, **options):
        from fb2parser_core.sync_lock import SyncBusy, sync_lock
        from fb2parser_core.synchronization import SynchronizationService
        from fb2parser_web.fb2parser_bridge import _config_path, _sync_lock_path

        service = SynchronizationService(_config_path())
        library = Path(service.library_path)
        if not library.is_dir():
            raise CommandError(f"Нет папки библиотеки: {library}")
        apply = options["apply"]
        out = open(options["out"], "w", encoding="utf-8") if options["out"] else None

        def log(line=""):
            (out.write(line + "\n") if out else self.stdout.write(line))

        log(f"Библиотека: {library}  ({'ВЫПОЛНЕНИЕ' if apply else 'только отчёт'})")
        try:
            with sync_lock(_sync_lock_path(), "library_cleanup", library=library):
                if options["part"] == "genres":
                    summary = self._genres(library, service.db_path, options["genre_choices"], apply, log)
                elif options["part"] == "books":
                    if not options["fixes"]:
                        raise CommandError("Для --part books нужен --fixes")
                    summary = self._books(library, service, Path(options["fixes"]), apply, log)
                else:
                    skip = options["skip"]
                    summary = self._compile(library, apply, log, Path(skip) if skip else None)
        except SyncBusy as exc:
            raise CommandError(f"Идёт синхронизация ({exc.owner}) — повторите позже") from None
        finally:
            if out:
                out.close()
        self.stdout.write(self.style.SUCCESS(summary))

    def _genres(self, library, db_path, choices_path, apply, log):
        from fb2parser_core.library_cleanup import (
            find_split_series,
            move_series_to_genre,
            read_genre_choices,
        )

        choices = read_genre_choices(Path(choices_path)) if choices_path else {}
        split = find_split_series(library)
        total = {"series": 0, "moved": 0, "replaced": 0, "dropped": 0, "conflicts": 0, "errors": 0, "waiting": 0,
                 "no_access": 0}
        log(f"Серий в нескольких жанрах: {len(split)}")
        for s in split:
            places = ", ".join(f"{g} ({n})" for g, n in sorted(s.books.items()))
            genre = choices.get(s.key) or s.majority()
            if not genre:
                total["waiting"] += 1
                log(f"? {s.key}: {places} — поровну, нужен выбор жанра (--genre-choices)")
                continue
            source = "выбор" if s.key in choices else "большинство"
            log(f"→ {s.key}: {places} → {genre} ({source})")
            stats = move_series_to_genre(library, s, genre, db_path, apply=apply, log=log)
            total["series"] += 1
            for k in ("moved", "replaced", "dropped", "conflicts", "errors", "no_access"):
                total[k] += stats[k]
        verb = "перенесено" if apply else "перенеслось бы"
        return (f"Серий: {total['series']}, файлов {verb}: {total['moved']}; на занятом месте — замена полной версией: "
                f"{total['replaced']}, удаление лишней: {total['dropped']}, обе остаются: "
                f"{total['conflicts']}; ошибок: {total['errors']}, ждут выбора жанра: {total['waiting']}, "
                f"не переносятся — нет прав на запись: {total['no_access']}")

    def _books(self, library, service, fixes_path, apply, log):
        from fb2parser_core.genre_assign import GenreAssignmentService
        from fb2parser_core.library_cleanup import (
            fix_book,
            read_book_fixes,
            remove_empty_dirs,
        )

        fixes = read_book_fixes(fixes_path)
        log(f"Книг в файле решений: {len(fixes)}")
        service.log_callback = lambda m: log("    " + m.strip())
        genres = GenreAssignmentService(logger=type("L", (), {"log": staticmethod(lambda m: log("    " + m))})())
        total = {}
        for fix in fixes:
            outcome = fix_book(library, fix, service.db_path, service._patch_fb2_tags, genres._assign_genre_to_file,
                               apply=apply, log=log)
            total[outcome] = total.get(outcome, 0) + 1
        if apply:
            # Пустые папки в жанрах, откуда уносили книги («Без жанра/Калинин
            # Даниил» — пустая и до чистки).
            for top in {Path(f.src).parts[0] for f in fixes if f.dst not in ("", "?")}:
                remove_empty_dirs(library / top)
        names =(("moved", "перенесено"), ("fixed", "теги на месте"), ("replaced", "замена полной версией"),
                 ("dropped", "удаление лишней копии"), ("conflict", "занято другой версией"),
                 ("waiting", "ждут выбора"), ("missing", "нет файла"), ("no_access", "нет прав на запись"),
                 ("error", "ошибок"))
        head = "Книги" if apply else "Книги (только отчёт)"
        return f"{head}: " + ", ".join(f"{label}: {total.get(key, 0)}" for key, label in names)

    def _compile(self, library, apply, log, skip_path=None):
        from fb2parser_core.auto_compile_service import find_library_groups, group_genre
        from fb2parser_core.library_cleanup import (
            describe_group,
            group_report_counts,
            read_skip_list,
            skip_reason,
        )
        from fb2parser_web.fb2parser_bridge import _config_path

        skip = read_skip_list(skip_path) if skip_path else []
        compiler, groups = find_library_groups(str(library), config_path=_config_path())
        skipped = {id(g) for g in groups if skip_reason(g, library, skip)}
        counts = group_report_counts([g for g in groups if id(g) not in skipped])
        log(f"Групп: {len(groups)} — очистка {counts.get('cleanup', 0)}, компиляция {counts.get('compile', 0)} "
            f"(томов {counts.get('compiled_books', 0)}); файлов на удаление: {counts.get('delete', 0)}"
            + (f"; пропуск по списку: {len(skipped)} групп" if skip else ""))
        ok = fail = 0
        for g in groups:
            log("\n".join(describe_group(compiler, g, library)))
            reason = skip_reason(g, library, skip)
            if reason:
                log(f"  ⏭ ПРОПУСК — в списке: {reason}")
                continue
            if not apply:
                continue
            result = compiler.compile_group(g, None, delete_sources=True, genre_override=group_genre(g, library))
            if result.success:
                ok += 1
            else:
                fail += 1
                log(f"  ✗ ошибка: {result.error}")
        skipped_note = f", пропущено по списку: {len(skipped)}" if skip else ""
        if not apply:
            return (f"Групп: {len(groups)}, файлов на удаление: {counts.get('delete', 0)}{skipped_note} "
                    f"(только отчёт)")
        return f"Групп выполнено: {ok}, ошибок: {fail}{skipped_note}"
