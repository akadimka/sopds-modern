"""Тесты для views.py и processors.py sopds_web_backend."""

from datetime import datetime

import pytest
from django.contrib.auth.models import AnonymousUser
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from opds_catalog.models import Book, Counter, Genre, bauthor, bookshelf


@pytest.fixture
def counter_with_books(db) -> None:
    """Counter с 'allbooks' для работы sopds_processor."""
    Counter.obj.create(
        name="allbooks",
        value=0,
        update_time=timezone.make_aware(datetime(2024, 1, 1)),
    )


# ──────────────────────────────────────────────
# Вспомогательные функции (без БД)
# ──────────────────────────────────────────────


class TestGetBreadcrumbs:
    def test_basic(self) -> None:
        from sopds_web_backend.views import get_breadcrumbs

        result = get_breadcrumbs("m")
        assert result[0] == "Books"

    def test_with_append(self) -> None:
        from sopds_web_backend.views import get_breadcrumbs

        result = get_breadcrumbs("m", append="test")
        assert result[-1] == "test"


class TestExtractInputParameters:
    def test_with_params(self, rf) -> None:
        from sopds_web_backend.views import _extract_input_parameters

        request = rf.get("/?searchtype=a&searchterms=test&searchterms0=extra&page=3")
        request.user = AnonymousUser()
        result = _extract_input_parameters(request)
        assert result["searchtype"] == "a"
        assert result["page_num"] == "3"

    def test_defaults(self, rf) -> None:
        from sopds_web_backend.views import _extract_input_parameters

        request = rf.get("/")
        request.user = AnonymousUser()
        result = _extract_input_parameters(request)
        assert result["searchtype"] == "m"
        assert result["searchterms"] == ""


# ──────────────────────────────────────────────
# hello, LoginView, LogoutView
# ──────────────────────────────────────────────


@pytest.mark.django_db
class TestHello:
    def test_hello_ok(self, client, counter_with_books) -> None:
        response = client.get(reverse("web:main"))
        assert response.status_code == 200

    def test_random_book_does_not_sort_whole_table(
        self, client, django_user, counter_with_books, catalog
    ) -> None:
        """Баг №104: order_by("?") на SQLite — ORDER BY RANDOM(), полная
        сортировка ВСЕЙ таблицы книг на каждый заход на главную страницу."""
        for i in range(5):
            Book.objects.create(
                filename=f"book{i}.fb2", path=".", format="fb2", cat_type=0,
                title=f"Книга {i}", search_title=f"КНИГА {i}", catalog=catalog,
            )

        client.force_login(django_user)
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(reverse("web:main"))

        assert response.status_code == 200
        # Django/SQLite компилируют order_by("?") в "ORDER BY RAND()" —
        # не встроенная RANDOM() SQLite, а функция, которую регистрирует
        # сам Django-бэкенд; в любом случае требует полной сортировки.
        random_sort_queries = [
            q for q in ctx.captured_queries
            if "opds_catalog_book" in q["sql"] and "RAND(" in q["sql"].upper()
        ]
        assert random_sort_queries == [], (
            f"Найден запрос со случайной сортировкой всей таблицы: {random_sort_queries}"
        )


@pytest.mark.django_db
class TestLoginView:
    def test_get(self, client, counter_with_books) -> None:
        response = client.get(reverse("web:login"))
        assert response.status_code == 200

    def test_post_valid(self, client, django_user, counter_with_books) -> None:
        response = client.post(
            reverse("web:login"), {"username": "test", "password": "secret"}
        )
        assert response.status_code == 302

    def test_post_invalid(self, client, counter_with_books) -> None:
        response = client.post(
            reverse("web:login"), {"username": "test", "password": "wrong"}
        )
        assert response.status_code == 403

    def test_post_inactive(self, client, django_user, counter_with_books) -> None:
        django_user.is_active = False
        django_user.save()
        response = client.post(
            reverse("web:login"), {"username": "test", "password": "secret"}
        )
        assert response.status_code == 403


@pytest.mark.django_db
class TestLogoutView:
    def test_logout(self, client, django_user, counter_with_books) -> None:
        client.force_login(django_user)
        response = client.get(reverse("web:logout"))
        assert response.status_code == 302


# ──────────────────────────────────────────────
# SearchBooksView
# ──────────────────────────────────────────────


@pytest.mark.django_db
class TestSearchBooksView:
    def _create_book(self, catalog, title="КНИГА") -> Book:
        """Создаёт книгу с указанным search_title."""
        return Book.objects.create(
            filename="test.fb2",
            path=".",
            format="fb2",
            search_title=title,
            catalog=catalog,
        )

    def test_get(self, client, django_user, counter_with_books) -> None:
        client.force_login(django_user)
        response = client.get(reverse("web:searchbooks"))
        assert response.status_code == 200


# ──────────────────────────────────────────────
# SearchSeriesView
# ──────────────────────────────────────────────


@pytest.mark.django_db
class TestSearchSeriesView:
    def test_contains(self, client, django_user, counter_with_books, series) -> None:
        client.force_login(django_user)
        response = client.get(
            reverse("web:searchseries"),
            {"searchtype": "m", "searchterms": "MYWORK"},
        )
        assert response.status_code == 200

    def test_startswith(self, client, django_user, counter_with_books, series) -> None:
        client.force_login(django_user)
        response = client.get(
            reverse("web:searchseries"),
            {"searchtype": "b", "searchterms": "MYWORK"},
        )
        assert response.status_code == 200

    def test_exact(self, client, django_user, counter_with_books, series) -> None:
        client.force_login(django_user)
        response = client.get(
            reverse("web:searchseries"),
            {"searchtype": "e", "searchterms": "MYWORK"},
        )
        assert response.status_code == 200


# ──────────────────────────────────────────────
# SearchAuthorsView
# ──────────────────────────────────────────────


@pytest.mark.django_db
class TestSearchAuthorsView:
    def test_search(self, client, django_user, counter_with_books, author) -> None:
        client.force_login(django_user)
        response = client.get(
            reverse("web:searchauthors"),
            {"searchtype": "m", "searchterms": "ШЕЛЕПНЕВ"},
        )
        assert response.status_code == 200


# ──────────────────────────────────────────────
# CatalogsView
# ──────────────────────────────────────────────


@pytest.mark.django_db
class TestCatalogsView:
    def test_root(self, client, django_user, counter_with_books) -> None:
        client.force_login(django_user)
        response = client.get(reverse("web:catalog"))
        assert response.status_code == 200

    def test_with_cat(self, client, django_user, counter_with_books, catalog) -> None:
        client.force_login(django_user)
        response = client.get(reverse("web:catalog"), {"cat": str(catalog.id)})
        assert response.status_code == 200


# ──────────────────────────────────────────────
# BooksView, AuthorsView, SeriesView, GenresView
# ──────────────────────────────────────────────


@pytest.mark.django_db
class TestBooksView:
    def test_no_params(self, client, django_user, counter_with_books) -> None:
        client.force_login(django_user)
        response = client.get(reverse("web:book"))
        assert response.status_code == 200

    def test_with_lang(self, client, django_user, counter_with_books) -> None:
        client.force_login(django_user)
        response = client.get(reverse("web:book"), {"lang": "1"})
        assert response.status_code == 200


@pytest.mark.django_db
class TestAuthorsView:
    def test_no_params(self, client, django_user, counter_with_books) -> None:
        client.force_login(django_user)
        response = client.get(reverse("web:author"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestSeriesView:
    def test_no_params(self, client, django_user, counter_with_books) -> None:
        client.force_login(django_user)
        response = client.get(reverse("web:series"))
        assert response.status_code == 200


@pytest.mark.django_db
class TestGenresView:
    def test_root(self, client, django_user, counter_with_books) -> None:
        client.force_login(django_user)
        response = client.get(reverse("web:genre"))
        assert response.status_code == 200


# ──────────────────────────────────────────────
# BSDelView + BSClearView
# ──────────────────────────────────────────────


@pytest.mark.django_db
class TestBSDelView:
    def test_delete(
        self, client, django_user, counter_with_books, book_with_relations
    ) -> None:
        bookshelf.objects.create(user=django_user, book=book_with_relations)
        client.force_login(django_user)
        response = client.get(
            reverse("web:bsdel"), {"book": str(book_with_relations.id)}
        )
        assert response.status_code == 302


@pytest.mark.django_db
class TestBSClearView:
    def test_clear(
        self, client, django_user, counter_with_books, book_with_relations
    ) -> None:
        bookshelf.objects.create(user=django_user, book=book_with_relations)
        client.force_login(django_user)
        response = client.get(reverse("web:bsclear"))
        assert response.status_code == 302


# ──────────────────────────────────────────────
# sopds_processor
# ──────────────────────────────────────────────


@pytest.mark.django_db
class TestSopdsProcessor:
    def test_basic(self, rf, counter_with_books) -> None:
        from sopds_web_backend.processors import sopds_processor

        request = rf.get("/")
        request.user = AnonymousUser()
        result = sopds_processor(request)
        assert "app_title" in result
        assert "stats" in result

    def test_alphabet_menu(self, rf, counter_with_books, override_config) -> None:
        from sopds_web_backend.processors import sopds_processor

        with override_config(SOPDS_ALPHABET_MENU=True):
            request = rf.get("/")
            request.user = AnonymousUser()
            result = sopds_processor(request)
            assert "lang_menu" in result

    def test_bookshelf_no_n_plus_one_queries(
        self, rf, counter_with_books, django_user, catalog, override_config
    ) -> None:
        """Баг №103: раньше на каждую строку книжной полки шёл отдельный
        Book.objects.get(id=...) — N+1, множится на КАЖДЫЙ рендер любой
        страницы для авторизованного пользователя (context processor)."""
        from opds_catalog.models import Author
        from sopds_web_backend.processors import sopds_processor

        author = Author.objects.create(full_name="Тест Автор", search_full_name="ТЕСТ АВТОР")
        for i in range(8):
            book = Book.objects.create(
                filename=f"book{i}.fb2", path=".", format="fb2", cat_type=0,
                title=f"Книга {i}", search_title=f"КНИГА {i}", catalog=catalog,
            )
            bauthor.objects.create(book=book, author=author)
            bookshelf.objects.create(user=django_user, book=book, readtime=timezone.now())

        with override_config(SOPDS_AUTH=True):
            request = rf.get("/")
            request.user = django_user
            with CaptureQueriesContext(connection) as ctx:
                result = sopds_processor(request)

        assert len(result["bookshelf"]) == 8
        assert result["bookshelf"][0]["title"].startswith("Книга")
        # book.authors.values() — ленивый QuerySet, реально выполняется
        # только тут, при обращении (в шаблоне — при рендере) — это
        # отдельная история, не часть данного фикса.
        assert len(result["bookshelf"][0]["authors"]) == 1

        db_queries = [
            q for q in ctx.captured_queries
            if "constance_constance" not in q["sql"] and "SAVEPOINT" not in q["sql"]
            and "RELEASE" not in q["sql"]
        ]
        # 1 запрос на bookshelf+book (select_related) + 1 на статистику
        # каталога (Counter) — раньше было бы ЕЩЁ +8 отдельных
        # Book.objects.get(), один на каждую из 8 строк книжной полки.
        assert len(db_queries) <= 3, (
            f"Запросов: {len(db_queries)} ({[q['sql'][:60] for q in db_queries]}) "
            "— не масштабируется с числом строк книжной полки?"
        )
