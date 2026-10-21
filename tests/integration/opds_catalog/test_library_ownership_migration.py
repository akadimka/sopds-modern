"""Миграции многопользовательской библиотеки (docs/multiuser-plan.md шаг 1.1):
вся существующая библиотека — суперпользователю, его библиотека открыта
всем, профили — всем пользователям; новые пользователи получают профиль
сразу."""
import pytest
from django.contrib.auth.models import User
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor

from opds_catalog.models import BookOwner, LibraryFileOwner, LibraryShare
from sopds_web_backend.models import UserProfile

BEFORE = [("opds_catalog", "0013_library_ownership"), ("sopds_web_backend", "0006_userprofile_library_fields")]
AFTER = [("opds_catalog", "0014_seed_library_owners"), ("sopds_web_backend", "0007_profiles_admin_library_public")]


def _migrate(targets):
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _migrate_to_latest():
    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


@pytest.fixture
def old_apps():
    apps = _migrate(BEFORE)
    yield apps
    _migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_existing_library_goes_to_admin_and_is_public(old_apps):
    U = old_apps.get_model("auth", "User")
    reader = U.objects.create(username="reader")
    U.objects.create(username="inactive_admin", is_superuser=True, is_active=False)
    admin = U.objects.create(username="admin", is_superuser=True, is_staff=True)
    Catalog = old_apps.get_model("opds_catalog", "Catalog")
    Book = old_apps.get_model("opds_catalog", "Book")
    cat = Catalog.objects.create(cat_name="Фантастика", path="Фантастика")

    def book(path, filename, cat_type=0, avail=2):
        return Book.objects.create(filename=filename, path=path, catalog=cat, cat_type=cat_type, avail=avail,
                                   format="fb2", docdate="", lang="ru", title=filename, search_title=filename.upper(),
                                   annotation="")

    plain = book("Фантастика\\Иванов Иван", "Звёзды.fb2")
    root_level = book(".", "Сборник.fb2")
    in_zip_1 = book("Фантастика/Петров/Архив.zip", "Том 1.fb2", cat_type=1)
    in_zip_2 = book("Фантастика/Петров/Архив.zip", "Том 2.fb2", cat_type=1)
    gone = book("Фантастика\\Старое", "Удалено.fb2", avail=0)

    _migrate(AFTER)
    _migrate_to_latest()   # проверки — текущими моделями (их поля — из следующих миграций)

    admin = User.objects.get(pk=admin.pk)
    assert set(LibraryFileOwner.objects.values_list("rel_path", flat=True)) == {
        "Фантастика/Иванов Иван/Звёзды.fb2", "Сборник.fb2", "Фантастика/Петров/Архив.zip"}
    assert set(LibraryFileOwner.objects.values_list("user", "source").distinct()) == {(admin.pk, "migration")}
    assert set(BookOwner.objects.values_list("book_id", flat=True)) == {
        plain.pk, root_level.pk, in_zip_1.pk, in_zip_2.pk}
    assert not BookOwner.objects.filter(book_id=gone.pk).exists()
    assert set(BookOwner.objects.values_list("user", flat=True).distinct()) == {admin.pk}

    assert UserProfile.objects.count() == User.objects.count()
    assert UserProfile.objects.get(user=admin).library_public is True
    assert UserProfile.objects.get(user_id=reader.pk).library_public is False
    assert UserProfile.objects.get(user__username="inactive_admin").library_public is False


@pytest.mark.django_db(transaction=True)
def test_without_active_admin_nothing_is_seeded(old_apps):
    old_apps.get_model("auth", "User").objects.create(username="reader")
    _migrate(AFTER)
    _migrate_to_latest()   # проверки — текущими моделями (их поля — из следующих миграций)
    assert not LibraryFileOwner.objects.exists() and not BookOwner.objects.exists()
    assert UserProfile.objects.filter(library_public=True).count() == 0


@pytest.mark.django_db
def test_new_user_gets_profile():
    user = User.objects.create_user("newbie", password="x")
    profile = UserProfile.objects.get(user=user)
    assert (profile.library_public, profile.quota_library_mb, profile.quota_books) == (False, None, None)


@pytest.mark.django_db
def test_constraints():
    a, b = User.objects.create_user("a"), User.objects.create_user("b")
    LibraryFileOwner.objects.create(rel_path="Жанр/a.fb2", user=a)
    LibraryFileOwner.objects.create(rel_path="Жанр/a.fb2", user=b)  # два владельца одного файла
    with pytest.raises(IntegrityError), transaction.atomic():
        LibraryFileOwner.objects.create(rel_path="Жанр/a.fb2", user=a)
    LibraryShare.objects.create(owner=a, grantee=b)
    with pytest.raises(IntegrityError), transaction.atomic():
        LibraryShare.objects.create(owner=a, grantee=b)
    with pytest.raises(IntegrityError), transaction.atomic():
        LibraryShare.objects.create(owner=a, grantee=a)
