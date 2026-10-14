"""Синхронизация библиотеки, запущенная из окна «Синхронизация», — в своём
процессе (баг №144).

Запускается веб-интерфейсом (fb2parser_web.views._spawn_sync_process), не
вручную. Раньше синхронизация шла потоком внутри рабочего процесса gunicorn:
долгая автокомпиляция не давала ему вовремя отвечать, gunicorn убивал
процесс по WORKER TIMEOUT вместе с синхронизацией, а состояние «идёт»
оставалось в кэше на часы, и «Стоп» было некому получить. Параметры,
прогресс, журнал и флаг «Стоп» — в общем кэше (fb2parser_web.job_state),
как и прежде.
"""
import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Выполнить синхронизацию, запрошенную из веб-интерфейса (служебная команда)."

    def handle(self, *args, **options):
        from fb2parser_web.views import _run_sync_thread, sync_job

        state = sync_job.get()
        if not state.get("running") or not state.get("allowed_folders"):
            # Баг №147: кэш этого процесса не общий с веб-сервером (LocMemCache)
            # или задача уже снята — параметров нет, синхронизировать нечего.
            self.stderr.write("fb2parser_sync_job: no running sync job with selected folders in the shared "
                              "cache — refusing to synchronize anything.")
            return
        sync_job.update(pid=os.getpid())
        _run_sync_thread()
