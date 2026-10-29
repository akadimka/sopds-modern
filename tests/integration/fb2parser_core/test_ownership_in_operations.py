"""Владельцы файлов библиотеки в операциях fb2parser (docs/multiuser-plan.md
шаг 1.4): синхронизация, замена более полной версией, переименование
автора, компиляция (сборник, дубли, переименование оставленного сборника,
книги разных владельцев не сливаются), удаление по списку."""
import os
import time

import pytest
from django.contrib.auth.models import User

from fb2parser_core import regen_csv
from fb2parser_core.fb2_compiler import CompilationGroup, FB2CompilerService
from fb2parser_core.fb2_utils import compress_fb2_file
from fb2parser_core.synchronization import SynchronizationService
from fb2parser_web import fb2parser_bridge as bridge
from fb2parser_web.views import _delete_paths_confined
from opds_catalog import ownership
from opds_catalog.models import LibraryFileOwner

pytestmark = pytest.mark.django_db

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info><genre>Фантастика</genre>
<author><first-name>Иван</first-name><last-name>Иванов</last-name></author>
<book-title>Звезды {n}</book-title><sequence name="Звезды" number="{n}"/></title-info></description>
<body><section><p>Текст книги {n}.{extra}</p></section></body>
</FictionBook>
"""
OLD = time.time() - 3 * 3600


@pytest.fixture
def lib(tmp_path, override_config):
    root = tmp_path / "lib"
    root.mkdir()
    with override_config(SOPDS_ROOT_LIB=str(root)):
        yield root


@pytest.fixture
def users():
    return (User.objects.create_user("admin", is_superuser=True),
            User.objects.create_user("anna"), User.objects.create_user("boris"))


def _book(path, n, extra=""):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FB2.format(n=n, extra=extra), encoding="utf-8")
    os.utime(path, (OLD, OLD))
    return path


def _owners_of(lib, path):
    return set(LibraryFileOwner.objects.filter(rel_path=ownership.rel_path(path, str(lib)))
               .values_list("user__username", flat=True))


def _all_owned(lib):
    return {(r, u) for r, u in LibraryFileOwner.objects.values_list("rel_path", "user__username")}


def _sync(tmp_path, lib, scan):
    svc = SynchronizationService(bridge._config_path())
    svc.library_path = lib
    svc.db_path = tmp_path / "sync.db"
    svc.last_scan_path = scan
    return svc


def test_sync_assigns_moved_files(tmp_path, lib, users):
    scan = tmp_path / "in"
    for n in (1, 2):
        _book(scan / "Иванов Иван" / "Звезды" / f"{n}. Звезды {n}.fb2", n)
    _sync(tmp_path, lib, scan).synchronize(allowed_folders={str(scan / "Иванов Иван")})
    files = [p for p in lib.rglob("*.fb2")]
    assert len(files) == 2
    assert all(_owners_of(lib, p) == {"admin"} for p in files)


def test_sync_replacing_compressed_file_keeps_its_owners(tmp_path, lib, users):
    _admin, anna, _ = users
    scan = tmp_path / "in"
    _book(scan / "Иванов Иван" / "Звезды" / "1. Звезды 1.fb2", 1)
    _sync(tmp_path, lib, scan).synchronize(allowed_folders={str(scan / "Иванов Иван")})
    [placed] = lib.rglob("*.fb2")
    LibraryFileOwner.objects.all().update(user=anna)        # книга — у Анны
    compress_fb2_file(placed)                                 # библиотеку сжали
    ownership.transfer(ownership.rel_path(placed, str(lib)), ownership.rel_path(str(placed) + ".zip", str(lib)))

    scan2 = tmp_path / "in2"                                  # более полная версия той же книги
    _book(scan2 / "Иванов Иван" / "Звезды" / "1. Звезды 1.fb2", 1, extra=" Дополнено." * 50)
    _sync(tmp_path, lib, scan2).synchronize(allowed_folders={str(scan2 / "Иванов Иван")})

    assert not list(lib.rglob("*.fb2.zip"))
    [replaced] = lib.rglob("*.fb2")
    assert replaced == placed
    assert _owners_of(lib, replaced) == {"anna", "admin"}
    assert _all_owned(lib) == {(ownership.rel_path(replaced, str(lib)), "anna"),
                               (ownership.rel_path(replaced, str(lib)), "admin")}


def test_rename_library_author_transfers_owners(tmp_path, lib, users):
    _admin, anna, _ = users
    old = _book(lib / "Фантастика" / "Призывающий Оро" / "Призывающий Оро - Серия т. 1.fb2", 1)
    ownership.assign(ownership.rel_path(old, str(lib)), anna)
    svc = SynchronizationService.__new__(SynchronizationService)
    svc.db_path, svc.library_path, svc.stats, svc._log = tmp_path / "c.db", lib, {"errors": 0}, lambda m: None
    svc.rename_library_author("Призывающий Оро", "Оро Призывающий", apply=True)
    new = lib / "Фантастика" / "Оро Призывающий" / "Оро Призывающий - Серия т. 1.fb2"
    assert new.exists()
    assert _all_owned(lib) == {(ownership.rel_path(new, str(lib)), "anna")}


def test_series_moved_to_another_genre_keeps_owners(tmp_path, lib, users):
    from fb2parser_core.library_cleanup import find_split_series, move_series_to_genre

    _admin, anna, _ = users
    _book(lib / "Детектив" / "Иванов Иван" / "Звезды" / "Иванов Иван - Звезды т. 1.fb2", 1)
    _book(lib / "Детектив" / "Иванов Иван" / "Звезды" / "Иванов Иван - Звезды т. 2.fb2", 2)
    old = _book(lib / "Фантастика" / "Иванов Иван" / "Звезды" / "Иванов Иван - Звезды т. 3.fb2", 3)
    ownership.assign(ownership.rel_path(old, str(lib)), anna)
    [series] = find_split_series(lib)
    move_series_to_genre(lib, series, "Детектив", tmp_path / "c.db", apply=True)
    new = lib / "Детектив" / "Иванов Иван" / "Звезды" / "Иванов Иван - Звезды т. 3.fb2"
    assert new.exists() and not old.exists()
    assert _all_owned(lib) == {(ownership.rel_path(new, str(lib)), "anna")}


def _groups(lib):
    records = regen_csv.RegenCSVService(bridge._config_path()).generate_csv(str(lib), output_csv_path=None)
    return FB2CompilerService().find_groups(records, lib)


def _series(lib, owners_by_volume):
    folder = lib / "Фантастика" / "Иванов Иван" / "Звезды"
    paths = []
    for n, user in owners_by_volume.items():
        p = _book(folder / f"Иванов Иван - Звезды т. {n}.fb2", n)
        ownership.assign(ownership.rel_path(p, str(lib)), user)
        paths.append(p)
    return paths


@pytest.mark.parametrize("delete_sources", [True, False])
def test_compiled_file_gets_owners_of_its_sources(lib, users, delete_sources):
    _admin, anna, _ = users
    sources = _series(lib, {1: anna, 2: anna, 3: anna})
    [group] = _groups(lib)
    result = FB2CompilerService().compile_group(group, output_dir=None, delete_sources=delete_sources)
    assert result.success and result.output_path.exists()
    assert _owners_of(lib, result.output_path) == {"anna"}
    for src in sources:
        assert _owners_of(lib, src) == (set() if delete_sources else {"anna"})


def test_books_of_different_owners_are_not_compiled_together(lib, users):
    _admin, anna, boris = users
    _series(lib, {1: anna, 2: anna, 3: boris, 4: boris})
    groups = _groups(lib)
    assert len(groups) == 2
    for g in groups:
        assert len({ownership.owner_set_key(ownership.rel_path(b.abs_path, str(lib))) for b in g.books}) == 1


def test_cleanup_only_moves_duplicate_owners_to_kept_and_follows_rename(lib, users):
    _admin, anna, boris = users
    folder = lib / "Фантастика" / "Иванов Иван"
    kept = _book(folder / "Иванов Иван - Звезды.fb2", 1)
    dup = _book(folder / "Иванов Иван - Звезды т. 1-2.fb2", 1)
    ownership.assign(ownership.rel_path(kept, str(lib)), anna)
    ownership.assign(ownership.rel_path(dup, str(lib)), boris)
    group = CompilationGroup(author="Иванов Иван", series="Звезды", books=[], order_determined=True,
                             volume_range="1-2", duplicate_paths=[dup], kept_paths=[kept], cleanup_only=True)
    result = FB2CompilerService().compile_group(group, output_dir=None)
    assert not dup.exists() and result.output_path is not None and result.output_path != kept
    assert _all_owned(lib) == {(ownership.rel_path(result.output_path, str(lib)), u) for u in ("anna", "boris")}


def test_confined_delete_releases_owners(lib, users):
    _admin, anna, _ = users
    p = _book(lib / "Фантастика" / "Иванов Иван" / "Звезды.fb2", 1)
    ownership.assign(ownership.rel_path(p, str(lib)), anna)
    deleted, errors = _delete_paths_confined([str(p)], str(lib))
    assert (deleted, errors) == (1, [])
    assert not LibraryFileOwner.objects.exists()


def test_operations_outside_library_do_not_touch_ownership(tmp_path, lib, users):
    outside = _book(tmp_path / "elsewhere" / "Звезды.fb2", 1)
    deleted, _errors = _delete_paths_confined([str(outside)], str(tmp_path / "elsewhere"))
    assert deleted == 1 and not LibraryFileOwner.objects.exists()
