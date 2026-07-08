import logging
import os
import threading

from constance import config
from django.contrib.admin.views.decorators import staff_member_required
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

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
}


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

        scanner = _TrackingScanner(logging.getLogger("fb2parser.scan"))
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


@require_POST
@staff_member_required(login_url="/web/login/")
def scan_start(request):
    with _scan_lock:
        if _scan_state["running"]:
            return HttpResponse("Сканирование уже запущено", status=400)
        root = request.POST.get("root", config.SOPDS_ROOT_LIB or "").strip()
        if not root or not os.path.isdir(root):
            return HttpResponse(f"Папка не найдена: {root}", status=400)
        _scan_state.update({
            "running": True, "done": False, "error": None,
            "processed": 0, "total": 0, "current": "",
            "books_added": 0, "books_skipped": 0, "bad_books": 0,
        })

    t = threading.Thread(target=_run_scan_thread, args=(root,), daemon=True)
    t.start()
    return _scan_status_partial()


def scan_status(request):
    return _scan_status_partial()


def _scan_status_partial():
    with _scan_lock:
        state = dict(_scan_state)
    pct = 0
    if state["total"] > 0:
        pct = min(100, int(state["processed"] / state["total"] * 100))
    return render(None, "fb2parser/scan_status.html", {"state": state, "pct": pct},
                  using=None) if False else \
        _render_status(state, pct)


def _render_status(state, pct):
    from django.template.loader import render_to_string
    html = render_to_string("fb2parser/scan_status.html", {"state": state, "pct": pct})
    return HttpResponse(html)


@staff_member_required(login_url="/web/login/")
def normalize(request):
    return render(request, "fb2parser/normalize.html", _ctx("normalize", "Нормализация"))


@staff_member_required(login_url="/web/login/")
def sync(request):
    return render(request, "fb2parser/sync.html", _ctx("sync", "Синхронизация"))


@staff_member_required(login_url="/web/login/")
def archive(request):
    return render(request, "fb2parser/archive.html", _ctx("archive", "Заархивировать"))


@staff_member_required(login_url="/web/login/")
def database(request):
    return render(request, "fb2parser/database.html", _ctx("database", "База данных"))


@staff_member_required(login_url="/web/login/")
def genres(request):
    return render(request, "fb2parser/genres.html", _ctx("genres", "Менеджер жанров"))


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
