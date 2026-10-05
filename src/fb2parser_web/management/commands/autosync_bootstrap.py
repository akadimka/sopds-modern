"""Разовая загрузка исходных кодов <genre> в память автосинхронизации.

    python manage.py autosync_bootstrap --sources ПАПКА_ИСХОДНИКОВ

В книгах библиотеки тег <genre> уже переписан именем жанра — выучить
«код → жанр» не из чего. Если исходники (скачанные папки) сохранились,
команда находит для книг библиотеки их исходники по отпечатку текста и
запоминает исходные коды. Порция — подпапка верхнего уровня ПАПКИ_ИСХОДНИКОВ.
См. docs/watch-folder-autosync-design.md, «Память».
"""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Восстановить исходные коды жанров книг библиотеки по сохранившимся исходникам."

    def add_arguments(self, parser):
        parser.add_argument("--sources", required=True, help="Папка со скачанными исходниками.")

    def handle(self, *args, **options):
        from fb2parser_core.library_memory import is_book_file, read_evidence
        from fb2parser_core.settings_manager import SettingsManager
        from fb2parser_web.fb2parser_bridge import _config_path, get_library_memory

        sources = Path(options["sources"])
        if not sources.is_dir():
            raise CommandError(f"Папка не найдена: {sources}")
        library = SettingsManager(_config_path()).get_library_path()
        if not library or not os.path.isdir(library):
            raise CommandError(f"Библиотека не найдена: {library!r}")

        memory = get_library_memory()
        stats = memory.refresh(library)
        self.stdout.write(f"Библиотека: {stats['total']} книг (обновлено {stats['updated']})")
        known = memory.library_fingerprints()

        files = [Path(d) / n for d, _s, names in os.walk(sources) for n in names if is_book_file(n)]
        self.stdout.write(f"Исходников: {len(files)}")
        matched = recorded = 0
        with ThreadPoolExecutor(8) as pool:
            for path, ev in zip(files, pool.map(read_evidence, files)):
                if ev.fingerprint not in known:
                    continue
                matched += 1
                batch = path.relative_to(sources).parts[0]
                recorded += memory.record_orig_codes(ev.fingerprint, ev.codes, batch, "bootstrap")
        self.stdout.write(f"Найдено в библиотеке: {matched}, записано исходных кодов: {recorded}")
