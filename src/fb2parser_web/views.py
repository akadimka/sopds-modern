from django.contrib.admin.views.decorators import staff_member_required
from django.shortcuts import render


def _ctx(page_id, title):
    return {"fb2_page": page_id, "fb2_title": title}


@staff_member_required(login_url="/web/login/")
def dashboard(request):
    return render(request, "fb2parser/dashboard.html", _ctx("dashboard", "Статистика"))


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
