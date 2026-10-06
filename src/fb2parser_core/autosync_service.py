"""Автосинхронизация папки наблюдения — см. docs/watch-folder-autosync-design.md.

- `preview` — отчёт «что бы я сделал», без побочных эффектов: найти порции,
  отложить ещё качающиеся, прогнать regen, проголосовать по жанру.
- `run` — то же по режиму из настроек: `dry_run` только пишет журнал,
  `auto` переписывает <genre> уверенных книг и синхронизирует их обычной
  синхронизацией (с автокомпиляцией); спорные книги остаются в папке
  наблюдения. Всё — под общей с ручной синхронизацией блокировкой.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .genre_voting import BookEvidence, GenreVoter, UnitDecision
from .library_memory import LibraryMemory, is_book_file, read_evidence
from .settings_manager import SettingsManager
from .sync_lock import SyncBusy, sync_lock

_log = logging.getLogger(__name__)

MODE_OFF, MODE_DRY_RUN, MODE_AUTO = "off", "dry_run", "auto"
MODE_MANUAL = "manual"  # решения человека во «Входящих» (в журнале)

# Итог запуска
RUN_OFF = "off"
RUN_BUSY = "busy"      # идёт ручная синхронизация — ждём следующего запуска
RUN_DONE = "done"
RUN_ERROR = "error"

# Судьба книги в журнале
OUTCOME_MOVED = "moved"            # уехала в библиотеку
OUTCOME_WOULD_MOVE = "would_move"  # пробный режим: уехала бы
OUTCOME_PENDING = "pending"        # жанр не решён — ждёт человека
OUTCOME_KEPT = "kept"              # жанр решён, но синхронизация оставила на месте (сверка автора, конфликт жанра серии, ошибка)
OUTCOME_REMOVED = "removed"        # синхронизация удалила как дубликат

KEEP_RUNS = 50

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


@dataclass
class RunResult:
    mode: str
    status: str
    run_id: Optional[int] = None
    reports: List[BatchReport] = field(default_factory=list)
    moved: List[Dict[str, Any]] = field(default_factory=list)
    pending: int = 0
    kept: int = 0
    removed: int = 0
    would_move: int = 0  # пробный режим: столько уехало бы
    error: str = ""
    # заметки синхронизации, требующие человека (сверка автора, конфликт жанра серии)
    notes: Dict[str, list] = field(default_factory=dict)


_JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS autosync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT, finished TEXT, mode TEXT, status TEXT, summary TEXT
);
CREATE TABLE IF NOT EXISTS autosync_books (
    run_id INTEGER, batch TEXT, source TEXT, library_path TEXT,
    author TEXT, series TEXT, title TEXT, genre TEXT,
    outcome TEXT, blocker TEXT, share REAL, signals TEXT
);
CREATE INDEX IF NOT EXISTS autosync_books_run ON autosync_books(run_id);
CREATE TABLE IF NOT EXISTS autosync_state (key TEXT PRIMARY KEY, value TEXT);
"""


class AutosyncJournal:
    """Журнал запусков (в файле памяти): что решено, что куда уехало, что ждёт."""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        with self._connect() as conn:
            conn.executescript(_JOURNAL_SCHEMA)
            try:  # журналы этапа 2 — без отметки «объявлено в канале»
                conn.execute("ALTER TABLE autosync_runs ADD COLUMN announced INTEGER DEFAULT 0")
            except sqlite3.OperationalError:
                pass

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=30)

    def start_run(self, mode: str) -> int:
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO autosync_runs(started, mode, status) VALUES (?, ?, 'running')",
                               (datetime.now().isoformat(timespec="seconds"), mode))
            return int(cur.lastrowid or 0)

    def add_books(self, run_id: int, rows: List[Dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.executemany(
                "INSERT INTO autosync_books(run_id, batch, source, library_path, author, series, title, genre, "
                "outcome, blocker, share, signals) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(run_id, r.get("batch", ""), r.get("source", ""), r.get("library_path", ""), r.get("author", ""),
                  r.get("series", ""), r.get("title", ""), r.get("genre") or "", r["outcome"],
                  r.get("blocker", ""), r.get("share", 0.0), json.dumps(r.get("signals", {}), ensure_ascii=False))
                 for r in rows])

    def finish_run(self, run_id: int, status: str, summary: Dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE autosync_runs SET finished = ?, status = ?, summary = ? WHERE id = ?",
                         (datetime.now().isoformat(timespec="seconds"), status,
                          json.dumps(summary, ensure_ascii=False, default=str), run_id))
            conn.execute("DELETE FROM autosync_books WHERE run_id NOT IN "
                         "(SELECT id FROM autosync_runs ORDER BY id DESC LIMIT ?)", (KEEP_RUNS,))
            conn.execute("DELETE FROM autosync_runs WHERE id NOT IN "
                         "(SELECT id FROM autosync_runs ORDER BY id DESC LIMIT ?)", (KEEP_RUNS,))

    def books(self, run_id: int, outcome: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM autosync_books WHERE run_id = ?" + (" AND outcome = ?" if outcome else "")
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, (run_id, outcome) if outcome else (run_id,))]

    def unannounced(self) -> tuple:
        """Перемещённые книги завершённых запусков, ещё не объявленные в
        канале, и id этих запусков (включая запуски без перемещений)."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            run_ids = [r["id"] for r in conn.execute(
                "SELECT id FROM autosync_runs WHERE finished IS NOT NULL AND COALESCE(announced, 0) = 0")]
            if not run_ids:
                return [], []
            marks = ", ".join("?" * len(run_ids))
            rows = [dict(r) for r in conn.execute(
                f"SELECT * FROM autosync_books WHERE outcome = ? AND run_id IN ({marks})",
                (OUTCOME_MOVED, *run_ids))]
        return rows, run_ids

    def mark_announced(self, run_ids: List[int]) -> None:
        with self._connect() as conn:
            conn.executemany("UPDATE autosync_runs SET announced = 1 WHERE id = ?", [(i,) for i in run_ids])

    def get_state(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM autosync_state WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO autosync_state(key, value) VALUES (?, ?)", (key, value))

    def last_run(self, modes: Optional[tuple] = None) -> Optional[Dict[str, Any]]:
        sql, args = "SELECT * FROM autosync_runs", ()
        if modes:
            sql += f" WHERE mode IN ({', '.join('?' * len(modes))})"
            args = tuple(modes)
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
            return dict(row) if row else None


@dataclass
class InboxUnit:
    """Единица во «Входящих»: книги одной серии (или одна книга) порции."""
    batch: str
    author: str
    series: str
    genre: Optional[str]   # подсказка
    share: float
    blocker: str
    signals: Dict[str, float]
    files: List[str] = field(default_factory=list)

    @property
    def uid(self) -> str:
        """Устойчивый id для формы: те же книги — тот же id между показом и отправкой."""
        key = "\x1f".join([self.batch, self.author, self.series, *sorted(self.files)])
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


@dataclass
class InboxBatch:
    name: str
    units: List[InboxUnit] = field(default_factory=list)

    @property
    def books(self) -> int:
        return sum(len(u.files) for u in self.units)


def _signals_json(d: UnitDecision) -> Dict[str, float]:
    return {f"{k}:{g}": round(w, 2) for (k, g), w in d.signals.items()}


class AutosyncService:
    def __init__(self, config_path: str, memory: LibraryMemory, genres_manager,
                 now: Callable[[], float] = time.time,
                 lock_path: Optional[str] = None,
                 sync_factory: Optional[Callable[[], Any]] = None,
                 compile_fn: Optional[Callable[..., dict]] = None):
        self.config_path = config_path
        self.settings = SettingsManager(config_path)
        self.cfg = self.settings.get_autosync_settings()
        self.memory = memory
        self.gm = genres_manager
        self.now = now
        self.lock_path = lock_path or os.path.join(os.path.dirname(os.path.abspath(memory.db_path)), "sync.lock")
        self.journal = AutosyncJournal(memory.db_path)
        self._sync_factory = sync_factory
        self._compile_fn = compile_fn

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

    # ---------- запуск ----------
    def run(self, progress: Optional[Callable[[str], None]] = None) -> RunResult:
        """Обработать папку наблюдения согласно режиму из настроек."""
        mode = self.cfg["mode"]
        if mode not in (MODE_DRY_RUN, MODE_AUTO):
            return RunResult(mode, RUN_OFF)
        try:
            with sync_lock(self.lock_path, "autosync"):
                return self._run_locked(mode, progress)
        except SyncBusy as busy:
            return RunResult(mode, RUN_BUSY, error=f"синхронизацию сейчас выполняет: {busy.owner}")

    def _run_locked(self, mode: str, progress) -> RunResult:
        run_id = self.journal.start_run(mode)
        result = RunResult(mode, RUN_DONE, run_id=run_id)
        try:
            folder = str(Path(self.cfg["watch_folder"]).resolve()) if self.cfg["watch_folder"] else ""
            result.reports = self.preview(folder, progress)
            ready = [r for r in result.reports if r.decisions]
            if mode == MODE_AUTO and any(d.auto for r in ready for d in r.decisions):
                rows = self._apply(folder, ready, result, progress)
            else:
                rows = self._decision_rows(ready, MODE_DRY_RUN if mode == MODE_DRY_RUN else MODE_AUTO)
                result.pending = sum(1 for r in rows if r["outcome"] == OUTCOME_PENDING)
                result.would_move = sum(1 for r in rows if r["outcome"] == OUTCOME_WOULD_MOVE)
            self.journal.add_books(run_id, rows)
        except Exception as e:  # журнал фиксирует сбой; уведомление админу — по result.status
            _log.exception("autosync: сбой запуска")
            result.status, result.error = RUN_ERROR, str(e)
        self.journal.finish_run(run_id, result.status, {
            "moved": len(result.moved), "pending": result.pending, "kept": result.kept,
            "removed": result.removed, "would_move": result.would_move, "error": result.error,
            "batches": {r.name: r.status for r in result.reports},
            "notes": result.notes,
        })
        return result

    @staticmethod
    def _decision_rows(ready: List[BatchReport], mode: str) -> List[Dict[str, Any]]:
        rows = []
        for r in ready:
            for d in r.decisions:
                outcome = OUTCOME_WOULD_MOVE if d.auto and mode == MODE_DRY_RUN else OUTCOME_PENDING
                for f in d.files:
                    rows.append({"batch": r.name, "source": f, "author": d.author, "series": d.series,
                                 "genre": d.genre, "outcome": outcome, "blocker": d.blocker,
                                 "share": d.share, "signals": _signals_json(d)})
        return rows

    def _make_assigner(self, root: Path, decided_by: str):
        from .genre_assign import GenreAssignmentService

        def record(fb2_path, content, _batch):
            batch = Path(fb2_path).resolve().relative_to(root).parts[0]
            self.memory.record_from_text(content, batch, decided_by)

        return GenreAssignmentService(logger=_LogAdapter(), codes_recorder=record)

    def _apply(self, folder: str, ready: List[BatchReport], result: RunResult, progress) -> List[Dict[str, Any]]:
        """Переписать жанр уверенных книг и синхронизировать их; спорные — оставить."""
        root = Path(folder)
        items: List[Dict[str, Any]] = []
        by_genre: Dict[str, List[str]] = defaultdict(list)
        hold: set = set()
        batches_to_sync = set()
        for r in ready:
            for d in r.decisions:
                for f in d.files:
                    items.append({"batch": r.name, "source": f, "author": d.author, "series": d.series,
                                  "genre": d.genre, "blocker": d.blocker, "share": d.share,
                                  "signals": _signals_json(d), "decided": d.auto})
                if d.auto and d.genre:
                    by_genre[d.genre].extend(d.files)
                    batches_to_sync.add(r.path)
                else:
                    hold.update(d.files)
        stats, failed = self._assign_and_sync(root, by_genre, hold, batches_to_sync, "auto", progress)
        return self._outcome_rows(root, items, stats, failed, result)

    def _assign_and_sync(self, root: Path, by_genre: Dict[str, List[str]], hold: set, batches: set,
                         decided_by: str, progress) -> tuple:
        """Жанр в файлы → обычная синхронизация порций → автокомпиляция.

        Файлы — пути относительно root (как record.file_path у regen); hold —
        файлы порций, которые остаются на месте. Возвращает (статистика
        синхронизации, файлы, которым не удалось записать жанр).
        """
        # 1) Жанр в файлы. Исходные коды запоминаются перед переписыванием.
        assigner = self._make_assigner(root, decided_by)
        failed = set()
        for genre, files in by_genre.items():
            done = assigner.assign_genre_to_files([str(root / f) for f in files], genre)
            failed |= {f for f in files if not done.get(str(root / f))}

        # 2) Обычная синхронизация порций.
        if progress:
            progress(f"Синхронизация {len(batches)} порций…")
        if self._sync_factory:
            sync = self._sync_factory()
        else:
            from .synchronization import SynchronizationService
            sync = SynchronizationService(self.config_path)
        sync.last_scan_path = root
        stats = sync.synchronize(log_callback=_log.info, allowed_folders=batches, hold_files=hold | failed) or {}

        # 3) Автокомпиляция серий у затронутых авторов — как в ручной синхронизации.
        touched = stats.get("touched_author_dirs") or set()
        if stats.get("files_moved") and touched:
            if progress:
                progress(f"Автокомпиляция ({len(touched)} авторов)…")
            compile_fn = self._compile_fn
            if compile_fn is None:
                from .auto_compile_service import auto_compile_library as compile_fn
            try:
                compile_fn(str(sync.library_path), config_path=self.config_path, filter_paths=touched)
            except Exception:
                _log.exception("autosync: сбой автокомпиляции")
        return stats, failed

    @staticmethod
    def _outcome_rows(root: Path, items: List[Dict[str, Any]], stats: dict, failed: set,
                      result: RunResult) -> List[Dict[str, Any]]:
        """Строки журнала: судьба каждой книги после синхронизации."""
        result.notes = {k: stats.get(k) or [] for k in ("reconciliation_notes", "genre_conflict_notes")}
        moved = {m["source"]: m for m in stats.get("moved_books") or []}
        rows = []
        for item in items:
            row = {k: v for k, v in item.items() if k != "decided"}
            f = item["source"]
            m = moved.get(f)
            if m:
                row.update(outcome=OUTCOME_MOVED, library_path=m["path"], title=m.get("title") or "",
                           author=m.get("author") or item["author"], series=m.get("series") or item["series"])
                result.moved.append(row)
            elif not item["decided"]:
                row["outcome"] = OUTCOME_PENDING
                result.pending += 1
            elif (root / f).exists():
                row.update(outcome=OUTCOME_KEPT, blocker="assign_failed" if f in failed else "sync_kept")
                result.kept += 1
            else:
                row["outcome"] = OUTCOME_REMOVED
                result.removed += 1
            rows.append(row)
        return rows

    # ---------- «Входящие» ----------
    def inbox(self) -> List[InboxBatch]:
        """Книги последнего запуска (авто или пробного), ждущие человека и
        всё ещё лежащие в папке наблюдения, — по порциям и единицам."""
        folder = self.cfg["watch_folder"]
        run = self.journal.last_run(modes=(MODE_DRY_RUN, MODE_AUTO))
        if not folder or not run:
            return []
        root = Path(folder).resolve()
        units: Dict[tuple, InboxUnit] = {}
        for row in self.journal.books(run["id"]):
            if row["outcome"] not in (OUTCOME_PENDING, OUTCOME_KEPT):
                continue
            if not (root / row["source"]).exists():
                continue  # уже решено (перемещено) или убрано вручную
            key = (row["batch"], row["author"], row["series"], row["genre"], row["blocker"])
            unit = units.get(key)
            if unit is None:
                unit = units[key] = InboxUnit(batch=row["batch"], author=row["author"], series=row["series"],
                                              genre=row["genre"], share=row["share"] or 0.0,
                                              blocker=row["blocker"], signals=json.loads(row["signals"] or "{}"))
            unit.files.append(row["source"])
        batches: Dict[str, InboxBatch] = {}
        for unit in units.values():
            unit.files.sort()
            batches.setdefault(unit.batch, InboxBatch(unit.batch)).units.append(unit)
        for b in batches.values():
            b.units.sort(key=lambda u: (-len(u.files), u.author, u.series))
        return sorted(batches.values(), key=lambda b: b.name.lower())

    def decide(self, batch: str, choices: Dict[str, str],
               progress: Optional[Callable[[str], None]] = None) -> RunResult:
        """Решения человека по порции из «Входящих»: {id единицы: жанр}.
        Выбранные единицы получают жанр и синхронизируются; остальные книги
        порции остаются на месте."""
        result = RunResult(MODE_MANUAL, RUN_DONE)
        inbox = {b.name: b for b in self.inbox()}
        if batch not in inbox:
            result.status, result.error = RUN_ERROR, f"порция «{batch}» не найдена во «Входящих»"
            return result
        root = Path(self.cfg["watch_folder"]).resolve()
        units = {u.uid: u for u in inbox[batch].units}
        chosen = {uid: g for uid, g in choices.items() if g and uid in units}
        if not chosen:
            result.status, result.error = RUN_ERROR, "не выбран ни один жанр"
            return result
        try:
            with sync_lock(self.lock_path, "inbox"):
                result.run_id = self.journal.start_run(MODE_MANUAL)
                by_genre: Dict[str, List[str]] = defaultdict(list)
                items: List[Dict[str, Any]] = []
                for uid, genre in chosen.items():
                    u = units[uid]
                    by_genre[genre].extend(u.files)
                    items += [{"batch": batch, "source": f, "author": u.author, "series": u.series, "genre": genre,
                               "blocker": "", "share": u.share, "signals": u.signals, "decided": True}
                              for f in u.files]
                decided = {i["source"] for i in items}
                batch_dir = root / batch
                hold = {os.path.relpath(os.path.join(d, n), root)
                        for d, _s, names in os.walk(batch_dir) for n in names if is_book_file(n)} - decided
                stats, failed = self._assign_and_sync(root, by_genre, hold, {str(batch_dir)}, "user", progress)
                rows = self._outcome_rows(root, items, stats, failed, result)
                self.journal.add_books(result.run_id, rows)
                self.journal.finish_run(result.run_id, result.status, {
                    "batch": batch, "moved": len(result.moved), "kept": result.kept,
                    "removed": result.removed, "notes": result.notes})
        except SyncBusy as busy:
            result.status, result.error = RUN_BUSY, f"синхронизацию сейчас выполняет: {busy.owner}"
        return result


def notify(service: "AutosyncService", result: RunResult,
           link: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None,
           client=None) -> Dict[str, str]:
    """Telegram после планового запуска (и скана библиотеки): новинки — в
    канал, итог — админу. Ошибки Telegram не пробрасываются: их видно в
    логе и в возвращаемом {адресат: "ok" | причина}."""
    from .telegram_notify import (
        TelegramClient,
        TelegramError,
        build_admin_report,
        build_digest,
        explain_error,
    )

    cfg = service.cfg
    if not cfg["telegram_token"] or result.status in (RUN_OFF, RUN_BUSY):
        return {}
    client = client or TelegramClient(cfg["telegram_token"], cfg["telegram_proxy"])
    sent: Dict[str, str] = {}
    journal = service.journal

    # Канал: всё перемещённое и ещё не объявленное (в т.ч. решения из «Входящих»).
    if cfg["telegram_channel"]:
        rows, run_ids = journal.unannounced()
        try:
            if rows:
                client.send_message(cfg["telegram_channel"], build_digest(rows, link))
                sent["channel"] = "ok"
            journal.mark_announced(run_ids)
        except TelegramError as e:
            sent["channel"] = explain_error(e)
            _log.warning("autosync: сводка в канал не отправлена: %s", e.description)

    # Админ: только когда есть что сказать — иначе каждую ночь одно и то же.
    if cfg["telegram_admin_chat"]:
        pending = [{"batch": b.name, "books": b.books} for b in service.inbox()]
        signature = json.dumps(sorted((p["batch"], p["books"]) for p in pending), ensure_ascii=False)
        notes = result.notes or {}
        worth = (result.status == RUN_ERROR or result.moved or result.kept or result.would_move
                 or any(notes.values()) or signature != journal.get_state("admin_pending"))
        if worth:
            base = (cfg["public_url"] or "").rstrip("/")
            summary = {"status": result.status, "mode": result.mode, "moved": len(result.moved),
                       "kept": result.kept, "removed": result.removed, "would_move": result.would_move,
                       "error": result.error, "notes": notes}
            try:
                client.send_message(cfg["telegram_admin_chat"],
                                    build_admin_report(summary, pending, f"{base}/fb2parser/inbox/" if base else ""))
                journal.set_state("admin_pending", signature)
                sent["admin"] = "ok"
            except TelegramError as e:
                sent["admin"] = explain_error(e)
                _log.warning("autosync: отчёт админу не отправлен: %s", e.description)
    return sent


class _LogAdapter:
    """Logger-интерфейс GenreAssignmentService поверх logging."""

    def log(self, msg, *_a, **_k):
        _log.info(msg)
