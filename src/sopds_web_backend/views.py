import logging
import threading

from opds_catalog.sopds_config import sopds_cfg as config
from django.contrib.auth import REDIRECT_FIELD_NAME, authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.template.context_processors import csrf
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods
from django.views.decorators.vary import vary_on_headers

from opds_catalog.models import (
    Author,
    Book,
    Catalog,
    Genre,
    Series,
    bookshelf,
    bseries,
    lang_menu,
)
from opds_catalog.services import (
    authors_services,
    book_services,
    catalog_services,
    genre_services,
    series_services,
)
from opds_catalog.services.catalog_services import DUMMY_CATALOG
from opds_catalog.utils import to_int

logger = logging.getLogger(__name__)

# ── Состояние сканирования SOPDS ─────────────────────────────────────────────
_sopds_scan_lock = threading.Lock()
_sopds_scan_state: dict = {"running": False, "done": False, "error": None}


def _run_sopds_scan():
    import logging
    import traceback as _tb
    from opds_catalog.sopdscan import opdsScanner
    from opds_catalog.sopds_config import sopds_cfg as _cfg
    from django import db
    db.connections.close_all()
    root = _cfg.SOPDS_ROOT_LIB
    with _sopds_scan_lock:
        _sopds_scan_state.update({"running": True, "done": False, "error": None,
                                  "root": root, "added": 0, "bad": 0, "deleted": 0,
                                  "first_error": None})

    # Capture first error from scanner logger
    _first_err = []
    class _ErrCapture(logging.Handler):
        def emit(self, record):
            if record.levelno >= logging.ERROR and not _first_err:
                _first_err.append(self.format(record))
    _handler = _ErrCapture()
    _log = logging.getLogger("scanner")
    _log.addHandler(_handler)

    try:
        from opds_catalog.opdsdb import clear_all
        from django.core.management import call_command
        clear_all()
        call_command("loaddata", "genre", app_label="opds_catalog", verbosity=0)
        scanner = opdsScanner()
        scanner.scan_all()
        with _sopds_scan_lock:
            _sopds_scan_state.update({
                "running": False, "done": True, "error": None,
                "added": scanner.books_added,
                "bad": scanner.bad_books,
                "deleted": scanner.books_deleted,
                "first_error": _first_err[0] if _first_err else None,
            })
    except Exception as e:
        with _sopds_scan_lock:
            _sopds_scan_state.update({"running": False, "done": True,
                                      "error": str(e), "traceback": _tb.format_exc(),
                                      "first_error": _first_err[0] if _first_err else None})
    finally:
        _log.removeHandler(_handler)


def sopds_login(function=None, redirect_field_name=REDIRECT_FIELD_NAME, url=None):
    actual_decorator = user_passes_test(
        lambda u: u.is_authenticated if config.SOPDS_AUTH else True,
        login_url=reverse_lazy(url),
        redirect_field_name=redirect_field_name,
    )
    if function:
        return actual_decorator(function)
    return actual_decorator


def _extract_input_parameters(request) -> dict[str, str]:
    args = {}
    args["searchtype"] = request.GET.get("searchtype", "m")
    args["searchterms"] = request.GET.get("searchterms", "")
    args["searchterms0"] = request.GET.get("searchterms0")
    args["page_num"] = request.GET.get("page")
    args["user"] = request.user.username
    return args


# Create your views here.
@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
def SearchBooksView(request):
    """Диспетчер для обработки параемтров запроса книг."""
    SOPDS_AUTH = config.SOPDS_AUTH
    args = {}
    args.update(csrf(request))
    if request.GET:
        args.update(_extract_input_parameters(request))

        books = book_services.search_book(
            args["searchtype"], args["searchterms"], args["searchterms0"], args["user"]
        )
        book_url = reverse("web:book")
        _books_root = {"label": _("Books"), "url": book_url}

        if args["searchtype"] in ("m", "b"):
            if args["searchtype"] == "b":
                lang_code = to_int(request.GET.get("lang"), 0)
                lang_label = lang_menu.get(lang_code, _("Select"))
                back_url = f"{book_url}?lang={lang_code}" if lang_code else f"{book_url}?lang=0"
                args["breadcrumbs"] = [
                    _books_root,
                    {"label": lang_label, "url": back_url},
                    {"label": args["searchterms"], "url": ""},
                ]
            else:
                args["breadcrumbs"] = [
                    _books_root,
                    {"label": _("Search by title"), "url": ""},
                    {"label": args["searchterms"], "url": ""},
                ]
            args["searchobject"] = "title"

        elif args["searchtype"] == "a":
            aname = authors_services.get_author_name(id=args["searchterms"])
            args["breadcrumbs"] = [_books_root, {"label": _("Search by author"), "url": ""}, {"label": aname, "url": ""}]
            args["searchobject"] = "author"

        # Поиск книг по серии
        elif args["searchtype"] == "s":
            ser = series_services.get_series_name(args["searchterms"])
            args["breadcrumbs"] = [_books_root, {"label": _("Search by series"), "url": ""}, {"label": ser, "url": ""}]
            args["searchobject"] = "series"

        # Поиск книг по жанру
        elif args["searchtype"] == "g":
            try:
                genre = Genre.objects.get(id=args["searchterms"])
                section = genre.section
                subsection = genre.subsection
                args["breadcrumbs"] = [
                    _books_root,
                    {"label": _("Search by genre"), "url": ""},
                    {"label": section, "url": ""},
                    {"label": subsection, "url": ""},
                ]
            except:
                args["breadcrumbs"] = [_books_root, {"label": _("Search by genre"), "url": ""}]

            args["searchobject"] = "genre"

        # Поиск книг на книжной полке
        elif args["searchtype"] == "u":
            # if config.SOPDS_AUTH:
            if SOPDS_AUTH:
                books = Book.objects.filter(bookshelf__user=request.user).order_by(
                    "-bookshelf__readtime"
                )
                args["breadcrumbs"] = [
                    _books_root,
                    {"label": _("Bookshelf"), "url": ""},
                    {"label": args["user"], "url": ""},
                ]
                # books = bookshelf.objects.filter(user=request.user).select_related('book')
            else:
                books = Book.objects.filter(id=0)
                args["breadcrumbs"] = [_books_root, {"label": _("Bookshelf"), "url": ""}]
            args["searchobject"] = "title"
            args["isbookshelf"] = 1

        # Поиск дубликатов для книги
        elif args["searchtype"] == "d":
            book_id = int(args["searchterms"])  # type: ignore[call-overload]
            mbook = Book.objects.get(id=book_id)
            books = (
                Book.objects.filter(title=mbook.title, authors__in=mbook.authors.all())
                .exclude(id=book_id)
                .distinct()
                .order_by("-docdate")
            )
            args["breadcrumbs"] = [_books_root, {"label": _("Doubles for book"), "url": ""}, {"label": mbook.title, "url": ""}]
            args["searchobject"] = "title"

        # Поиск книги по ID
        elif args["searchtype"] == "i":
            book_id = to_int(args["searchterms"], 0)
            books = Book.objects.filter(id=book_id)
            try:
                args["breadcrumbs"] = [_books_root, {"label": books[0].title, "url": ""}]
            except IndexError:
                args["breadcrumbs"] = [_books_root]
            args["searchobject"] = "title"

        page_num = to_int(args.get("page_num"), 1)
        items, op = book_services.paginated_book_content(
            books,
            page_num,
            search_doubles=(args["searchtype"] != "d"),
            user=request.user,
            auth_enabled=SOPDS_AUTH,
        )

        args["paginator"] = op
        args["searchterms"] = args["searchterms"]
        args["searchtype"] = args["searchtype"]
        args["books"] = items
        args["current"] = "search"
        args["cache_id"] = "%s:%s:%s" % (
            args["searchterms"],
            args["searchtype"],
            op["number"],
        )

        if args["searchtype"] == "u":
            args["cache_t"] = 0
        else:
            args["cache_t"] = config.SOPDS_CACHE_TIME

    return render(request, "sopds_books.html", args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
def SearchSeriesView(request):
    # Read searchtype, searchterms, searchterms0, page from form
    args = {}
    args.update(csrf(request))

    if request.GET:
        searchtype = request.GET.get("searchtype", "m")
        searchterms = request.GET.get("searchterms", "")
        # searchterms0 = int(request.POST.get('searchterms0', ''))
        page_num = int(request.GET.get("page", "1"))
        page_num = page_num if page_num > 0 else 1

        series = series_services.search_series(searchtype, searchterms)

        # Создаем результирующее множество
        paginator = Paginator(series, config.SOPDS_MAXITEMS)
        try:
            page = paginator.page(page_num)
        except (EmptyPage, PageNotAnInteger):
            page = paginator.page(paginator.num_pages)
        items = []
        for row in page.object_list:
            p = {
                "id": row.id,
                "ser": row.ser,
                "lang_code": row.lang_code,
                "book_count": row.count_book,
            }
            items.append(p)

        args["paginator"] = {
            "num_pages": paginator.num_pages,
            "has_previous": page.has_previous(),
            "has_next": page.has_next(),
            "previous_page_number": page.previous_page_number()
            if page.has_previous()
            else 1,
            "next_page_number": page.next_page_number()
            if page.has_next()
            else paginator.num_pages,
            "number": page.number,
            "page_range": list(paginator.page_range),
        }
        args["searchterms"] = searchterms
        args["searchtype"] = searchtype
        args["series"] = items
        args["searchobject"] = "series"
        args["current"] = "search"
        series_url = reverse("web:series")
        lang_code = to_int(request.GET.get("lang"), 0)
        lang_label = lang_menu.get(lang_code, _("Select"))
        back_url = f"{series_url}?lang={lang_code}" if lang_code else f"{series_url}?lang=0"
        args["breadcrumbs"] = [
            {"label": _("Series"), "url": f"{series_url}?lang=0"},
            {"label": lang_label, "url": back_url},
            {"label": searchterms, "url": ""},
        ]
        args["cache_id"] = "%s:%s:%s" % (searchterms, searchtype, page.number)
        args["cache_t"] = config.SOPDS_CACHE_TIME

    return render(request, "sopds_series.html", args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
def SearchAuthorsView(request):
    # Read searchtype, searchterms, searchterms0, page from form
    args = {}
    args.update(csrf(request))

    if request.GET:
        searchtype = request.GET.get("searchtype", "m")
        searchterms = request.GET.get("searchterms", "")
        # searchterms0 = int(request.POST.get('searchterms0', ''))
        page_num = int(request.GET.get("page", "1"))
        page_num = page_num if page_num > 0 else 1

        authors = authors_services.search_authors_with_counts(searchtype, searchterms)
        paginator = Paginator(authors, config.SOPDS_MAXITEMS)
        try:
            page = paginator.page(page_num)
        except (EmptyPage, PageNotAnInteger):
            page = paginator.page(paginator.num_pages)
        items = []

        for row in page.object_list:
            p = {
                "id": row.id,
                "full_name": row.full_name,
                "lang_code": row.lang_code,
                "book_count": row.book_count,
            }
            items.append(p)

        args["paginator"] = {
            "num_pages": paginator.num_pages,
            "has_previous": page.has_previous(),
            "has_next": page.has_next(),
            "previous_page_number": page.previous_page_number()
            if page.has_previous()
            else 1,
            "next_page_number": page.next_page_number()
            if page.has_next()
            else paginator.num_pages,
            "number": page.number,
            "page_range": list(paginator.page_range),
        }
        args["searchterms"] = searchterms
        args["searchtype"] = searchtype
        args["authors"] = items
        args["searchobject"] = "author"
        args["current"] = "search"
        author_url = reverse("web:author")
        lang_code = to_int(request.GET.get("lang"), 0)
        lang_label = lang_menu.get(lang_code, _("Select"))
        back_url = f"{author_url}?lang={lang_code}" if lang_code else author_url
        args["breadcrumbs"] = [
            {"label": _("Authors"), "url": author_url},
            {"label": lang_label, "url": back_url},
            {"label": searchterms, "url": ""},
        ]
        args["cache_id"] = "%s:%s:%s" % (searchterms, searchtype, page.number)
        args["cache_t"] = config.SOPDS_CACHE_TIME

    return render(request, "sopds_authors.html", args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
def CatalogsView(request):
    args = {}

    if request.GET:
        cat_id = request.GET.get("cat", None)
        page_num = int(request.GET.get("page", "1"))
    else:
        cat_id = None
        page_num = 1

    try:
        if cat_id is not None:
            cat = Catalog.objects.get(id=cat_id)
        else:
            cat = Catalog.objects.get(parent__id=cat_id)
    except Catalog.DoesNotExist:
        cat = None

    items, op = catalog_services.paginated_catalog_content(
        cat or DUMMY_CATALOG,
        page_num,
        config.SOPDS_MAXITEMS,
        user=request.user,
        auth_enabled=config.SOPDS_AUTH,
    )

    args["paginator"] = op
    args["items"] = items
    args["cat_id"] = cat_id
    args["current"] = "catalog"

    breadcrumbs_list = []
    if cat:
        while cat.parent:
            breadcrumbs_list.insert(0, (cat.cat_name, cat.id))
            cat = cat.parent
        breadcrumbs_list.insert(0, (_("ROOT"), 0))
    # breadcrumbs_list.insert(0, (_('Catalogs'),-1))
    args["breadcrumbs_cat"] = breadcrumbs_list
    args["breadcrumbs"] = [{"label": _("Catalogs"), "url": ""}]
    args["cache_id"] = "%s:%s:%s" % (args["current"], cat_id, op["number"])
    args["cache_t"] = config.SOPDS_CACHE_TIME

    return render(request, "sopds_catalogs.html", args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
def BooksView(request):
    args = {}
    book_url = reverse("web:book")

    if "lang" not in request.GET:
        args["breadcrumbs"] = [{"label": _("Books"), "url": ""}]
        args["lang_menu"] = lang_menu
        args["picker_url"] = book_url
        args["current"] = "book"
        return render(request, "sopds_langpicker.html", args)

    lang_code = to_int(request.GET.get("lang"))
    chars = request.GET.get("chars", "")

    length = len(chars) + 1

    items = book_services.find_books_by_template(chars, length, lang_code)

    args["items"] = items
    args["current"] = "book"
    args["lang_code"] = lang_code
    crumbs = [{"label": _("Books"), "url": book_url}]
    if chars:
        crumbs.append({"label": lang_menu[lang_code], "url": f"{book_url}?lang={lang_code}"})
        crumbs.append({"label": chars, "url": ""})
    else:
        crumbs.append({"label": lang_menu[lang_code], "url": ""})
    args["breadcrumbs"] = crumbs
    args["cache_id"] = "%s:%s:%s" % (args["current"], lang_code, chars)
    args["cache_t"] = config.SOPDS_CACHE_TIME

    return render(request, "sopds_selectbook.html", args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
def AuthorsView(request):
    args = {}
    author_url = reverse("web:author")

    if "lang" not in request.GET:
        args["breadcrumbs"] = [{"label": _("Authors"), "url": ""}]
        args["lang_menu"] = lang_menu
        args["picker_url"] = author_url
        args["current"] = "author"
        return render(request, "sopds_langpicker.html", args)

    lang_code = to_int(request.GET.get("lang"))
    chars = request.GET.get("chars", "")

    length = len(chars) + 1

    items = authors_services.find_authors_by_template(chars, length, lang_code)

    args["items"] = items
    args["current"] = "author"
    args["lang_code"] = lang_code
    crumbs = [{"label": _("Authors"), "url": author_url}]
    if chars:
        crumbs.append({"label": lang_menu[lang_code], "url": f"{author_url}?lang={lang_code}"})
        crumbs.append({"label": chars, "url": ""})
    else:
        crumbs.append({"label": lang_menu[lang_code], "url": ""})
    args["breadcrumbs"] = crumbs
    args["cache_id"] = "%s:%s:%s" % (args["current"], lang_code, chars)
    args["cache_t"] = config.SOPDS_CACHE_TIME

    return render(request, "sopds_selectauthor.html", args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
def SeriesView(request):
    args = {}

    if request.GET:
        lang_code = int(request.GET.get("lang", "0"))
        chars = request.GET.get("chars", "")
    else:
        lang_code = 0
        chars = ""

    length = len(chars) + 1

    items = series_services.get_series(chars, length, lang_code)

    args["items"] = items
    args["current"] = "series"
    args["lang_code"] = lang_code
    series_url = reverse("web:series")
    crumbs = [{"label": _("Series"), "url": f"{series_url}?lang=0"}]
    if chars:
        crumbs.append({"label": lang_menu[lang_code], "url": f"{series_url}?lang={lang_code}"})
        crumbs.append({"label": chars, "url": ""})
    else:
        crumbs.append({"label": lang_menu[lang_code], "url": ""})
    args["breadcrumbs"] = crumbs
    args["cache_id"] = "%s:%s:%s" % (args["current"], lang_code, chars)
    args["cache_t"] = config.SOPDS_CACHE_TIME

    return render(request, "sopds_selectseries.html", args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
def GenresView(request):
    args = {}

    if request.GET:
        section_id = to_int(request.GET.get("section"))
    else:
        section_id = 0

    genre_url = reverse("web:genre")
    if section_id == 0:
        items = genre_services.get_genres()
        args["breadcrumbs"] = [{"label": _("Genres"), "url": ""}]
    else:
        section = genre_services.get_genre_section(section_id)
        items = genre_services.get_genre_details(section_id)
        args["breadcrumbs"] = [
            {"label": _("Genres"), "url": genre_url},
            {"label": section, "url": ""},
        ]

    args["items"] = items
    args["current"] = "genre"
    args["parent_id"] = section_id
    args["cache_id"] = "%s:%s" % (args["current"], section_id)
    args["cache_t"] = config.SOPDS_CACHE_TIME

    return render(request, "sopds_selectgenres.html", args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
# g @sopds_login(url="web:login")
def SearchSuggestView(request):
    """Подсказки для строки поиска через htmx."""
    logger.critical("Suggestion helper")

    if request.method == "POST":
        q = request.POST.get("searchterms", "").strip()
        search_type = request.POST.get("type", "title")
    elif request.method == "GET":
        q = request.GET.get("searchterms", "").strip()
        search_type = request.GET.get("type", "title")
    else:
        return HttpResponseNotAllowed(["GET", "POST"])

    logger.info(f"Suggestion request '{q}' type '{search_type}'")

    if len(q) < 2:
        logger.info("suggestion query too short")
        return HttpResponse("")

    if search_type == "title":
        logger.info("Suggest books by title '{q}'")
        items = Book.objects.filter(search_title__contains=q.upper())[:10]
    elif search_type == "author":
        items = Author.objects.filter(search_full_name__contains=q.upper())[:10]
    elif search_type == "series":
        items = Series.objects.filter(search_ser__contains=q.upper())[:10]
    else:
        items = []

    return render(
        request,
        "sopds_search_suggestions.html",
        {"items": items, "search_type": search_type},
    )


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
@require_http_methods(["GET", "DELETE"])
def BSDelView(request):
    if request.GET:
        book = request.GET.get("book", None)
    else:
        book = None

    book = int(book)

    bookshelf.objects.filter(user=request.user, book=book).delete()

    if request.headers.get("HX-Request") == "true":
        return HttpResponse(
            status=200,
            headers={
                "HX-Redirect": "%s?searchtype=u&page=1" % reverse("web:searchbooks")
            },
        )

    return redirect("%s?searchtype=u" % reverse("web:searchbooks"))


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
def BSClearView(request):
    bookshelf.objects.filter(user=request.user).delete()
    return redirect("%s?searchtype=u" % reverse("web:searchbooks"))


def sopds_scan_start(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    with _sopds_scan_lock:
        already = _sopds_scan_state["running"]
    if not already:
        t = threading.Thread(target=_run_sopds_scan, daemon=True)
        t.start()
    return _render_sopds_scan_status(request)


def sopds_scan_status(request):
    return _render_sopds_scan_status(request)


def _render_sopds_scan_status(request):
    with _sopds_scan_lock:
        state = dict(_sopds_scan_state)
    return render(request, "sopds_scan_status.html", {"state": state})


@require_http_methods(["GET", "POST"])
def sopds_settings(request):
    import os
    from fb2parser_core.settings_manager import SettingsManager
    _config_path = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "fb2_data", "settings", "config.json")
    )
    sm = SettingsManager(_config_path)

    _BOOL_FIELDS = [
        'auth', 'alphabet_menu', 'doubles_hide', 'title_as_filename',
        'fb2sax', 'zipscan', 'inpx_enable', 'inpx_skip_unchanged',
        'inpx_test_zip', 'inpx_test_files', 'delete_logical', 'scan_start_directly',
    ]
    _INT_FIELDS = ['maxitems', 'splititems', 'scan_shed_day', 'scan_shed_dow', 'scan_shed_hour', 'scan_shed_min']
    _STR_FIELDS = ['root_lib', 'book_extensions', 'fb2toepub', 'fb2tomobi', 'fb2toazw3', 'temp_dir', 'scanner_pid', 'scanner_log', 'language']

    if request.method == 'POST':
        sopds = sm.settings.setdefault('sopds', {})
        for f in _BOOL_FIELDS:
            sopds[f] = request.POST.get(f) == 'on'
        for f in _INT_FIELDS:
            try:
                sopds[f] = int(request.POST.get(f, 0))
            except ValueError:
                pass
        for f in _STR_FIELDS:
            sopds[f] = request.POST.get(f, '').strip()
        sm.save()
        return redirect(reverse('web:settings') + '?saved=1')

    sopds = sm.settings.get('sopds', {})
    comments = sopds.get('_comments', {})
    args = {
        'breadcrumbs': [_('Settings')],
        'saved': request.GET.get('saved') == '1',
        'sopds': sopds,
        'comments': comments,
    }
    return render(request, 'sopds_settings.html', args)


def service_worker(request):
    """Отдать Service Worker с корневого URL /sw.js (нужен для правильного scope)."""
    import os
    sw_path = os.path.join(os.path.dirname(__file__), 'static', 'js', 'sw.js')
    with open(sw_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return HttpResponse(content, content_type='application/javascript')


def offline_page(request):
    return render(request, 'sopds_offline.html')


def hello(request):
    from django.db.models import Count

    args = {}
    args["breadcrumbs"] = []
    args["stats"] = {
        "allbooks":   Book.objects.count(),
        "allauthors": Author.objects.count(),
        "allgenres":  Genre.objects.count(),
    }
    args["top_genres"]   = Genre.objects.annotate(cnt=Count("bgenre")).order_by("-cnt")[:5]
    args["recent_books"] = Book.objects.order_by("-id").prefetch_related("genres")[:10]
    args["random_book"]  = Book.objects.order_by("?").first()
    return render(request, "sopds_hello.html", args)


@sopds_login(url="web:login")
def book_card(request, book_id):
    from opds_catalog.models import Book
    from django.http import Http404
    try:
        book = Book.objects.prefetch_related("authors", "genres", "series").get(pk=book_id)
    except Book.DoesNotExist:
        raise Http404
    ser_no = None
    first_series = book.series.first()
    if first_series:
        bs = bseries.objects.filter(book=book, ser=first_series).first()
        ser_no = bs.ser_no if bs else None
    return render(request, "sopds_book_card.html", {
        "b": book,
        "ser_no": ser_no,
    })


def LoginView(request):
    args = {}
    args["breadcrumbs"] = [_("Login")]
    args.update(csrf(request))
    try:
        username = request.POST["username"]
        password = request.POST["password"]
    except KeyError:
        return render(request, "sopds_login.html", args)

    next_url = request.GET.get("next", reverse("web:main"))

    # Валидация next_url для предотвращения open redirect
    allowed_hosts = {request.get_host()}
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts=allowed_hosts,
        require_https=False,
    ):
        next_url = reverse("web:main")

    user = authenticate(username=username, password=password)
    if user is not None:
        if user.is_active:
            login(request, user)
            return redirect(next_url)
        else:
            args["system_message"] = {
                "text": _("This account is not active!"),
                "type": "alert",
            }
            return handler403(request, args)
    else:
        args["system_message"] = {
            "text": _("User does not exist or the password is incorrect!"),
            "type": "alert",
        }
        return handler403(request, args)


@vary_on_headers("HTTP_ACCEPT_LANGUAGE")
@sopds_login(url="web:login")
def LogoutView(request):
    logout(request)
    args = {}
    args["breadcrumbs"] = [_("Logout")]
    return redirect(reverse("web:main"))


def handler403(request, args):
    response = render(request, "sopds_login.html", args)
    response.status_code = 403
    return response
