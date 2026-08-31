"""Management command: sopds_watch

Демон реального времени для инкрементального обновления OPDS-каталога.

В отличие от sopds_scanner (полный os.walk() по всей библиотеке на каждый
прогон — не масштабируется на десятки тысяч файлов), sopds_watch:
  1. Один раз при старте делает полный scan_all() — переживает даунтайм
     демона (перезапуски/деплои), после которого что-то могло измениться
     без нашего ведома.
  2. Дальше следит за библиотекой через inotify (watchdog) и точечно
     пересобирает только реально изменившиеся папки, без полного обхода.

Ночной sopds-scan.service остаётся как есть — дешёвая страховка на случай
пропущенных событий (переполнение очереди inotify при экстремальном
всплеске, время простоя самого демона и т.п.), не заменяется этим демоном.

Запуск (аналогично sopds_scanner — под systemd, БЕЗ демонизации):
    python manage.py sopds_watch
"""
import os
import threading
import time

from django.core.management.base import BaseCommand
from django.db import connection, connections, transaction

from opds_catalog import opdsdb
from opds_catalog.sopds_config import sopds_cfg as config


def _dedup_nested(dirs) -> list:
    """Убрать из набора папки, вложенные в другие папки того же набора —
    их всё равно пересоберёт обход родительской папки."""
    ordered = sorted(dirs, key=len)
    result: list = []
    for d in ordered:
        if not any(d == kept or d.startswith(kept + os.sep) for kept in result):
            result.append(d)
    return result


class LibraryDirtyTracker:
    """Копит "грязные" (изменившиеся) папки библиотеки между вызовами take_ready().

    Обработчики событий watchdog вызываются в отдельном потоке observer'а и
    не должны трогать БД напрямую (см. docstring модуля) — они только
    отмечают путь как грязный. Реальная пересборка происходит в отдельном
    потоке-воркере, батчем, после того как поток событий "утихнет".
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._dirty: set[str] = set()
        self._last_event = 0.0

    def mark(self, path: str, is_directory: bool) -> None:
        d = path if is_directory else os.path.dirname(path)
        with self._lock:
            self._dirty.add(d)
            self._last_event = time.monotonic()

    def take_ready(self, quiet_seconds: float):
        """Вернуть накопленные грязные папки, если события не приходили
        последние quiet_seconds — иначе None (ещё не "утихло")."""
        with self._lock:
            if self._dirty and time.monotonic() - self._last_event >= quiet_seconds:
                snapshot, self._dirty = self._dirty, set()
                return snapshot
            return None


def _make_event_handler(tracker: LibraryDirtyTracker):
    from watchdog.events import FileSystemEventHandler

    class _LibraryEventHandler(FileSystemEventHandler):
        def on_created(self, event):
            tracker.mark(event.src_path, event.is_directory)

        def on_modified(self, event):
            tracker.mark(event.src_path, event.is_directory)

        def on_deleted(self, event):
            tracker.mark(event.src_path, event.is_directory)

        def on_moved(self, event):
            tracker.mark(event.src_path, event.is_directory)
            tracker.mark(event.dest_path, event.is_directory)

    return _LibraryEventHandler()


class Command(BaseCommand):
    help = "Real-time incremental library watcher (inotify) — replaces periodic full scans."

    def handle(self, *args, **options):
        from opds_catalog.sopdscan import opdsScanner
        from watchdog.observers import Observer

        scanner = opdsScanner()

        self.stdout.write(f"Startup: full reconciliation scan of {config.SOPDS_ROOT_LIB} ...")
        self._reconnect_if_needed()
        scanner.scan_all()
        self.stdout.write(
            f"Startup scan complete: added={scanner.books_added} "
            f"deleted={scanner.books_deleted} skipped={scanner.books_skipped}"
        )

        tracker = LibraryDirtyTracker()
        observer = Observer()
        observer.schedule(_make_event_handler(tracker), config.SOPDS_ROOT_LIB, recursive=True)
        observer.start()
        self.stdout.write(f"Watching {config.SOPDS_ROOT_LIB} for changes...")

        try:
            self._flush_loop(tracker, scanner)
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            observer.stop()
            observer.join()

    def _reconnect_if_needed(self) -> None:
        # Тот же приём, что и в sopds_scanner.py: долгоживущий процесс может
        # унаследовать неиспользуемое соединение к БД.
        if connection.connection and not connection.is_usable():
            del connections._connections.default

    def _flush_loop(self, tracker: LibraryDirtyTracker, scanner) -> None:
        while True:
            time.sleep(2)
            quiet_seconds = config.SOPDS_WATCH_DEBOUNCE_SECONDS
            dirty = tracker.take_ready(quiet_seconds)
            if not dirty:
                continue
            self._flush(_dedup_nested(dirty), scanner)

    def _flush(self, dirs: list, scanner) -> None:
        self._reconnect_if_needed()
        self.stdout.write(f"Rescanning {len(dirs)} changed folder(s)...")
        with transaction.atomic():
            for d in dirs:
                if not os.path.isdir(d):
                    # Папка целиком исчезла (переименована/удалена) — всё
                    # равно нужно пройти books_del_phisical_scoped ниже,
                    # scan_path() по несуществующему пути просто ничего не найдёт.
                    self.stdout.write(f"  {d} (папка больше не существует)")
                else:
                    self.stdout.write(f"  {d}")
                rel_path = os.path.relpath(d, config.SOPDS_ROOT_LIB)
                opdsdb.avail_check_prepare_scoped(rel_path)
                if os.path.isdir(d):
                    scanner.scan_path(d)
                opdsdb.books_del_phisical_scoped(rel_path)
            opdsdb.cleanup_orphan_entities()
