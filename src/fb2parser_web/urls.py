from django.urls import path
from fb2parser_web import views

app_name = "fb2parser"

urlpatterns = [
    path("",              views.dashboard,   name="dashboard"),
    path("scan/",              views.scan,        name="scan"),
    path("scan/start/",        views.scan_start,  name="scan_start"),
    path("scan/status/",       views.scan_status,  name="scan_status"),
    path("normalize/",         views.normalize,        name="normalize"),
    path("normalize/start/",   views.normalize_start,  name="normalize_start"),
    path("normalize/status/",  views.normalize_status, name="normalize_status"),
    path("sync/",              views.sync,             name="sync"),
    path("sync/start/",        views.sync_start,       name="sync_start"),
    path("sync/status/",       views.sync_status,      name="sync_status"),
    path("genre-assign/",        views.genre_assign,        name="genre_assign"),
    path("genre-assign/start/",  views.genre_assign_start,  name="genre_assign_start"),
    path("genre-assign/status/", views.genre_assign_status, name="genre_assign_status"),
    path("archive/",      views.archive,     name="archive"),
    path("database/",     views.database,    name="database"),
    path("genres/",       views.genres,      name="genres"),
    path("log/",          views.log,         name="log"),
    path("search/",       views.search,      name="search"),
    path("new-books/",    views.new_books,   name="new_books"),
    path("series-gaps/",  views.series_gaps, name="series_gaps"),
    path("integrity/",    views.integrity,   name="integrity"),
]
