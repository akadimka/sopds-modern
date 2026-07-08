import logging
import os
import threading

from constance import config
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import render

from opds_catalog.models import Author, Book, Catalog, Counter, Genre, Series
from opds_catalog.sopdscan import opdsScanner

# ── Состояние сканирования (живёт в памяти процесса) ─────────────────────────
_scan_lock = threading.Lock()
_scan_state: dict = {
    "running": False,
    "done": False,
    "error": None,
    "processed": 0,
    "total": 0,
    "current": "",
    "books_added": 0,
    "books_skipped": 0,
    "bad_books": 0,
    "bad_list": [],   # [(rel_path/name, error_msg), ...]
}


class _ErrorCapture(logging.Handler):
    """Перехватывает ERROR-записи сканера и складывает их в _scan_state."""

    def emit(self, record):
        if record.levelno >= logging.ERROR:
            msg = self.format(record)
            with _scan_lock:
                if len(_scan_state["bad_list"]) < 200:
                    _scan_state["bad_list"].append(msg)


class _TrackingScanner(opdsScanner):
    """Сканер с перехватом processfile для обновления прогресса."""

    def processfile(self, name, full_path, file, cat, archive=0, file_size=0):
        with _scan_lock:
            _scan_state["processed"] += 1
            _scan_state["current"] = name
        super().processfile(name, full_path, file, cat, archive, file_size)


def _count_files(root_path):
    """Предварительный подсчёт файлов книг для прогресс-бара."""
    try:
        extensions = set(
            e.lstrip(".") for e in config.SOPDS_BOOK_EXTENSIONS.split()
        )
        total = 0
        for _, _, files in os.walk(root_path, followlinks=True):
            for f in files:
                ext = os.path.splitext(f)[1].lower().lstrip(".")
                if ext in extensions or ext == "zip":
                    total += 1
        return total
    except Exception:
        return 0


def _run_scan_thread(root_path):
    """Тело фонового потока сканирования."""
    from django import db

    db.connections.close_all()
    try:
        total = _count_files(root_path)
        with _scan_lock:
            _scan_state["total"] = total
            _scan_state["processed"] = 0
            _scan_state["current"] = ""

        scan_logger = logging.getLogger("fb2parser.scan")
        capture = _ErrorCapture()
        capture.setLevel(logging.ERROR)
        scan_logger.addHandler(capture)
        # also capture errors from the root scanner logger
        root_scan_logger = logging.getLogger("scanner")
        root_scan_logger.addHandler(capture)
        scanner = _TrackingScanner(scan_logger)
        scanner.scan_all()
        Counter.objects.update_known_counters()

        with _scan_lock:
            _scan_state["done"] = True
            _scan_state["running"] = False
            _scan_state["books_added"] = scanner.books_added
            _scan_state["books_skipped"] = scanner.books_skipped
            _scan_state["bad_books"] = scanner.bad_books
    except Exception as exc:
        with _scan_lock:
            _scan_state["error"] = str(exc)
            _scan_state["running"] = False
    finally:
        from django import db as _db
        _db.connections.close_all()


def _ctx(page_id, title, **kwargs):
    return {"fb2_page": page_id, "fb2_title": title, **kwargs}


@staff_member_required(login_url="/web/login/")
def dashboard(request):
    stats = {
        "books":   Book.objects.count(),
        "authors": Author.objects.count(),
        "genres":  Genre.objects.count(),
        "series":  Series.objects.count(),
        "catalogs": Catalog.objects.count(),
    }
    try:
        last_scan = Counter.objects.get(name="allbooks").update_time
    except Counter.DoesNotExist:
        last_scan = None

    # Топ-5 жанров по количеству книг
    from django.db.models import Count
    top_genres = (
        Genre.objects.annotate(cnt=Count("bgenre"))
        .order_by("-cnt")[:5]
    )
    # Последние 10 добавленных книг
    recent_books = Book.objects.order_by("-id")[:10]

    return render(request, "fb2parser/dashboard.html", _ctx(
        "dashboard", "Статистика",
        stats=stats,
        last_scan=last_scan,
        top_genres=top_genres,
        recent_books=recent_books,
    ))


@staff_member_required(login_url="/web/login/")
def scan(request):
    root = config.SOPDS_ROOT_LIB or ""
    with _scan_lock:
        state = dict(_scan_state)
    return render(request, "fb2parser/scan.html", _ctx("scan", "Сканирование", root=root, state=state))


@staff_member_required(login_url="/web/login/")
def scan_start(request):
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    with _scan_lock:
        if _scan_state["running"]:
            return _render_status(dict(_scan_state))
        root = request.POST.get("root", config.SOPDS_ROOT_LIB or "").strip()
        if not root or not os.path.isdir(root):
            return HttpResponse(
                f'<div id="scan-status"><div class="callout alert">❌ Папка не найдена: {root}</div></div>'
            )
        _scan_state.update({
            "running": True, "done": False, "error": None,
            "processed": 0, "total": 0, "current": "",
            "books_added": 0, "books_skipped": 0, "bad_books": 0,
            "bad_list": [],
        })

    t = threading.Thread(target=_run_scan_thread, args=(root,), daemon=True)
    t.start()
    return _render_status(dict(_scan_state))


@staff_member_required(login_url="/web/login/")
def scan_status(request):
    return _render_status(dict(_scan_state))


def _render_status(state):
    pct = 0
    if state["total"] > 0:
        pct = min(100, int(state["processed"] / state["total"] * 100))
    from django.template.loader import render_to_string
    html = render_to_string("fb2parser/scan_status.html", {"state": state, "pct": pct})
    return HttpResponse(html)


@staff_member_required(login_url="/web/login/")
def archive(request):
    return render(request, "fb2parser/archive.html", _ctx("archive", "Заархивировать"))


@staff_member_required(login_url="/web/login/")
def database(request):
    return render(request, "fb2parser/database.html", _ctx("database", "База данных"))


# ── Жанры ────────────────────────────────────────────────────────────────────

def _genre_tree_to_list(nodes, depth=0):
    """Рекурсивно сериализует дерево жанров в плоский список для шаблона."""
    result = []
    for node in nodes:
        result.append({
            "name": node.name,
            "depth": depth,
            "assigned": sorted(node.assigned),
            "has_children": bool(node.children),
        })
        result.extend(_genre_tree_to_list(node.children, depth + 1))
    return result


@staff_member_required(login_url="/web/login/")
def genres(request):
    from .fb2parser_bridge import get_genres_manager
    error = None
    genre_list = []
    try:
        gm = get_genres_manager()
        genre_list = _genre_tree_to_list(gm.root_nodes)
    except Exception as e:
        error = str(e)
    return render(request, "fb2parser/genres.html", _ctx(
        "genres", "Менеджер жанров",
        genre_list=genre_list,
        error=error,
    ))


# ── Присвоение жанра ─────────────────────────────────────────────────────────

_assign_lock = threading.Lock()
_assign_state: dict = {
    "running": False, "done": False, "error": None,
    "processed": 0, "total": 0, "current": "",
    "count": 0,
}


@staff_member_required(login_url="/web/login/")
def genre_assign(request):
    """Страница присвоения жанра папке."""
    from .fb2parser_bridge import get_genres_manager
    from constance import config as cfg
    error = None
    genre_names = []
    try:
        gm = get_genres_manager()
        genre_names = sorted(
            node.name
            for node in gm.root_nodes
            for node in _iter_all_nodes(node)
        )
    except Exception as e:
        error = str(e)

    root = cfg.SOPDS_ROOT_LIB or ""
    with _assign_lock:
        state = dict(_assign_state)
    return render(request, "fb2parser/genre_assign.html", _ctx(
        "genre_assign", "Присвоение жанра",
        genre_names=genre_names,
        root=root,
        state=state,
        error=error,
    ))


def _iter_all_nodes(node):
    yield node
    for child in node.children:
        yield from _iter_all_nodes(child)


def _run_assign_thread(folder_path, genre_name):
    from .fb2parser_bridge import get_genre_assignment_service
    from django import db
    db.connections.close_all()
    try:
        def on_progress(cur, tot, name):
            with _assign_lock:
                _assign_state.update({"processed": cur, "total": tot, "current": name})

        svc = get_genre_assignment_service()
        count = svc.assign_genre_to_folder(
            folder_path, genre_name,
            progress_callback=on_progress,
        )
        with _assign_lock:
            _assign_state.update({"done": True, "running": False, "count": count})
    except Exception as exc:
        with _assign_lock:
            _assign_state.update({"error": str(exc), "running": False})
    finally:
        from django import db as _db
        _db.connections.close_all()


@staff_member_required(login_url="/web/login/")
def genre_assign_start(request):
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    folder = request.POST.get("folder", "").strip()
    genre = request.POST.get("genre", "").strip()
    if not folder or not genre:
        return HttpResponse('<div id="assign-status"><div class="callout alert">❌ Укажите папку и жанр</div></div>')
    if not os.path.isdir(folder):
        return HttpResponse(f'<div id="assign-status"><div class="callout alert">❌ Папка не найдена: {folder}</div></div>')
    with _assign_lock:
        if _assign_state["running"]:
            return _render_assign_status(dict(_assign_state))
        _assign_state.update({
            "running": True, "done": False, "error": None,
            "processed": 0, "total": 0, "current": "", "count": 0,
        })
    threading.Thread(target=_run_assign_thread, args=(folder, genre), daemon=True).start()
    return _render_assign_status(dict(_assign_state))


@staff_member_required(login_url="/web/login/")
def genre_assign_status(request):
    with _assign_lock:
        state = dict(_assign_state)
    return _render_assign_status(state)


def _render_assign_status(state):
    from django.template.loader import render_to_string
    pct = 0
    if state["total"] > 0:
        pct = min(100, int(state["processed"] / state["total"] * 100))
    html = render_to_string("fb2parser/genre_assign_status.html", {"state": state, "pct": pct})
    return HttpResponse(html)


# ── Нормализация ──────────────────────────────────────────────────────────────

_norm_lock = threading.Lock()
_norm_state: dict = {
    "running": False, "done": False, "error": None,
    "processed": 0, "total": 0, "log": [],
}


@staff_member_required(login_url="/web/login/")
def normalize(request):
    from constance import config as cfg
    with _norm_lock:
        state = dict(_norm_state)
    return render(request, "fb2parser/normalize.html", _ctx(
        "normalize", "Нормализация",
        root=cfg.SOPDS_ROOT_LIB or "",
        state=state,
    ))


def _run_normalize_thread(folder_path):
    from .fb2parser_bridge import _ensure_path, get_normalization_settings
    from django import db
    db.connections.close_all()
    try:
        _ensure_path()
        import importlib
        settings_mod = importlib.import_module("settings_manager")
        pipeline_mod = importlib.import_module("author_pipeline_service")
        pass3_mod = importlib.import_module("passes.pass3_normalize")

        fb2parser_path = _ensure_path()
        import os as _os
        settings = settings_mod.SettingsManager(_os.path.join(fb2parser_path, "config.json"))

        log_lines = []
        def log(msg):
            log_lines.append(msg)
            with _norm_lock:
                _norm_state["log"] = log_lines[-100:]

        log(f"Запуск нормализации для: {folder_path}")
        records = pipeline_mod.run_author_only_pipeline(folder_path, settings, None)
        with _norm_lock:
            _norm_state["total"] = len(records)

        log(f"Получено записей: {len(records)}")
        p3 = pass3_mod.Pass3Normalize(None, settings)
        p3.execute(records)
        log("Нормализация завершена.")

        with _norm_lock:
            _norm_state.update({"done": True, "running": False, "processed": len(records)})
    except Exception as exc:
        import traceback
        with _norm_lock:
            _norm_state.update({"error": str(exc), "running": False})
    finally:
        from django import db as _db
        _db.connections.close_all()


@staff_member_required(login_url="/web/login/")
def normalize_start(request):
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    folder = request.POST.get("folder", "").strip()
    if not folder or not os.path.isdir(folder):
        return HttpResponse(f'<div id="norm-status"><div class="callout alert">❌ Папка не найдена: {folder}</div></div>')
    with _norm_lock:
        if _norm_state["running"]:
            return _render_norm_status(dict(_norm_state))
        _norm_state.update({"running": True, "done": False, "error": None,
                            "processed": 0, "total": 0, "log": []})
    threading.Thread(target=_run_normalize_thread, args=(folder,), daemon=True).start()
    return _render_norm_status(dict(_norm_state))


@staff_member_required(login_url="/web/login/")
def normalize_status(request):
    with _norm_lock:
        state = dict(_norm_state)
    return _render_norm_status(state)


def _render_norm_status(state):
    from django.template.loader import render_to_string
    pct = 0
    if state["total"] > 0:
        pct = min(100, int(state["processed"] / state["total"] * 100))
    html = render_to_string("fb2parser/normalize_status.html", {"state": state, "pct": pct})
    return HttpResponse(html)


# ── Синхронизация ─────────────────────────────────────────────────────────────

_sync_lock = threading.Lock()
_sync_state: dict = {
    "running": False, "done": False, "error": None,
    "processed": 0, "total": 0, "current": "",
    "stats": {},
    "log": [],
}


@staff_member_required(login_url="/web/login/")
def sync(request):
    with _sync_lock:
        state = dict(_sync_state)
    return render(request, "fb2parser/sync.html", _ctx("sync", "Синхронизация", state=state))


def _run_sync_thread():
    from .fb2parser_bridge import get_sync_service
    from django import db
    db.connections.close_all()
    try:
        svc = get_sync_service()
        log_lines = []

        def on_progress(cur, tot, msg):
            with _sync_lock:
                _sync_state.update({"processed": cur, "total": tot, "current": msg})

        def on_log(msg):
            log_lines.append(msg)
            with _sync_lock:
                _sync_state["log"] = log_lines[-200:]

        stats = svc.synchronize(
            progress_callback=on_progress,
            log_callback=on_log,
        )
        with _sync_lock:
            _sync_state.update({"done": True, "running": False, "stats": stats or {}})
    except Exception as exc:
        with _sync_lock:
            _sync_state.update({"error": str(exc), "running": False})
    finally:
        from django import db as _db
        _db.connections.close_all()


@staff_member_required(login_url="/web/login/")
def sync_start(request):
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    with _sync_lock:
        if _sync_state["running"]:
            return _render_sync_status(dict(_sync_state))
        _sync_state.update({"running": True, "done": False, "error": None,
                            "processed": 0, "total": 0, "current": "",
                            "stats": {}, "log": []})
    threading.Thread(target=_run_sync_thread, daemon=True).start()
    return _render_sync_status(dict(_sync_state))


@staff_member_required(login_url="/web/login/")
def sync_status(request):
    with _sync_lock:
        state = dict(_sync_state)
    return _render_sync_status(state)


def _render_sync_status(state):
    from django.template.loader import render_to_string
    pct = 0
    if state["total"] > 0:
        pct = min(100, int(state["processed"] / state["total"] * 100))
    html = render_to_string("fb2parser/sync_status.html", {"state": state, "pct": pct})
    return HttpResponse(html)


@staff_member_required(login_url="/web/login/")
def log(request):
    return render(request, "fb2parser/log.html", _ctx("log", "Лог"))


@staff_member_required(login_url="/web/login/")
def search(request):
    return render(request, "fb2parser/search.html", _ctx("search", "Поиск по метаданным"))


@staff_member_required(login_url="/web/login/")
def new_books(request):
    return render(request, "fb2parser/new_books.html", _ctx("new_books", "Новые книги"))


@staff_member_required(login_url="/web/login/")
def series_gaps(request):
    return render(request, "fb2parser/series_gaps.html", _ctx("series_gaps", "Серии с пробелами"))


@staff_member_required(login_url="/web/login/")
def integrity(request):
    return render(request, "fb2parser/integrity.html", _ctx("integrity", "Проверка целостности FB2"))
