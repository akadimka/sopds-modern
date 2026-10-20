"""Модуль учёта владения файлами библиотеки (opds_catalog/ownership.py,
docs/multiuser-plan.md шаг 1.2)."""
import os

import pytest
from django.contrib.auth.models import User

from opds_catalog import ownership
from opds_catalog.models import (
    Book,
    BookOwner,
    Catalog,
    LibraryFileOwner,
    LibraryOwnershipLog,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def lib(tmp_path, override_config):
    with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
        yield tmp_path


@pytest.fixture
def users():
    admin = User.objects.create_user("admin", is_superuser=True, is_staff=True)
    return admin, User.objects.create_user("anna"), User.objects.create_user("boris")


def _file(lib, rel, data=b"x"):
    p = lib.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _book(rel, cat_type=0):
    folder, name = os.path.split(rel.replace("/", os.sep))
    cat = Catalog.objects.get_or_create(cat_name="c", path="c")[0]
    path = rel.replace("/", os.sep) if cat_type else (folder or ".")
    return Book.objects.create(filename=name, path=path, catalog=cat, cat_type=cat_type, avail=2, format="fb2",
                               docdate="", lang="ru", title=name, search_title=name.upper(), annotation="")


def _owners(rel):
    return set(LibraryFileOwner.objects.filter(rel_path=rel).values_list("user__username", flat=True))


def _book_owners(book):
    return set(BookOwner.objects.filter(book=book).values_list("user__username", flat=True))


class TestRelPath:
    def test_canonical_form(self, lib):
        assert ownership.rel_path(lib / "Фантастика" / "Иванов" / "a.fb2") == "Фантастика/Иванов/a.fb2"
        assert ownership.rel_path(str(lib) + os.sep + "Жанр" + os.sep + "." + os.sep + "b.fb2.zip") == "Жанр/b.fb2.zip"

    def test_unicode_nfc(self, lib):
        decomposed = "Звёзды.fb2"  # «ё» из двух кодовых точек (macOS, часть архивов)
        assert ownership.rel_path(lib / decomposed) == "Звёзды.fb2"

    @pytest.mark.parametrize("outside", ["..", "../other/a.fb2", ""])
    def test_outside_library_rejected(self, lib, outside):
        with pytest.raises(ValueError):
            ownership.rel_path(lib / outside if outside else lib)

    def test_book_rel_path(self, lib):
        assert ownership.book_rel_path(_book("Жанр/Автор/a.fb2")) == "Жанр/Автор/a.fb2"
        assert ownership.book_rel_path(_book("root.fb2")) == "root.fb2"
        assert ownership.book_rel_path(_book("Жанр/Архив.zip", cat_type=1)) == "Жанр/Архив.zip"


class TestOperations:
    def test_assign_links_existing_catalog_records(self, lib, users):
        admin, anna, _ = users
        book = _book("Жанр/a.fb2")
        assert ownership.assign("Жанр/a.fb2", anna, source="upload", origin_sha256="ab" * 32, size=10)
        assert not ownership.assign("Жанр\\a.fb2", anna)  # тот же путь в другой записи — тот же владелец
        assert _owners("Жанр/a.fb2") == {"anna"} and _book_owners(book) == {"anna"}
        rec = LibraryFileOwner.objects.get(user=anna)
        assert (rec.source, rec.size) == ("upload", 10)

    def test_two_owners_of_one_file(self, lib, users):
        _, anna, boris = users
        ownership.assign("Жанр/a.fb2", anna)
        ownership.assign("Жанр/a.fb2", boris)
        assert ownership.owner_set_key("Жанр/a.fb2") == tuple(sorted([anna.pk, boris.pk]))

    def test_transfer_moves_owners_and_merges(self, lib, users):
        _, anna, boris = users
        ownership.assign("Жанр/a.fb2", anna)
        ownership.assign("Жанр/a.fb2.zip", boris)
        zipped = _book("Жанр/a.fb2.zip")
        ownership.transfer("Жанр/a.fb2", "Жанр/a.fb2.zip")
        assert _owners("Жанр/a.fb2") == set()
        assert _owners("Жанр/a.fb2.zip") == {"anna", "boris"}
        assert _book_owners(zipped) == {"anna"}  # boris связан не был — связь только у перенесённых

    def test_transfer_same_owner_on_both_paths(self, lib, users):
        _, anna, _ = users
        ownership.assign("Жанр/a.fb2", anna)
        ownership.assign("Жанр/b.fb2", anna)
        ownership.transfer("Жанр/a.fb2", "Жанр/b.fb2")
        assert LibraryFileOwner.objects.filter(user=anna).count() == 1

    def test_merge_into_compilation(self, lib, users):
        _, anna, boris = users
        ownership.assign("Серия/т1.fb2", anna)
        ownership.assign("Серия/т2.fb2", boris)
        compiled = _book("Серия/Сборник.fb2")
        ownership.merge(["Серия/т1.fb2", "Серия/т2.fb2"], "Серия/Сборник.fb2")
        assert _owners("Серия/т1.fb2") == _owners("Серия/т2.fb2") == set()
        assert _owners("Серия/Сборник.fb2") == {"anna", "boris"} == _book_owners(compiled)

    def test_release_one_owner_keeps_others(self, lib, users):
        _, anna, boris = users
        _file(lib, "Жанр/a.fb2")
        book = _book("Жанр/a.fb2")
        ownership.assign("Жанр/a.fb2", anna)
        ownership.assign("Жанр/a.fb2", boris)
        ownership.release("Жанр/a.fb2", anna)
        assert _owners("Жанр/a.fb2") == {"boris"} == _book_owners(book)

    def test_last_owner_leaves_existing_file_goes_to_admin(self, lib, users):
        _, anna, _ = users
        _file(lib, "Жанр/a.fb2")
        book = _book("Жанр/a.fb2")
        ownership.assign("Жанр/a.fb2", anna)
        ownership.release("Жанр/a.fb2", anna)
        assert _owners("Жанр/a.fb2") == {"admin"} == _book_owners(book)
        assert LibraryOwnershipLog.objects.filter(action="to_admin", rel_path="Жанр/a.fb2").count() == 1

    def test_deleted_file_has_no_owners(self, lib, users):
        _, anna, boris = users
        ownership.assign("Жанр/a.fb2", anna)
        ownership.assign("Жанр/a.fb2", boris)
        ownership.release("Жанр/a.fb2")  # файла на диске нет
        assert _owners("Жанр/a.fb2") == set()

    def test_log(self, lib, users):
        _, anna, _ = users
        ownership.assign("a.fb2", anna, actor=anna)
        ownership.transfer("a.fb2", "b.fb2")
        ownership.release("b.fb2", file_exists=False)
        assert list(LibraryOwnershipLog.objects.order_by("id").values_list("action", "rel_path", "target_rel_path")) == [
            ("assign", "a.fb2", ""), ("transfer", "a.fb2", "b.fb2"), ("release", "b.fb2", "")]
