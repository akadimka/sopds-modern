"""Сжатие библиотеки, запущенное кнопкой «Сжать», — в своём процессе с
низким приоритетом (баг №159).

Запускается веб-интерфейсом (fb2parser_web.views._spawn_compress_process)
под `nice -n 19 ionice -c3`. Прогресс, результат и «Стоп» — в общем кэше
(fb2parser_web.job_state), как и прежде.

Вручную (тот же приоритет — его задаёт запускающий):

    nice -n 19 ionice -c3 python manage.py fb2parser_compress --library
"""
import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Сжать .fb2 библиотеки в .fb2.zip (служебная команда кнопки «Сжать»; --library — вручную)."

    def add_arguments(self, parser):
        parser.add_argument("--library", action="store_true",
                            help="запустить вручную по библиотеке из настроек (без кнопки в веб-интерфейсе)")

    def handle(self, *args, **options):
        from fb2parser_web.views import (
            _run_compress_thread,
            compress_job,
            compress_stop_flag,
        )

        if options["library"]:
            from fb2parser_core.settings_manager import SettingsManager
            from fb2parser_web.fb2parser_bridge import _config_path

            library_path = SettingsManager(_config_path()).get_library_path()
            compress_stop_flag.clear()
            if not compress_job.try_start(library_path=library_path):
                self.stderr.write("Сжатие уже идёт.")
                return
        else:
            state = compress_job.get()
            library_path = state.get("library_path")
            if not state.get("running") or not library_path:
                self.stderr.write("fb2parser_compress: no running compress job in the shared cache — nothing to do.")
                return
        compress_job.update(pid=os.getpid())
        _run_compress_thread(library_path)
        state = compress_job.get()
        self.stdout.write(f"Сжато: {state.get('compressed')}, из {state.get('total')}, "
                          f"освобождено байт: {state.get('bytes_saved')}, ошибок: {len(state.get('errors') or [])}"
                          + (f", ошибка: {state['error']}" if state.get("error") else ""))
