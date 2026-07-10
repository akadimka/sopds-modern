import logging
import os
import re
import threading

from constance import config
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse, JsonResponse
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
    root = config.SOPDS_ROOT_LIB or ""
    with _scan_lock:
        state = dict(_scan_state)
    return render(request, "fb2parser/dashboard.html", _ctx(
        "dashboard", "Главная",
        root=root,
        state=state,
    ))


@staff_member_required(login_url="/web/login/")
def statistics(request):
    from django.db.models import Count
    stats = {
        "books":    Book.objects.count(),
        "authors":  Author.objects.count(),
        "genres":   Genre.objects.count(),
        "series":   Series.objects.count(),
        "catalogs": Catalog.objects.count(),
    }
    try:
        last_scan = Counter.objects.get(name="allbooks").update_time
    except Counter.DoesNotExist:
        last_scan = None
    top_genres = Genre.objects.annotate(cnt=Count("bgenre")).order_by("-cnt")[:5]
    recent_books = Book.objects.order_by("-id").prefetch_related("genres")[:10]
    return render(request, "fb2parser/statistics.html", _ctx(
        "statistics", "Статистика",
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


# ── Браузер папок ────────────────────────────────────────────────────────────

@staff_member_required(login_url="/web/login/")
def browse_folders(request):
    """Возвращает HTML-список подпапок (и опционально файлов) для picker."""
    path = request.GET.get("path", "").strip()
    target_input = request.GET.get("target", "")
    show_files = request.GET.get("show_files", "")       # непустое → показывать файлы
    ext_filter  = request.GET.get("ext", "").lower()     # ".csv" → только .csv файлы

    entries = []
    error = None
    parent = None

    if not path:
        import string
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            entries = [{"name": d, "path": d, "is_drive": True, "is_file": False} for d in drives]
        else:
            path = "/"

    if path:
        try:
            parent = str(os.path.dirname(path.rstrip("/\\")) or path)
            if parent == path.rstrip("/\\"):
                parent = None
            entries = []
            for name in sorted(os.listdir(path), key=str.lower):
                full = os.path.join(path, name)
                if os.path.isdir(full):
                    entries.append({"name": name, "path": full, "is_drive": False, "is_file": False})
                elif show_files:
                    if not ext_filter or name.lower().endswith(ext_filter):
                        entries.append({"name": name, "path": full, "is_drive": False, "is_file": True})
        except PermissionError:
            error = "Нет доступа к папке"
        except Exception as e:
            error = str(e)

    from django.template.loader import render_to_string
    html = render_to_string("fb2parser/folder_browser.html", {
        "path": path,
        "parent": parent,
        "entries": entries,
        "target": target_input,
        "error": error,
        "show_files": show_files,
        "ext_filter": ext_filter,
    })
    return HttpResponse(html)


# ── Дерево папок (главная страница) ──────────────────────────────────────────

@staff_member_required(login_url="/web/login/")
def folder_tree(request):
    """Возвращает один уровень дерева папок (ленивая загрузка)."""
    import hashlib
    path = request.GET.get("path", "").strip()
    if not path or not os.path.isdir(path):
        return HttpResponse(
            "<div style='padding:1.5rem; color:#7f8c8d; text-align:center;'>Укажите папку для отображения структуры</div>"
        )
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except PermissionError:
        return HttpResponse("<div style='padding:0.5rem 1rem; color:#c0392b; font-size:0.83rem;'>⚠ Нет доступа к папке</div>")
    except Exception as e:
        return HttpResponse(f"<div style='padding:0.5rem 1rem; color:#c0392b; font-size:0.83rem;'>⚠ {e}</div>")

    entries = []
    for name in names:
        full = os.path.join(path, name)
        if not os.path.isdir(full):
            continue
        try:
            children = os.listdir(full)
        except PermissionError:
            children = []
        has_subdirs = any(os.path.isdir(os.path.join(full, f)) for f in children)
        has_fb2_direct = any(f.lower().endswith(".fb2") for f in children)
        if not has_fb2_direct and not has_subdirs:
            continue  # пустая папка — скрываем
        node_id = "n" + hashlib.md5(full.encode("utf-8", errors="replace")).hexdigest()[:12]
        entries.append({
            "path": full,
            "name": name,
            "has_subdirs": has_subdirs,
            "node_id": node_id,
        })

    from django.template.loader import render_to_string
    html = render_to_string("fb2parser/folder_tree.html", {"folders": entries})
    return HttpResponse(html)


@staff_member_required(login_url="/web/login/")
@staff_member_required
def server_restart(request):
    """Touch manage.py to trigger Django dev server autoreload."""
    import pathlib, threading
    manage_py = pathlib.Path(__file__).parent.parent / "manage.py"
    def _touch():
        import time; time.sleep(0.3)
        manage_py.touch()
    threading.Thread(target=_touch, daemon=True).start()
    return HttpResponse(
        '<script>setTimeout(function(){location.reload();},2500);</script>'
        '<span style="color:#27ae60">⟳ Перезагрузка...</span>',
        content_type="text/html; charset=utf-8",
    )


def folder_count(request):
    """Рекурсивный подсчёт FB2 в папке — вызывается асинхронно после рендера узла."""
    path = request.GET.get("path", "").strip()
    if not path or not os.path.isdir(path):
        return HttpResponse("")
    count = 0
    try:
        for _, _, files in os.walk(path):
            count += sum(1 for f in files if f.lower().endswith(".fb2"))
    except Exception:
        pass
    if count == 0:
        return HttpResponse("")
    return HttpResponse(
        f'<span class="ftree-count">{count} fb2</span>',
        content_type="text/html; charset=utf-8",
    )


@staff_member_required(login_url="/web/login/")
def genre_names(request):
    """Список всех имён жанров для picker-а (HTML-частичка)."""
    from .fb2parser_bridge import get_genres_manager
    from django.template.loader import render_to_string
    try:
        gm = get_genres_manager()
        names = []
        def _collect(nodes):
            for n in nodes:
                names.append(n.name)
                _collect(n.children)
        _collect(gm.root_nodes)
        names.sort()
        error = None
    except Exception as e:
        names = []
        error = str(e)
    html = render_to_string("fb2parser/genre_picker_list.html", {"names": names, "error": error})
    return HttpResponse(html)


@staff_member_required(login_url="/web/login/")
def assign_genre_multi(request):
    """Присвоить жанр нескольким папкам сразу. POST JSON: {genre, paths:[...]}."""
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    import json
    from django.http import JsonResponse
    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    genre = data.get("genre", "").strip()
    paths = data.get("paths", [])
    if not genre or not paths:
        return JsonResponse({"error": "genre and paths required"}, status=400)
    from .fb2parser_bridge import get_genre_assignment_service
    results = []
    try:
        service = get_genre_assignment_service()
    except Exception as e:
        return JsonResponse({"error": f"Не удалось загрузить fb2parser: {e}"}, status=500)
    for path in paths:
        if not os.path.isdir(path):
            results.append({"path": path, "success": False, "error": "Папка не найдена"})
            continue
        try:
            count = service.assign_genre_to_folder(path, genre)
            results.append({"path": path, "success": True, "count": count})
            with _genre_assignments_lock:
                _genre_assignments[os.path.abspath(path)] = genre
        except Exception as e:
            results.append({"path": path, "success": False, "error": str(e)})
    return JsonResponse({"results": results})


@staff_member_required(login_url="/web/login/")
def main_scan_start(request):
    """Запускает сканирование с главной страницы; возвращает строку статуса."""
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    with _scan_lock:
        if _scan_state["running"]:
            return _render_main_status(dict(_scan_state))
        root = request.POST.get("root", config.SOPDS_ROOT_LIB or "").strip()
        if not root or not os.path.isdir(root):
            return HttpResponse(
                f'<span style="color:#c0392b;">❌ Папка не найдена: {root}</span>'
            )
        _scan_state.update({
            "running": True, "done": False, "error": None,
            "processed": 0, "total": 0, "current": "",
            "books_added": 0, "books_skipped": 0, "bad_books": 0,
            "bad_list": [],
        })
    t = threading.Thread(target=_run_scan_thread, args=(root,), daemon=True)
    t.start()
    return _render_main_status(dict(_scan_state))


@staff_member_required(login_url="/web/login/")
def main_scan_status(request):
    return _render_main_status(dict(_scan_state))


def _render_main_status(state):
    from django.template.loader import render_to_string
    pct = 0
    if state["total"] > 0:
        pct = min(100, int(state["processed"] / state["total"] * 100))
    html = render_to_string("fb2parser/main_scan_statusbar.html", {"state": state, "pct": pct})
    return HttpResponse(html)


@staff_member_required(login_url="/web/login/")
def scan_results(request):
    """3-панельный вид результатов: жанры / ошибки / детали."""
    with _scan_lock:
        state = dict(_scan_state)
    from django.db.models import Count
    genres_qs = (
        Genre.objects
        .values("subsection")
        .annotate(cnt=Count("bgenre"))
        .order_by("subsection")
    )
    return render(request, "fb2parser/main_results.html", {
        "state": state,
        "genres_list": list(genres_qs),
    })


@staff_member_required(login_url="/web/login/")
def genre_books(request):
    """Книги для выбранного жанра (для панели Детали)."""
    genre_name = request.GET.get("genre", "")
    books = Book.objects.filter(genre__subsection=genre_name).order_by("title")[:200]
    from django.template.loader import render_to_string
    html = render_to_string("fb2parser/genre_books.html", {"books": books, "genre": genre_name})
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
    "records": [], "records_total": 0,
    "current_file": "", "folder": "",
}


@staff_member_required(login_url="/web/login/")
def normalize(request):
    from constance import config as cfg
    with _norm_lock:
        state = dict(_norm_state)
    # Если после перезапуска сервера память пуста — пробуем восстановить кэш
    if not state["records"] and not state["running"]:
        folder = state.get("folder") or cfg.SOPDS_ROOT_LIB or ""
        if folder:
            _norm_restore_from_cache(folder)
            with _norm_lock:
                state = dict(_norm_state)
    return render(request, "fb2parser/normalize.html", _ctx(
        "normalize", "Нормализация",
        root=cfg.SOPDS_ROOT_LIB or "",
        state=state,
    ))


def _norm_cache_path(folder_path):
    import hashlib
    h = hashlib.md5(folder_path.encode("utf-8", errors="replace")).hexdigest()[:16]
    cache_dir = os.path.join(os.path.dirname(__file__), "_norm_cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f"norm_{h}.json")


def _norm_cache_save(folder_path, records):
    import json, time
    try:
        data = {"folder": folder_path, "ts": time.time(), "records": records}
        with open(_norm_cache_path(folder_path), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _norm_cache_load(folder_path, max_age_hours=24):
    import json, time
    try:
        p = _norm_cache_path(folder_path)
        if not os.path.exists(p):
            return None
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        age_h = (time.time() - data.get("ts", 0)) / 3600
        if age_h > max_age_hours:
            return None
        if data.get("folder") != folder_path:
            return None
        return data["records"]
    except Exception:
        return None


def _norm_restore_from_cache(folder_path):
    """Загружает кэш в _norm_state если он есть и актуален. Возвращает True при успехе."""
    records = _norm_cache_load(folder_path)
    if records is None:
        return False
    with _norm_lock:
        _norm_state.update({
            "done": True, "running": False, "error": None,
            "processed": len(records), "total": len(records),
            "records": records, "records_total": len(records),
            "folder": folder_path, "current_file": "",
        })
    return True


def _run_normalize_thread(folder_path):
    from django import db
    db.connections.close_all()
    try:
        from fb2parser_core import regen_csv
        from .fb2parser_bridge import _config_path
        config_path = _config_path()
        service = regen_csv.RegenCSVService(config_path)

        def _progress(current, total, status=""):
            with _norm_lock:
                _norm_state["processed"] = current
                _norm_state["total"] = max(total, 1)
                if status:
                    _norm_state["current_file"] = str(status)[:120]
                    _norm_state["log"].append(str(status))
                    _norm_state["log"] = _norm_state["log"][-200:]

        records = service.generate_csv(folder_path, progress_callback=_progress) or []

        recs_dicts = []
        for r in records:
            recs_dicts.append({
                "file_path":        getattr(r, "file_path", ""),
                "metadata_authors": getattr(r, "metadata_authors", ""),
                "proposed_author":  getattr(r, "proposed_author", ""),
                "author_source":    getattr(r, "author_source", ""),
                "metadata_series":  getattr(r, "metadata_series", ""),
                "proposed_series":  getattr(r, "proposed_series", ""),
                "series_source":    getattr(r, "series_source", ""),
                "book_title":       getattr(r, "file_title", ""),
                "metadata_genre":   getattr(r, "metadata_genre", ""),
            })

        with _norm_lock:
            _norm_state.update({
                "done": True, "running": False,
                "processed": len(recs_dicts), "total": len(recs_dicts),
                "records": recs_dicts, "records_total": len(recs_dicts),
            })
        # Сохраняем кэш на диск — переживёт перезапуск сервера
        _norm_cache_save(folder_path, recs_dicts)
    except Exception as exc:
        import traceback
        tb = traceback.format_exc()
        with _norm_lock:
            _norm_state.update({"error": str(exc), "running": False})
            _norm_state["log"].append(tb)
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
        _norm_state.update({
            "running": True, "done": False, "error": None,
            "processed": 0, "total": 0, "log": [],
            "records": [], "records_total": 0, "current_file": "",
            "folder": folder,
        })
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
    html = render_to_string("fb2parser/normalize_bar.html", {"state": state, "pct": pct})
    return HttpResponse(html)


@staff_member_required(login_url="/web/login/")
def normalize_table(request):
    """Возвращает HTML-строки таблицы нормализации (постранично)."""
    offset = int(request.GET.get("offset", 0))
    limit = 1000
    q = request.GET.get("q", "").strip().lower()
    with _norm_lock:
        records = list(_norm_state["records"])
        total_all = _norm_state["records_total"]
    if q:
        records = [r for r in records if any(q in str(v).lower() for v in r.values())]
    total = len(records)
    page = records[offset:offset + limit]
    has_more = (offset + limit) < total
    from django.template.loader import render_to_string
    html = render_to_string("fb2parser/normalize_table.html", {
        "records": page,
        "has_more": has_more,
        "next_offset": offset + limit,
        "total": total,
        "total_all": total_all,
        "q": q,
    })
    return HttpResponse(html)


@staff_member_required(login_url="/web/login/")
@staff_member_required(login_url="/web/login/")
def names_from_csv(request):
    """GET ?csv_path=... → список авторов с неизвестным полом из CSV-файла."""
    import re as _re, csv as _csv
    csv_path = request.GET.get("csv_path", "").strip()

    if not csv_path:
        return HttpResponse(
            '<div style="padding:1rem;color:#7f8c8d;">Укажите путь к CSV-файлу.</div>')
    if not os.path.isfile(csv_path):
        return HttpResponse(
            f'<div style="padding:1rem;color:#c0392b;">❌ Файл не найден: {csv_path}</div>')

    from fb2parser_core.settings_manager import SettingsManager
    from fb2parser_core.author_pipeline_service import guess_first_name
    from .fb2parser_bridge import _config_path
    sm = SettingsManager(_config_path())
    male_set   = {n.lower() for n in sm.get_male_names()}
    female_set = {n.lower() for n in sm.get_female_names()}

    seen = set()
    rows = []
    try:
        with open(csv_path, encoding='utf-8', newline='') as f:
            for row in _csv.DictReader(f):
                if row.get('delete_flag'):
                    continue
                combined = (row.get('proposed_author') or '').strip()
                if not combined or combined == 'Сборник':
                    continue
                source    = row.get('author_source') or row.get('series_source') or ''
                file_path = row.get('file_path') or ''
                for author in (a.strip() for a in _re.split(r'[,;]+', combined) if a.strip()):
                    if author in seen:
                        continue
                    seen.add(author)
                    gender = ''
                    for word in author.split():
                        w = word.lower()
                        if w in male_set:
                            gender = 'М'; break
                        if w in female_set:
                            gender = 'Ж'; break
                    if gender:
                        continue
                    rows.append({'source': source, 'author': author,
                                 'first_name': guess_first_name(author, source),
                                 'file_path': file_path})
    except Exception as e:
        return HttpResponse(
            f'<div style="padding:1rem;color:#c0392b;">❌ Ошибка чтения CSV: {e}</div>')

    from django.template.loader import render_to_string
    html = render_to_string("fb2parser/names_list.html", {"rows": rows})
    return HttpResponse(html)


def names_list(request):
    """Возвращает список авторов с неизвестным полом из текущего _norm_state."""
    import re as _re
    with _norm_lock:
        records = list(_norm_state["records"])
        cached_folder = _norm_state.get("folder", "")
    # Пробуем восстановить из кэша если память пуста
    if not records and cached_folder:
        _norm_restore_from_cache(cached_folder)
        with _norm_lock:
            records = list(_norm_state["records"])
    if not records:
        return HttpResponse('<div style="padding:1rem;color:#7f8c8d;">Сначала создайте CSV.</div>')

    from fb2parser_core.settings_manager import SettingsManager
    from fb2parser_core.author_pipeline_service import guess_first_name
    from .fb2parser_bridge import _config_path
    sm = SettingsManager(_config_path())
    male_set   = {n.lower() for n in sm.get_male_names()}
    female_set = {n.lower() for n in sm.get_female_names()}

    seen = set()
    rows = []
    for r in records:
        combined = (r.get("proposed_author") or "").strip()
        if not combined or combined == "Сборник":
            continue
        source = r.get("author_source") or ""
        for author in (a.strip() for a in _re.split(r'[,;]+', combined) if a.strip()):
            if author in seen:
                continue
            seen.add(author)
            gender = ""
            for word in author.split():
                w = word.lower()
                if w in male_set:
                    gender = "М"
                    break
                if w in female_set:
                    gender = "Ж"
                    break
            if gender:
                continue
            first_name = guess_first_name(author, source)
            rows.append({"source": source, "author": author,
                         "first_name": first_name, "file_path": r.get("file_path", "")})

    from django.template.loader import render_to_string
    html = render_to_string("fb2parser/names_list.html", {"rows": rows})
    return HttpResponse(html)


@staff_member_required(login_url="/web/login/")
def names_save(request):
    """POST {male: [...], female: [...]} — добавляет имена в app_settings.json."""
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    import json
    from fb2parser_core.settings_manager import SettingsManager
    from .fb2parser_bridge import _config_path

    try:
        data = json.loads(request.body)
    except Exception:
        return JsonResponse({"error": "bad json"}, status=400)

    male_new   = [n.strip() for n in data.get("male", [])   if n.strip()]
    female_new = [n.strip() for n in data.get("female", []) if n.strip()]

    if not male_new and not female_new:
        return JsonResponse({"added": 0})

    sm = SettingsManager(_config_path())

    male_added = female_added = 0
    if male_new:
        existing = set(sm.get_male_names())
        actual = set(male_new) - existing
        if actual:
            merged = sorted(existing | actual, key=str.lower)
            sm.set_male_names(merged)
            male_added = len(actual)

    if female_new:
        existing = set(sm.get_female_names())
        actual = set(female_new) - existing
        if actual:
            merged = sorted(existing | actual, key=str.lower)
            sm.set_female_names(merged)
            female_added = len(actual)

    return JsonResponse({"added": male_added + female_added,
                         "male": male_added, "female": female_added})


@staff_member_required(login_url="/web/login/")
def names_check_online(request):
    """POST {authors: ["Иванов Иван", ...]} → StreamingHttpResponse с SSE.
    Каждое событие: data: {"author": "...", "gender": "М"|"Ж"|"", "status": "ok"|"unknown"|"error"|"rate_limit"}
    """
    import json
    from django.http import StreamingHttpResponse
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    try:
        data = json.loads(request.body)
        authors = [a.strip() for a in data.get("authors", []) if a.strip()]
    except Exception:
        return JsonResponse({"error": "bad json"}, status=400)

    from fb2parser_core.gender_lookup import GenderLookupService, STATUS_FOUND
    from .fb2parser_bridge import _config_path
    db_path = os.path.join(os.path.dirname(_config_path()), "gender_cache.db")

    def event_stream():
        svc = GenderLookupService()
        svc._db_path = __import__('pathlib').Path(db_path)
        svc._load_db_cache()
        for author in authors:
            try:
                result = svc.lookup_one(author)
                gender = ""
                if result.status == STATUS_FOUND:
                    gender = "М" if result.gender_ru == "Муж." else "Ж" if result.gender_ru == "Жен." else ""
                payload = json.dumps({"author": author, "gender": gender,
                                      "status": result.status,
                                      "first_name": result.first_name or ""},
                                     ensure_ascii=False)
            except Exception as e:
                payload = json.dumps({"author": author, "gender": "", "status": "error",
                                      "detail": str(e)}, ensure_ascii=False)
            yield f"data: {payload}\n\n".encode()
        yield b'data: {"done": true}\n\n'

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


# ── Великомученницы ───────────────────────────────────────────────────────────

def _is_female_author(author_str: str, male_set: set, female_set: set) -> bool:
    """Автор женщина: ни одно слово не мужское, хотя бы одно женское."""
    parts = author_str.split()
    if not parts:
        return False
    for word in parts:
        if word.lower() in male_set:
            return False
    for word in parts:
        if word.lower() in female_set:
            return True
    return False


@staff_member_required(login_url="/web/login/")
def martyrs_list(request):
    """Возвращает список файлов, у которых все авторы — женщины."""
    import re as _re
    _SKIP = {"Сборник", "Соавторство", "[unknown]"}

    with _norm_lock:
        records = list(_norm_state["records"])
        folder  = _norm_state.get("folder", "")

    if not records and folder:
        _norm_restore_from_cache(folder)
        with _norm_lock:
            records = list(_norm_state["records"])
            folder  = _norm_state.get("folder", "")

    if not records:
        return HttpResponse('<div style="padding:1rem;color:#7f8c8d;">Сначала создайте CSV.</div>')

    from fb2parser_core.settings_manager import SettingsManager
    from .fb2parser_bridge import _config_path
    sm = SettingsManager(_config_path())
    male_set   = {n.lower() for n in sm.get_male_names()}
    female_set = {n.lower() for n in sm.get_female_names()}

    rows = []
    for rec in records:
        combined = (rec.get("proposed_author") or "").strip()
        if not combined or combined in _SKIP:
            continue
        authors = [a.strip() for a in _re.split(r'[,;]+', combined) if a.strip()]
        if authors and all(_is_female_author(a, male_set, female_set) for a in authors):
            file_path = rec.get("file_path", "")
            full_path = os.path.join(folder, file_path) if folder and not os.path.isabs(file_path) else file_path
            rows.append({"file_path": file_path, "full_path": full_path, "authors": combined})

    from django.template.loader import render_to_string
    return HttpResponse(render_to_string("fb2parser/martyrs.html", {"rows": rows, "folder": folder}))


@staff_member_required(login_url="/web/login/")
def martyrs_delete(request):
    """POST {paths: [...]} — удаляет файлы и пустые папки вверх до корня."""
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    import json
    try:
        data  = json.loads(request.body)
        paths = [p.strip() for p in data.get("paths", []) if p.strip()]
    except Exception:
        return JsonResponse({"error": "bad json"}, status=400)

    with _norm_lock:
        folder = _norm_state.get("folder", "")

    deleted, errors = 0, []
    deleted_dirs = set()
    for p in paths:
        try:
            if os.path.isfile(p):
                parent = os.path.dirname(p)
                os.remove(p)
                deleted += 1
                deleted_dirs.add(parent)
        except Exception as e:
            errors.append(f"{p}: {e}")

    # Удаляем пустые папки вверх до корня библиотеки
    for d in sorted(deleted_dirs, key=len, reverse=True):
        try:
            cur = d
            while cur and os.path.isdir(cur) and not os.listdir(cur):
                if folder and os.path.normpath(cur) == os.path.normpath(folder):
                    break
                os.rmdir(cur)
                cur = os.path.dirname(cur)
        except Exception:
            pass

    return JsonResponse({"deleted": deleted, "errors": errors})


# ── Дубликаты ─────────────────────────────────────────────────────────────────

import unicodedata as _ud


_RGET_ALIASES = {'file_title': 'book_title', 'book_title': 'file_title'}


def _rget(rec, attr, default=''):
    """Универсальный геттер: работает и с dataclass-объектами, и с dict (из JSON-кэша).

    Обрабатывает алиасы полей (file_title ↔ book_title).
    """
    if isinstance(rec, dict):
        v = rec.get(attr)
        if v is None and attr in _RGET_ALIASES:
            v = rec.get(_RGET_ALIASES[attr])
        return v if v is not None else default
    return getattr(rec, attr, default)


_DUP_COLLECTION = re.compile(
    r'\b(сборник|антология|anthology|collection|omnibus|сборн)\b',
    re.IGNORECASE | re.UNICODE,
)
_DUP_RNG_SN = re.compile(r'^\d+\s*[-–—]\s*\d+')


def _dup_priority(path, rec=None) -> float:
    score = 0.0
    if rec is not None and (_rget(rec, 'proposed_series') or '').strip():
        score += 2.0
    if not any(_DUP_COLLECTION.search(p) for p in path.parts):
        score += 1.0
    score += len(path.parts) * 0.1
    return score


def _dup_norm_str(s: str) -> str:
    s = _ud.normalize('NFKC', s or '').lower().replace('ё', 'е')
    s = re.sub(r'[«»"\'„"‟\(\)\[\]…]', '', s)
    return re.sub(r'\s+', ' ', s).strip()


def _dup_norm_author(s: str) -> str:
    s = _ud.normalize('NFKC', s or '').lower().replace('ё', 'е')
    return re.sub(r'\s+', ' ', re.sub(r'\.', ' ', s)).strip()


def _dup_rec_authors(rec) -> frozenset:
    proposed = _rget(rec, 'proposed_author') or ''
    meta = _rget(rec, 'metadata_authors') or ''
    src = proposed if proposed else meta
    return frozenset(a for a in (_dup_norm_author(p) for p in re.split(r'[;,]', src)) if len(a) >= 3)


def _find_duplicates(records, folder_path) -> dict:
    """Двухфазный поиск дубликатов: хэш + метаданные.

    Возвращает {dup_path: {source, reasons, series}}.
    """
    from pathlib import Path as _Path
    _PLACEHOLDER = {'no title', 'без названия', 'untitled', 'unknown'}

    def _abs(fp):
        p = _Path(fp)
        return p if p.is_absolute() else folder_path / p

    # Индекс record → Path
    rec_by_path = {}
    for rec in records:
        fp = _rget(rec, 'file_path') or ''
        if fp:
            rec_by_path[_abs(fp)] = rec

    def _pick_src(paths_with_recs):
        scored = sorted(paths_with_recs,
                        key=lambda pr: (-_dup_priority(pr[0], pr[1]), str(pr[0])))
        return scored[0][0], [p for p, _ in scored[1:]]

    result = {}

    # Фаза 1: хэш-дубликаты
    hash_map = {}
    for rec in records:
        h = _rget(rec, 'content_hash') or ''
        fp = _rget(rec, 'file_path') or ''
        if h and fp:
            hash_map.setdefault(h, []).append(_abs(fp))
    for paths in hash_map.values():
        if len(paths) < 2:
            continue
        src, dups = _pick_src([(p, rec_by_path.get(p)) for p in paths])
        for dup in dups:
            result.setdefault(dup, {'source': src, 'reasons': set(), 'series': ''})['reasons'].add('Хэш')

    # Фаза 2: метадата-дубликаты
    _RNG_IN_STEM = re.compile(r'\b(\d+)\s*[-–—]\s*(\d+)\s*$')

    def _precomp_range(rec):
        """Возвращает нормализованную строку диапазона ('N-M') или '' если не предкомпиляция."""
        sn = (_rget(rec, 'series_number') or '').strip()
        m = _DUP_RNG_SN.match(sn) if sn else None
        if m:
            return sn
        fp = _rget(rec, 'file_path') or ''
        stem = _Path(fp).stem if fp else ''
        last = stem.rsplit('. ', 1)[-1] if '. ' in stem else stem
        m2 = _RNG_IN_STEM.search(last)
        return m2.group(0).strip() if m2 else ''

    def _is_precomp(rec):
        return bool(_precomp_range(rec))

    def _is_subcomp(rec):
        return (_Path(_rget(rec, 'file_path') or '').stem.count('. ') >= 2)

    title_map = {}
    for rec in records:
        t = _dup_norm_str(_rget(rec, 'file_title') or '')
        if t and len(t) >= 4 and t not in _PLACEHOLDER:
            title_map.setdefault(t, []).append(rec)

    for recs in title_map.values():
        if len(recs) < 2:
            continue
        recs_sorted = sorted(recs, key=lambda r: str(_rget(r, 'file_path')))
        for i, ra in enumerate(recs_sorted):
            auth_a = _dup_rec_authors(ra)
            if not auth_a:
                continue
            for rb in recs_sorted[i + 1:]:
                auth_b = _dup_rec_authors(rb)
                if not auth_b or not (auth_a & auth_b):
                    continue
                rng_a, rng_b = _precomp_range(ra), _precomp_range(rb)
                # Предкомпиляция vs одиночная книга → не дубликаты
                if bool(rng_a) != bool(rng_b):
                    continue
                # Две предкомпиляции с разными диапазонами → не дубликаты
                if rng_a and rng_b and rng_a != rng_b:
                    continue
                if _is_subcomp(ra) != _is_subcomp(rb):
                    continue
                fp_a = _rget(ra, 'file_path') or ''
                fp_b = _rget(rb, 'file_path') or ''
                if not fp_a or not fp_b:
                    continue
                pa, pb = _abs(fp_a), _abs(fp_b)
                if not pb.exists():
                    continue
                if _dup_priority(pa, ra) >= _dup_priority(pb, rb):
                    src_p, dup_p, dup_rec = pa, pb, rb
                else:
                    src_p, dup_p, dup_rec = pb, pa, ra
                    if not src_p.exists():
                        continue
                series = (_rget(dup_rec, 'proposed_series') or
                          _rget(ra, 'proposed_series') or
                          _rget(rb, 'proposed_series') or '')
                entry = result.setdefault(dup_p, {'source': src_p, 'reasons': set(), 'series': ''})
                entry['reasons'].add('Метаданные')
                if series and not entry['series']:
                    entry['series'] = series

    return result


@staff_member_required(login_url="/web/login/")
def duplicates_find(request):
    """GET — ищет дубликаты в записях текущего сеанса нормализации."""
    from pathlib import Path as _Path
    with _norm_lock:
        records = list(_norm_state["records"])
        folder = _norm_state.get("folder", "")

    if not records and not folder:
        from constance import config as _cfg
        folder = _cfg.SOPDS_ROOT_LIB or ""
    if not records and folder:
        _norm_restore_from_cache(folder)
        with _norm_lock:
            records = list(_norm_state["records"])
            folder = _norm_state.get("folder", folder)

    if not records:
        return render(request, "fb2parser/duplicates.html", {
            "rows": [], "groups": [],
            "error": "Нет данных — сначала запустите нормализацию.",
        })

    folder_path = _Path(folder) if folder else _Path(".")

    # Диагностика: проверяем первые записи
    diag = []
    for r in records[:2]:
        fp = _rget(r, 'file_path') or 'N/A'
        ft = (_rget(r, 'file_title') or '')[:40]
        pa = (_rget(r, 'proposed_author') or '')[:30]
        diag.append(f"fp={fp!r} title={ft!r} author={pa!r}")
    diag_str = " | ".join(diag)

    try:
        all_dups = _find_duplicates(records, folder_path)
    except Exception as exc:
        import traceback as _tb
        return render(request, "fb2parser/duplicates.html", {
            "rows": [], "groups": [], "error": f"{exc}\n\nDIAG: {diag_str}",
        })

    # Группируем по оригиналу (source)
    from collections import defaultdict
    groups_map = defaultdict(list)
    total_bytes = 0
    for dup_path in sorted(all_dups):
        info = all_dups[dup_path]
        try:
            sz = dup_path.stat().st_size if dup_path.exists() else 0
        except Exception:
            sz = 0
        total_bytes += sz
        sz_str = (f"{sz // 1024} КБ" if sz < 1_048_576
                  else f"{sz / 1_048_576:.1f} МБ")
        reason = '+'.join(sorted(info['reasons']))
        src = info['source']
        try:
            rel = str(dup_path.relative_to(folder_path))
        except ValueError:
            rel = str(dup_path)
        try:
            src_rel = str(src.relative_to(folder_path)) if src else ''
        except ValueError:
            src_rel = str(src) if src else ''
        groups_map[str(src)].append({
            "full_path": str(dup_path),
            "file_path": rel,
            "reason": reason,
            "series": info.get('series', ''),
            "size": sz_str,
            "size_bytes": sz,
            "src_rel": src_rel,
        })

    groups = [{"source_full": src, "source_rel": rows[0]["src_rel"], "rows": rows}
              for src, rows in sorted(groups_map.items())]
    total_mb = total_bytes / 1_048_576
    total_size_str = f"{total_mb:.1f} МБ" if total_bytes >= 1_048_576 else f"{total_bytes // 1024} КБ"

    return render(request, "fb2parser/duplicates.html", {
        "groups": groups,
        "total_dups": len(all_dups),
        "total_files": len(records),
        "total_size": total_size_str,
        "diag": diag_str,
    })


@staff_member_required(login_url="/web/login/")
def duplicates_delete(request):
    """POST {paths: [...]} — удаляет отмеченные дубликаты и пустые папки."""
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    import json
    try:
        data = json.loads(request.body)
        paths = [p.strip() for p in data.get("paths", []) if p.strip()]
    except Exception:
        return JsonResponse({"error": "bad json"}, status=400)

    with _norm_lock:
        folder = _norm_state.get("folder", "")

    deleted, errors = 0, []
    deleted_dirs = set()
    for p in paths:
        try:
            if os.path.isfile(p):
                parent = os.path.dirname(p)
                os.remove(p)
                deleted += 1
                deleted_dirs.add(parent)
        except Exception as e:
            errors.append(f"{p}: {e}")

    for d in sorted(deleted_dirs, key=len, reverse=True):
        try:
            cur = d
            while cur and os.path.isdir(cur) and not os.listdir(cur):
                if folder and os.path.normpath(cur) == os.path.normpath(folder):
                    break
                os.rmdir(cur)
                cur = os.path.dirname(cur)
        except Exception:
            pass

    return JsonResponse({"deleted": deleted, "errors": errors})


# ── Компилятор ────────────────────────────────────────────────────────────────

_compiler_lock = threading.Lock()
_compiler_state: dict = {
    "groups": [],           # List[CompilationGroup] — только в памяти
    "folder": "",
    "running": False,
    "done": False,
    "error": None,
    "progress": 0,
    "total": 0,
    "current": "",
    "log": [],
}


def _rec_to_ns(rec):
    """Конвертировать dict-запись (из JSON-кэша) в SimpleNamespace для FB2CompilerService."""
    from types import SimpleNamespace
    if isinstance(rec, dict):
        d = dict(rec)
        # Алиас: кэш хранит book_title, а FB2CompilerService ожидает file_title
        if 'book_title' in d and 'file_title' not in d:
            d['file_title'] = d['book_title']
        # Гарантируем наличие всех полей BookRecord
        _defaults = {
            'file_path': '', 'file_title': '', 'metadata_authors': '',
            'proposed_author': '', 'author_source': '', 'metadata_series': '',
            'proposed_series': '', 'series_source': '', 'metadata_genre': '',
            'series_number': '', 'content_hash': '',
            'needs_filename_fallback': False, 'delete_flag': False,
        }
        for k, v in _defaults.items():
            d.setdefault(k, v)
        return SimpleNamespace(**d)
    return rec


@staff_member_required(login_url="/web/login/")
def compiler_scan(request):
    """GET — ищет группы для компиляции из записей текущего сеанса нормализации."""
    from pathlib import Path as _Path
    with _norm_lock:
        records = list(_norm_state["records"])
        folder = _norm_state.get("folder", "")

    if not records and not folder:
        from constance import config as _cfg
        folder = _cfg.SOPDS_ROOT_LIB or ""
    if not records and folder:
        _norm_restore_from_cache(folder)
        with _norm_lock:
            records = list(_norm_state["records"])
            folder = _norm_state.get("folder", folder)

    if not records:
        return render(request, "fb2parser/compiler_groups.html", {
            "groups": [], "error": "Нет данных — сначала запустите нормализацию.",
        })

    ns_records = [_rec_to_ns(r) for r in records]
    folder_path = _Path(folder) if folder else _Path(".")

    try:
        from fb2parser_core.fb2_compiler import FB2CompilerService
        svc = FB2CompilerService()
        groups = svc.find_groups(ns_records, folder_path)
    except Exception as exc:
        import traceback as _tb
        return render(request, "fb2parser/compiler_groups.html", {
            "groups": [], "error": str(exc) + "\n" + _tb.format_exc()[-500:],
        })

    with _compiler_lock:
        _compiler_state.update({
            "groups": groups,
            "folder": str(folder_path),
            "done": False, "running": False, "error": None, "log": [],
        })

    _SORT_LABEL = {
        "series_number": "Номер тома",
        "filename":      "Имя файла",
        "title_date":    "Дата (назв.)",
        "publish_date":  "Дата (изд.)",
    }

    import json as _json

    group_data = []
    groups_books_js = {}   # idx → {books, dups, excluded, auto_excluded, output_name}

    for i, g in enumerate(groups):
        if g.cleanup_only:
            status = "cleanup"
        elif g.alphabetical_order:
            status = "alpha"
        elif not g.order_determined:
            status = "partial"
        else:
            status = "ok"

        # Книги группы
        books_list = []
        for n, book in enumerate(g.books, 1):
            title = (getattr(book.record, 'file_title', '') or
                     getattr(book.record, 'book_title', '') or
                     book.abs_path.stem)
            try:
                sz = book.abs_path.stat().st_size
                sz_str = f"{sz // 1024} КБ" if sz < 1_048_576 else f"{sz / 1_048_576:.1f} МБ"
            except Exception:
                sz_str = "—"
            vol = book.volume_label or ("α" if g.alphabetical_order else ("?" if book.order_ambiguous else "—"))
            books_list.append({
                "n": n,
                "title": title[:80],
                "file": book.abs_path.name,
                "sort_src": _SORT_LABEL.get(book.sort_source, book.sort_source or "—"),
                "vol": vol,
                "ambiguous": book.order_ambiguous,
                "size": sz_str,
            })

        # Дубликаты, исключённые
        dups_list = [str(p) for p in (g.duplicate_paths or [])]
        excl_list = [str(p) for p in (g.excluded_paths or [])]
        auto_excl = [str(p) for p in (g.auto_excluded_paths or [])]
        kept_list = [str(p) for p in (g.kept_paths or [])]

        # Предпросмотр имени выходного файла
        try:
            n_vols = len(g.books)
            vol_m = re.match(r'^(\d+)-(\d+)$', g.volume_range or '')
            lo = int(vol_m.group(1)) if vol_m else 0
            hi = int(vol_m.group(2)) if vol_m else 0
            suffix = svc._series_suffix(n_vols, lo, hi, g.part_count,
                                         series_complete=g.series_complete)
            clean_s = FB2CompilerService._clean_series_name(g.series)
            safe_a = re.sub(r'[\\/:*?"<>|]', '_', g.author)
            safe_s = re.sub(r'[/:*?"<>|]', '_', FB2CompilerService._series_to_display(clean_s))
            if suffix:
                suffix = svc._suppress_redundant_suffix(safe_s, suffix)
            out_name = f"{safe_a} - {safe_s} ({suffix}).fb2" if suffix else f"{safe_a} - {safe_s}.fb2"
        except Exception:
            out_name = ""

        series_disp = FB2CompilerService._series_to_display(g.series)
        group_data.append({
            "idx": i,
            "author": g.author,
            "series": series_disp,
            "count": len(g.books),
            "range": g.volume_range or "",
            "status": status,
            "cleanup_only": g.cleanup_only,
        })
        groups_books_js[i] = {
            "books": books_list,
            "dups": dups_list,
            "excluded": excl_list,
            "auto_excluded": auto_excl,
            "kept": kept_list,
            "output_name": out_name,
            "cleanup_only": g.cleanup_only,
        }

    return render(request, "fb2parser/compiler_groups.html", {
        "groups": group_data,
        "total": len(groups),
        "groups_books_json": _json.dumps(groups_books_js, ensure_ascii=False),
    })


def _run_compiler_thread(indices, delete_sources):
    """Фоновый поток: компилирует выбранные группы."""
    try:
        from fb2parser_core.fb2_compiler import FB2CompilerService
    except ImportError:
        from fb2parser_core.fb2_compiler import FB2CompilerService  # noqa

    with _compiler_lock:
        all_groups = list(_compiler_state["groups"])

    groups_to_run = [all_groups[i] for i in indices if i < len(all_groups)]
    total = len(groups_to_run)
    svc = FB2CompilerService()
    log = []

    for n, group in enumerate(groups_to_run, 1):
        with _compiler_lock:
            _compiler_state["progress"] = n - 1
            _compiler_state["total"] = total
            _compiler_state["current"] = f"{group.author} / {FB2CompilerService._series_to_display(group.series)}"

        try:
            result = svc.compile_group(group, output_dir=None, delete_sources=delete_sources)
            if result.success:
                if result.output_path:
                    log.append({"ok": True, "msg": f"✓ {result.output_path.name}"})
                else:
                    log.append({"ok": True, "msg": f"♻ Очищено: {group.author} / {group.series}"})
            else:
                log.append({"ok": False, "msg": f"✗ {group.author} / {group.series}: {result.error}"})
        except Exception as exc:
            log.append({"ok": False, "msg": f"✗ {group.author} / {group.series}: {exc}"})

        with _compiler_lock:
            _compiler_state["log"] = log[:]

    with _compiler_lock:
        _compiler_state.update({
            "running": False, "done": True,
            "progress": total, "total": total,
            "current": "", "log": log,
        })


@staff_member_required(login_url="/web/login/")
def compiler_run(request):
    """POST {indices:[...], delete_sources:bool} — запускает компиляцию в фоне."""
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    import json
    try:
        data = json.loads(request.body)
        indices = [int(i) for i in data.get("indices", [])]
        delete_sources = bool(data.get("delete_sources", False))
    except Exception:
        return JsonResponse({"error": "bad json"}, status=400)

    if not indices:
        return JsonResponse({"error": "Не выбрано ни одной группы"}, status=400)

    with _compiler_lock:
        if _compiler_state["running"]:
            return JsonResponse({"error": "Компиляция уже запущена"}, status=409)
        _compiler_state.update({
            "running": True, "done": False, "error": None,
            "progress": 0, "total": len(indices), "current": "",
            "log": [],
        })

    t = threading.Thread(target=_run_compiler_thread, args=(indices, delete_sources), daemon=True)
    t.start()
    return JsonResponse({"ok": True, "total": len(indices)})


@staff_member_required(login_url="/web/login/")
def compiler_status(request):
    """GET — текущий статус компиляции (для polling)."""
    with _compiler_lock:
        s = dict(_compiler_state)
    s.pop("groups", None)
    s["folder"] = str(s.get("folder", ""))
    return JsonResponse(s)


# ── Синхронизация ─────────────────────────────────────────────────────────────

_sync_lock = threading.Lock()
_sync_state: dict = {
    "running": False, "done": False, "error": None,
    "processed": 0, "total": 0, "current": "",
    "stats": {},
    "log": [],
}
_sync_stop_event = threading.Event()
# {abs_path: genre_name} — папки с назначенным жанром в текущей сессии
_genre_assignments: dict = {}
_genre_assignments_lock = threading.Lock()


@staff_member_required(login_url="/web/login/")
def sync(request):
    with _sync_lock:
        state = dict(_sync_state)
    pct = 0
    if state["total"] > 0:
        pct = min(100, int(state["processed"] / state["total"] * 100))
    scan_path = request.GET.get("scan_path", "").strip()
    with _genre_assignments_lock:
        assignments = dict(_genre_assignments)
    return render(request, "fb2parser/sync.html", {
        "state": state, "pct": pct,
        "scan_path": scan_path,
        "assignments": assignments,
    })


@staff_member_required(login_url="/web/login/")
def sync_clear_assignments(request):
    """Сбросить список папок с назначенными жанрами (при смене scan_path)."""
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    with _genre_assignments_lock:
        _genre_assignments.clear()
    from django.http import HttpResponse
    return HttpResponse("ok")


def _run_sync_thread():
    from .fb2parser_bridge import get_sync_service
    from django import db
    db.connections.close_all()
    try:
        svc = get_sync_service()
        with _sync_lock:
            scan_path = _sync_state.get("scan_path")
            allowed_folders = _sync_state.get("allowed_folders")
        if scan_path:
            from pathlib import Path as _Path
            svc.last_scan_path = _Path(scan_path)
        log_lines = []

        def on_progress(cur, tot, msg):
            if _sync_stop_event.is_set():
                raise InterruptedError("Остановлено пользователем")
            with _sync_lock:
                _sync_state.update({"processed": cur, "total": tot, "current": msg})

        def on_log(msg):
            log_lines.append(msg)
            with _sync_lock:
                _sync_state["log"] = log_lines[-200:]

        stats = svc.synchronize(
            progress_callback=on_progress,
            log_callback=on_log,
            allowed_folders=allowed_folders,
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
        scan_path = request.POST.get("scan_path", "").strip() or None
        with _genre_assignments_lock:
            allowed = set(_genre_assignments.keys()) if _genre_assignments else None
        _sync_stop_event.clear()
        _sync_state.update({"running": True, "done": False, "error": None,
                            "processed": 0, "total": 0, "current": "",
                            "stats": {}, "log": [],
                            "scan_path": scan_path, "allowed_folders": allowed})
    threading.Thread(target=_run_sync_thread, daemon=True).start()
    return _render_sync_status(dict(_sync_state))


@staff_member_required(login_url="/web/login/")
def sync_status(request):
    with _sync_lock:
        state = dict(_sync_state)
    return _render_sync_status(state)


@staff_member_required(login_url="/web/login/")
def sync_stop(request):
    if request.method != "POST":
        from django.http import HttpResponseNotAllowed
        return HttpResponseNotAllowed(["POST"])
    _sync_stop_event.set()
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


# ── Настройки FB2Parser ───────────────────────────────────────────────────────

_SETTINGS_LISTS = {
    'filename_blacklist':         'Слова/фразы, не считающиеся названиями серий (жанровые термины и т.д.)',
    'service_words':              'Служебные слова, игнорируемые при анализе имён файлов',
    'sequence_patterns':          'Regex-шаблоны для распознавания номеров серий (напр. \\d+\\., книга\\s\\d+)',
    'abbreviations_preserve_case':'Аббревиатуры с сохранением регистра (СССР, РФ, США)',
    'author_initials_and_suffixes':'Суффиксы/инициалы авторов, игнорируемые при сравнении (мл, ст, ср)',
    'genre_category_words':       'Слова-категории серий для распознавания типа серии',
    'male_names':                 'Список мужских имён для определения пола автора',
    'female_names':               'Список женских имён для определения пола автора',
    'no_series_folder_names':     'Имена папок, означающих «без серии» (Вне серий, Без серии, standalone)',
}


@staff_member_required(login_url="/web/login/")
def fb2parser_settings(request):
    import json as _json
    from fb2parser_core.settings_manager import SettingsManager
    from .fb2parser_bridge import _config_path, _genres_path, _csv_dir
    sm = SettingsManager(_config_path())

    if request.method == 'POST':
        sm.set_library_path(request.POST.get('library_path', '').strip())
        genres = request.POST.get('genres_file_path', '').strip()
        sm.set_genres_file_path(genres or _genres_path())
        lim = request.POST.get('folder_parse_limit', '').strip()
        if lim.isdigit():
            sm.set_folder_parse_limit(int(lim))
        sm.set_generate_csv(request.POST.get('generate_csv') == 'on')
        norm_folder = request.POST.get('normalizer_folder', '').strip()
        sm.set_normalizer_folder(norm_folder or _csv_dir())
        sm.save()
        from django.shortcuts import redirect as _redir
        from django.urls import reverse as _rev
        return _redir(_rev('fb2parser:fb2parser_settings') + '?saved=1')

    ctx = _ctx("settings", "Настройки")
    ctx['saved'] = request.GET.get('saved') == '1'

    # Подставляем дефолты если пути не заданы
    genres_path = sm.get_genres_file_path()
    if not genres_path or genres_path in ('.', ''):
        genres_path = _genres_path()
        sm.set_genres_file_path(genres_path)
    norm_folder = sm.get_normalizer_folder()
    if not norm_folder:
        norm_folder = _csv_dir()
        sm.set_normalizer_folder(norm_folder)

    ctx['library_path']       = sm.get_library_path()
    ctx['genres_file_path']   = genres_path
    ctx['normalizer_folder']  = norm_folder
    ctx['folder_parse_limit'] = sm.get_folder_parse_limit()
    ctx['generate_csv']       = sm.get_generate_csv()
    ctx['lists_meta']         = list(_SETTINGS_LISTS.items())
    ctx['first_list_key']     = list(_SETTINGS_LISTS.keys())[0]
    ctx['lists_data_json']    = _json.dumps({k: sm.get_list(k) or [] for k in _SETTINGS_LISTS}, ensure_ascii=False)
    ctx['conversions']        = sm.get_author_surname_conversions() or {}
    return render(request, "fb2parser/settings.html", ctx)


@staff_member_required(login_url="/web/login/")
def settings_list_op(request):
    """POST {key, value, action:'add'|'delete'} → JSON."""
    import json as _json
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad JSON'}, status=400)
    key    = body.get('key', '')
    value  = (body.get('value') or '').strip()
    action = body.get('action', '')
    if key not in _SETTINGS_LISTS:
        return JsonResponse({'error': 'unknown key'}, status=400)
    if not value:
        return JsonResponse({'error': 'empty value'}, status=400)

    from fb2parser_core.settings_manager import SettingsManager
    from .fb2parser_bridge import _config_path
    sm = SettingsManager(_config_path())
    lst = list(sm.get_list(key) or [])
    if action == 'add':
        if value not in lst:
            lst.append(value)
            sm.set_list(key, lst)
    elif action == 'delete':
        lst = [x for x in lst if x != value]
        sm.set_list(key, lst)
    else:
        return JsonResponse({'error': 'unknown action'}, status=400)
    return JsonResponse({'ok': True, 'list': lst})


@staff_member_required(login_url="/web/login/")
def settings_conv_op(request):
    """POST {from_val, to_val?, action:'add'|'delete'} → JSON."""
    import json as _json
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    try:
        body = _json.loads(request.body)
    except Exception:
        return JsonResponse({'error': 'bad JSON'}, status=400)
    action   = body.get('action', '')
    from_val = (body.get('from_val') or '').strip()

    from fb2parser_core.settings_manager import SettingsManager
    from .fb2parser_bridge import _config_path
    sm = SettingsManager(_config_path())
    convs = dict(sm.get_author_surname_conversions() or {})

    if action == 'add':
        to_val = (body.get('to_val') or '').strip()
        if not from_val or not to_val:
            return JsonResponse({'error': 'both fields required'}, status=400)
        convs[from_val] = to_val
        sm.set_author_surname_conversions(convs)
    elif action == 'delete':
        if not from_val:
            return JsonResponse({'error': 'from_val required'}, status=400)
        convs.pop(from_val, None)
        sm.set_author_surname_conversions(convs)
    else:
        return JsonResponse({'error': 'unknown action'}, status=400)
    return JsonResponse({'ok': True, 'conversions': convs})
