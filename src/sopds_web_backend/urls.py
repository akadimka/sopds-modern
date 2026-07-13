# from django.conf.urls import url
from django.urls import re_path

from sopds_web_backend import views

app_name = "opds_web_backend"

urlpatterns = [
    re_path(r"^search/suggest/$", views.SearchSuggestView, name="suggest"),
    re_path(r"^search/books/$", views.SearchBooksView, name="searchbooks"),
    re_path(r"^search/authors/$", views.SearchAuthorsView, name="searchauthors"),
    re_path(r"^search/series/$", views.SearchSeriesView, name="searchseries"),
    re_path(r"^catalog/$", views.CatalogsView, name="catalog"),
    re_path(r"^book/$", views.BooksView, name="book"),
    re_path(r"^author/$", views.AuthorsView, name="author"),
    re_path(r"^genre/$", views.GenresView, name="genre"),
    re_path(r"^series/$", views.SeriesView, name="series"),
    re_path(r"^login/$", views.LoginView, name="login"),
    re_path(r"^logout/$", views.LogoutView, name="logout"),
    re_path(r"^bs/delete/$", views.BSDelView, name="bsdel"),
    re_path(r"^bs/clear/$", views.BSClearView, name="bsclear"),
    re_path(r"^$", views.hello, name="main"),
    re_path(r"^scan/start/$", views.sopds_scan_start, name="scan_start"),
    re_path(r"^scan/status/$", views.sopds_scan_status, name="scan_status"),
    re_path(r"^settings/$", views.sopds_settings, name="settings"),
    re_path(r"^offline/$", views.offline_page, name="offline"),
]

# handler403 = 'views.handler403'
