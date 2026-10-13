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

        sync_job.update(pid=os.getpid())
        _run_sync_thread()
