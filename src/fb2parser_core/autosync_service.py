"""Автосинхронизация папки наблюдения — см. docs/watch-folder-autosync-design.md.

Этап 1: только отчёт «что бы я сделал» (`preview`) — без побочных эффектов:
найти порции, отложить ещё качающиеся, прогнать regen, проголосовать по
жанру. Перемещение и уведомления — следующие этапы.
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .genre_voting import BookEvidence, GenreVoter, UnitDecision
from .library_memory import LibraryMemory, is_book_file, read_evidence
from .settings_manager import SettingsManager

_log = logging.getLogger(__name__)

# Недокачанные файлы торрент-клиентов и браузеров.
PARTIAL_SUFFIXES = (".part", ".partial", ".!qb", ".!ut", ".crdownload", ".download", ".tmp")

STATUS_READY = "ready"
STATUS_DOWNLOADING = "downloading"
STATUS_EMPTY = "empty"
STATUS_LOOSE = "loose"  # файлы прямо в корне папки наблюдения
STATUS_NO_RECORDS = "no_records"  # regen не вернул по порции ни одной книги


@dataclass
class BatchReport:
    name: str
    path: str
    status: str
    books: int = 0
    decisions: List[UnitDecision] = field(default_factory=list)

    @property
    def auto_books(self) -> int:
        return sum(len(d.files) for d in self.decisions if d.auto)

    @property
    def pending_books(self) -> int:
        return sum(len(d.files) for d in self.decisions if not d.auto)


class AutosyncService:
    def __init__(self, config_path: str, memory: LibraryMemory, genres_manager,
                 now: Callable[[], float] = time.time):
        self.config_path = config_path
        self.settings = SettingsManager(config_path)
        self.cfg = self.settings.get_autosync_settings()
        self.memory = memory
        self.gm = genres_manager
        self.now = now

    # ---------- порции ----------
    def _is_quiet(self, path: Path) -> bool:
        """Порция готова: нет недокачанных файлов и ничего не менялось
        последние quiet_minutes."""
        limit = self.now() - float(self.cfg["quiet_minutes"]) * 60
        for dirpath, _dirs, names in os.walk(path):
            for name in names:
                if name.lower().endswith(PARTIAL_SUFFIXES):
                    return False
                try:
                    if os.stat(os.path.join(dirpath, name)).st_mtime > limit:
                        return False
                except OSError as e:  # напр. путь длиннее MAX_PATH на Windows
                    _log.warning("autosync: нет доступа к %s: %s", name, e)
        return True

    @staticmethod
    def _count_books(path: Path) -> int:
        return sum(1 for _d, _s, names in os.walk(path) for n in names if is_book_file(n))

    def discover_batches(self, watch_folder: str) -> List[BatchReport]:
        root = Path(watch_folder)
        reports: List[BatchReport] = []
        loose = 0
        for entry in sorted(root.iterdir(), key=lambda p: p.name.lower()):
            if entry.is_file():
                loose += is_book_file(entry.name)
                continue
            n = self._count_books(entry)
            if not n:
                status = STATUS_EMPTY
            elif not self._is_quiet(entry):
                status = STATUS_DOWNLOADING
            else:
                status = STATUS_READY
            reports.append(BatchReport(entry.name, str(entry), status, n))
        if loose:
            reports.append(BatchReport("", str(root), STATUS_LOOSE, loose))
        return reports

    # ---------- голосование ----------
    def build_voter(self, progress: Optional[Callable[[str], None]] = None) -> GenreVoter:
        library = self.settings.get_library_path()
        if library and os.path.isdir(library):
            stats = self.memory.refresh(library)
            if progress:
                progress(f"Память библиотеки: {stats['total']} книг, обновлено {stats['updated']}, "
                         f"удалено {stats['removed']}")
        priority = self.settings.get_genre_priority_order()
        return GenreVoter(
            self.memory.build(),
            resolve_code=lambda c: self.gm.resolve_code(c, priority)[0],
            genre_names=self.gm.all_genre_names(),
            excluded_codes=self.gm.excluded_codes,
            confidence=float(self.cfg["confidence"]),
        )

    def preview(self, watch_folder: Optional[str] = None,
                progress: Optional[Callable[[str], None]] = None) -> List[BatchReport]:
        """Отчёт по порциям папки наблюдения; файлы не трогаются."""
        from .regen_csv import RegenCSVService

        folder = watch_folder or self.cfg["watch_folder"]
        if not folder or not os.path.isdir(folder):
            raise FileNotFoundError(f"Папка наблюдения не найдена: {folder!r}")
        # Полный путь: regen сравнивает файлы с filter_paths после resolve(),
        # а короткая форма Windows (DMITRI~1) с полной не совпадает.
        folder = str(Path(folder).resolve())
        reports = self.discover_batches(folder)
        ready = [r for r in reports if r.status == STATUS_READY]
        if not ready:
            return reports
        voter = self.build_voter(progress)
        if progress:
            progress(f"Разбор {len(ready)} порций (regen)…")
        records = RegenCSVService(self.config_path).generate_csv(
            folder, output_csv_path=None,
            filter_paths={Path(r.path).resolve() for r in ready},
        ) or []
        by_batch: Dict[str, List[BookEvidence]] = defaultdict(list)
        root = Path(folder)
        for rec in records:
            if getattr(rec, "delete_flag", False):
                continue
            parts = Path(rec.file_path).parts
            if len(parts) < 2:
                continue
            ev = read_evidence(root / rec.file_path)
            by_batch[parts[0]].append(BookEvidence(
                file_path=rec.file_path,
                author=rec.proposed_author or "",
                series=rec.proposed_series or "",
                codes=[c.strip() for c in (rec.metadata_genre or "").split(",") if c.strip()],
                pubseq=ev.pubseq,
            ))
        for r in ready:
            books = by_batch.get(r.name, [])
            if not books:
                r.status = STATUS_NO_RECORDS
                continue
            r.decisions = voter.decide_batch(books)
        return reports
