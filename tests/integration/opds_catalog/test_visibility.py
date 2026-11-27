"""Правило видимости книг (opds_catalog/visibility.py, docs/multiuser-plan.md
шаг 1.5): своя / открытая мне / открытая всем / чужая; суперпользователь видит
всё, аноним — только открытые всем; авторы, серии, жанры, папки и счётчики —
только по видимым книгам; книга по id вне видимости — 404."""
import pytest
from django.contrib.auth.models import User
from django.http import Http404

from opds_catalog import ownership, visibility
from opds_catalog.models import (
    Author,
    Book,
    BookOwner,
    Catalog,
    Genre,
    LibraryShare,
    Series,
    bauthor,
    bgenre,
    bseries,
)
from sopds_web_backend.models import UserProfile

pytestmark = pytest.mark.django_db


class Anon:
    is_authenticated = False
    is_active = False
    is_superuser = False
    pk = None


@pytest.fixture
def lib():
    u = {n: User.objects.create_user(n) for n in ("anna", "boris", "carl", "pub", "gone")}
    u["admin"] = User.objects.create_user("admin", is_superuser=True)
    UserProfile.objects.filter(user__in=[u["pub"], u["gone"]]).update(library_public=True)
    User.objects.filter(pk=u["gone"].pk).update(is_active=False)   # заблокирован — его библиотеки не видно
    LibraryShare.objects.create(owner=u["boris"], grantee=u["anna"])
    genre_cat = Catalog.objects.create(cat_name="Фантастика", path="Фантастика")
    shared_author = Author.objects.create(full_name="Общий Автор", search_full_name="ОБЩИЙ АВТОР")
    genre = Genre.objects.create(genre="sf", section="Фантастика", subsection="sf")
    books = {}

    def book(name, owners, avail=2, author=None):
        cat = Catalog.objects.create(cat_name=name, path=f"Фантастика\\{name}", parent=genre_cat)
        b = Book.objects.create(filename=f"{name}.fb2", path=cat.path, catalog=cat, avail=avail, format="fb2",
                                docdate="", lang="ru", title=name, search_title=name.upper(), annotation="")
        a = author or Author.objects.create(full_name=f"Автор {name}", search_full_name=f"АВТОР {name}".upper())
        bauthor.objects.create(book=b, author=a)
        bgenre.objects.create(book=b, genre=genre)
        bseries.objects.create(book=b, ser=Series.objects.create(ser=f"Серия {name}", search_ser=name.upper()))
        BookOwner.objects.bulk_create([BookOwner(book=b, user=u[o]) for o in owners])
        books[name] = b

    book("A", ["anna"], author=shared_author)
    book("B", ["boris"])
    book("C", ["carl"], author=shared_author)
    book("P", ["pub"])
    book("G", ["gone"])
    book("AC", ["anna", "carl"])
    book("A0", ["anna"], avail=0)
    return u, books


def _titles(qs):
    return sorted(qs.values_list("title", flat=True))


@pytest.mark.parametrize("who, expected", [
    ("anna", ["A", "AC", "B", "P"]),
    ("boris", ["B", "P"]),
    ("carl", ["AC", "C", "P"]),
    ("admin", ["A", "AC", "B", "C", "G", "P"]),
])
def test_visible_books(lib, who, expected):
    users, _ = lib
    assert _titles(visibility.visible_books(users[who])) == expected


def test_anonymous_sees_only_public(lib):
    assert _titles(visibility.visible_books(Anon())) == ["P"]
    assert _titles(visibility.visible_books(None)) == ["P"]


def test_one_library(lib):
    users, _ = lib
    assert _titles(visibility.visible_books(users["anna"], library=users["boris"])) == ["B"]
    assert _titles(visibility.visible_books(users["anna"], library=users["carl"])) == []   # не открыта ей
    assert _titles(visibility.visible_books(users["admin"], library=users["carl"])) == ["AC", "C"]


def test_book_by_id(lib):
    users, books = lib
    assert visibility.get_visible_book_or_404(users["anna"], books["B"].pk) == books["B"]
    for bad in (books["C"].pk, books["A0"].pk, 10 ** 9, "abc", None):
        with pytest.raises(Http404):
            visibility.get_visible_book_or_404(users["anna"], bad)


def test_authors_series_genres_only_of_visible_books(lib):
    users, _ = lib
    anna, boris = users["anna"], users["boris"]
    names = sorted(visibility.visible_authors(boris).values_list("full_name", flat=True))
    assert names == ["Автор B", "Автор P"]                     # «Общий Автор» — только у чужих книг
    assert "Общий Автор" in visibility.visible_authors(anna).values_list("full_name", flat=True)
    assert sorted(visibility.visible_series(boris).values_list("ser", flat=True)) == ["Серия B", "Серия P"]
    assert visibility.visible_genres(boris).count() == 1
    assert visibility.visible_genres(Anon()).count() == 1
    no_books = User.objects.create_user("empty")
    UserProfile.objects.filter(library_public=True).update(library_public=False)
    assert not visibility.visible_genres(no_books).exists()


def test_counts_only_visible_books(lib):
    users, _ = lib
    shared = Author.objects.get(full_name="Общий Автор")
    count = lambda u: Author.objects.filter(pk=shared.pk).annotate(  # noqa: E731
        n=visibility.visible_book_count(u)).get().n
    assert (count(users["anna"]), count(users["carl"]), count(users["boris"]), count(users["admin"])) == (1, 1, 0, 2)


def test_catalogs_with_visible_books_and_their_parents(lib):
    users, _ = lib
    ids = visibility.visible_catalog_ids(users["boris"])
    assert sorted(Catalog.objects.filter(pk__in=ids).values_list("cat_name", flat=True)) == ["B", "P", "Фантастика"]
    assert visibility.visible_catalog_ids(users["admin"]) is None


def test_user_counters_and_invalidation(lib, tmp_path, override_config):
    users, books = lib
    assert visibility.user_counters(users["boris"]) == {
        "books": 2, "catalogs": 3, "authors": 2, "genres": 1, "series": 2}
    # Владение изменилось — счётчики пересчитаны без ожидания срока кэша.
    with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
        ownership.assign(ownership.book_rel_path(books["C"]), users["boris"])
    assert visibility.user_counters(users["boris"])["books"] == 3
    assert BookOwner.objects.filter(book=books["C"], user=users["boris"]).exists()


def test_admin_counters_count_existing_books(lib):
    """Суперпользователь: все существующие книги (A0 — логически удалена, не в счёт),
    папки и сущности — только с книгами, как и в списках."""
    users, _ = lib
    Author.objects.create(full_name="Без книг", search_full_name="БЕЗ КНИГ")
    assert visibility.user_counters(users["admin"]) == {
        "books": 6, "catalogs": 7, "authors": 5, "genres": 1, "series": 6}


def test_genre_counter_counts_sections(lib):
    """Счётчик жанров — разделы, как на странице «Жанры»: две записи жанра
    с одним разделом — один жанр (на главной было 19, на странице 9)."""
    users, books = lib
    other = Genre.objects.create(genre="космос", section="Фантастика", subsection="космос")
    bgenre.objects.create(book=books["B"], genre=other)
    detective = Genre.objects.create(genre="Детектив", section="Детектив", subsection="")
    bgenre.objects.create(book=books["C"], genre=detective)
    assert visibility.user_counters(users["boris"])["genres"] == 1
    assert visibility.user_counters(users["admin"])["genres"] == 2


def test_catalog_book_counts_include_subfolders(lib):
    users, books = lib
    root = Catalog.objects.get(cat_name="Фантастика")
    inner = Catalog.objects.create(cat_name="Цикл", path="Фантастика\\B\\Цикл", parent=books["B"].catalog)
    Book.objects.create(filename="B2.fb2", path=inner.path, catalog=inner, avail=2, format="fb2", docdate="",
                        lang="ru", title="B2", search_title="B2", annotation="")
    BookOwner.objects.create(book=Book.objects.get(title="B2"), user=users["boris"])
    counts = visibility.catalog_book_counts(users["boris"])
    assert counts[root.pk] == 3                                 # B, B2, P — чужие не в счёт
    assert counts[books["B"].catalog_id] == 2
    assert books["C"].catalog_id not in counts
    assert visibility.catalog_book_counts(users["admin"])[root.pk] == 7   # A0 удалена — не в счёт


def test_only_superuser_role_sees_everything(lib):
    """Всё видит роль «Суперпользователь» (is_superuser); is_staff без
    суперправ (бывший «Admin», такой роли больше нет) — как обычный
    пользователь (docs/multiuser-design.md)."""
    users, _ = lib
    staff = User.objects.create_user("staff", is_staff=True)
    assert visibility.is_library_admin(users["admin"]) and not visibility.is_library_admin(staff)
    assert _titles(visibility.visible_books(staff)) == ["P"]


def test_access_changes_reset_cached_folders_and_counts(lib):
    """Папки и счётчики кэшируются на 5 минут; блокировка владельца,
    закрытие библиотеки и отзыв доступа меняют их сразу."""
    users, books = lib
    anna = users["anna"]
    folder_b = books["B"].catalog_id
    assert folder_b in visibility.visible_catalog_ids(anna)            # библиотека Бориса открыта Анне
    LibraryShare.objects.filter(owner=users["boris"], grantee=anna).delete()
    assert folder_b not in visibility.visible_catalog_ids(anna)
    assert visibility.user_counters(anna)["books"] == 3                # A, AC, P

    folder_p = books["P"].catalog_id
    profile = UserProfile.objects.get(user=users["pub"])
    profile.library_public = False
    profile.save()
    assert folder_p not in visibility.visible_catalog_ids(anna)
    assert visibility.catalog_book_counts(anna).get(folder_p) is None

    profile.library_public = True
    profile.save()
    assert folder_p in visibility.visible_catalog_ids(anna)
    pub = users["pub"]
    pub.is_active = False
    pub.save()
    assert folder_p not in visibility.visible_catalog_ids(anna)
    assert visibility.user_counters(anna)["books"] == 2
