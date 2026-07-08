from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render

from opds_catalog.models import Author, Book, Catalog, Counter, Genre, Series


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
    return render(request, "fb2parser/scan.html", _ctx("scan", "Сканирование"))


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
