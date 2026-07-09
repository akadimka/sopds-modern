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
    recent_books = Book.objects.order_by("-id")[:10]
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
    """Возвращает HTML-список подпапок для folder picker."""
    path = request.GET.get("path", "").strip()
    target_input = request.GET.get("target", "")  # id поля, которое заполняем

    entries = []
    error = None
    parent = None

    if not path:
        # Показываем корни: диски на Windows, / на Linux
        import string
        if os.name == "nt":
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
            entries = [{"name": d, "path": d, "is_drive": True} for d in drives]
        else:
            path = "/"

    if path:
        try:
            parent = str(os.path.dirname(path.rstrip("/\\")) or path)
            if parent == path.rstrip("/\\"):
                parent = None  # уже на корне
            entries = []
            for name in sorted(os.listdir(path), key=str.lower):
                full = os.path.join(path, name)
                if os.path.isdir(full):
                    entries.append({"name": name, "path": full, "is_drive": False})
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
            yield f"data: {payload}\n\n"
        yield "data: {\"done\": true}\n\n"

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


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
