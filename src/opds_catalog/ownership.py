"""Учёт владения файлами библиотеки (docs/multiuser-design.md,
docs/multiuser-plan.md шаг 1.2).

Единственный способ менять `LibraryFileOwner`: любая операция, которая
кладёт файл в библиотеку, двигает, сливает или удаляет его, вызывает
функцию отсюда. Ключ — канонический путь `rel_path()`; строить его иначе
нельзя (в проекте пути пишутся тремя способами: «\\», «/», родные
разделители ОС).

Записи каталога (`BookOwner`) по тому же пути обновляются сразу, а не при
следующем скане: отозванное владение должно скрывать книгу немедленно.

Правило безопасного отказа: у существующего файла без владельцев владелец —
администратор (`admin_user()`), а не «никто» и не «все».
"""
from __future__ import annotations

import os
import posixpath
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q

from opds_catalog.models import Book, BookOwner, LibraryFileOwner, LibraryOwnershipLog
from opds_catalog.sopds_config import sopds_cfg as config

CAT_NORMAL = 0


def _canonical(rel: str) -> str:
    rel = posixpath.normpath(rel.replace("\\", "/")).lstrip("/")
    return unicodedata.normalize("NFC", "" if rel == "." else rel)


def rel_path(abs_path, root: Optional[str] = None) -> str:
    """Канонический путь файла относительно корня библиотеки (`root_lib`):
    разделитель «/», Unicode NFC. ValueError — путь вне библиотеки."""
    root = root if root is not None else config.SOPDS_ROOT_LIB
    if not root:
        raise ValueError("library root (root_lib) is not configured")
    try:
        rel = os.path.relpath(os.path.abspath(os.fspath(abs_path)), os.path.abspath(root))
    except ValueError as e:  # другой диск Windows
        raise ValueError(f"{abs_path!s} is outside the library {root!s}") from e
    rel = _canonical(rel)
    if not rel or rel == ".." or rel.startswith("../"):
        raise ValueError(f"{abs_path!s} is outside the library {root!s}")
    return rel


def book_rel_path(book: Book) -> str:
    """Путь файла записи каталога: обычная книга — папка + имя, книга в
    zip/inpx — путь архива (так их пишет сканер)."""
    if book.cat_type != CAT_NORMAL:
        return _canonical(book.path)
    return _canonical(posixpath.join(book.path.replace("\\", "/"), book.filename))


def _books_at(rel: str):
    """Записи каталога, чей файл — `rel` (Book.path хранится с родными
    разделителями ОС, корень — «.»)."""
    folder, name = posixpath.split(rel)
    variants = {folder or ".", (folder or ".").replace("/", "\\"), (folder or ".").replace("/", os.sep)}
    rel_variants = {rel, rel.replace("/", "\\"), rel.replace("/", os.sep)}
    return Book.objects.filter(
        Q(cat_type=CAT_NORMAL, path__in=variants, filename=name) | (~Q(cat_type=CAT_NORMAL) & Q(path__in=rel_variants)))


def admin_user() -> Optional[User]:
    """Владелец «по умолчанию» — первый активный суперпользователь."""
    return User.objects.filter(is_superuser=True, is_active=True).order_by("id").first()


def owners(rel: str) -> set[int]:
    return set(LibraryFileOwner.objects.filter(rel_path=_canonical(rel)).values_list("user_id", flat=True))


def owner_set_key(rel: str) -> tuple[int, ...]:
    """Набор владельцев как ключ группировки (компилятор не сливает книги
    с разными наборами владельцев)."""
    return tuple(sorted(owners(rel)))


def _log(action, rel, user=None, target="", actor=None):
    LibraryOwnershipLog.objects.create(action=action, rel_path=rel, target_rel_path=target,
                                       user_id=getattr(user, "pk", user), actor=actor)


def _link_books(rel: str, user_ids: Iterable[int]) -> None:
    BookOwner.objects.bulk_create(
        [BookOwner(book_id=b, user_id=u) for b in _books_at(rel).values_list("id", flat=True) for u in user_ids],
        ignore_conflicts=True)


def _add_owner(rel, user_id, *, source, actor=None, action=LibraryOwnershipLog.ASSIGN, **fields) -> bool:
    _obj, created = LibraryFileOwner.objects.get_or_create(
        rel_path=rel, user_id=user_id, defaults={"source": source, **fields})
    if created:
        _log(action, rel, user_id, actor=actor)
    return created


def assign(rel: str, user, *, source=LibraryFileOwner.SOURCE_ADMIN, actor=None,
           origin_name="", origin_sha256="", size=0) -> bool:
    """Сделать `user` владельцем файла (файл положили в библиотеку или
    пользователь загрузил уже имеющуюся книгу). True — владелец новый."""
    rel = _canonical(rel)
    with transaction.atomic():
        created = _add_owner(rel, user.pk, source=source, actor=actor, origin_name=origin_name,
                             origin_sha256=origin_sha256, size=size)
        _link_books(rel, [user.pk])
    return created


def transfer(old_rel: str, new_rel: str, *, actor=None) -> None:
    """Файл переехал (перемещение, переименование, сжатие): владельцы
    старого пути — владельцы нового; если у нового пути уже есть
    владельцы — объединение."""
    old_rel, new_rel = _canonical(old_rel), _canonical(new_rel)
    if old_rel == new_rel:
        return
    with transaction.atomic():
        moved = list(LibraryFileOwner.objects.filter(rel_path=old_rel))
        for o in moved:
            if LibraryFileOwner.objects.filter(rel_path=new_rel, user_id=o.user_id).exists():
                o.delete()
            else:
                o.rel_path = new_rel
                o.save(update_fields=["rel_path"])
        if moved:
            _log(LibraryOwnershipLog.TRANSFER, old_rel, target=new_rel, actor=actor)
        _link_books(new_rel, [o.user_id for o in moved])


def merge(sources: Iterable[str], target: str, *, actor=None) -> None:
    """Несколько файлов слились в один (сборник): владельцы сборника —
    объединение владельцев исходников; записи исходников удаляются
    (сами файлы удаляет вызывающий)."""
    target = _canonical(target)
    sources = [s for s in (_canonical(s) for s in sources) if s != target]
    with transaction.atomic():
        user_ids = set(LibraryFileOwner.objects.filter(rel_path__in=sources).values_list("user_id", flat=True))
        for uid in sorted(user_ids):
            _add_owner(target, uid, source=LibraryFileOwner.SOURCE_ADMIN, actor=actor)
        LibraryFileOwner.objects.filter(rel_path__in=sources).delete()
        for s in sources:
            _log(LibraryOwnershipLog.MERGE, s, target=target, actor=actor)
        _link_books(target, owners(target))


def release(rel: str, user=None, *, file_exists: Optional[bool] = None, actor=None) -> None:
    """Владение прекращается: `user` — отказ одного владельца (удалил книгу
    у себя), None — файла больше нет (удалён). У существующего файла без
    владельцев владелец — администратор."""
    rel = _canonical(rel)
    with transaction.atomic():
        qs = LibraryFileOwner.objects.filter(rel_path=rel)
        if user is not None:
            qs = qs.filter(user_id=user.pk)
        released = list(qs.values_list("user_id", flat=True))
        qs.delete()
        BookOwner.objects.filter(book__in=_books_at(rel), user_id__in=released).delete()
        for uid in released:
            _log(LibraryOwnershipLog.RELEASE, rel, uid, actor=actor)
        if file_exists is None:
            root = config.SOPDS_ROOT_LIB
            file_exists = bool(root) and os.path.exists(os.path.join(root, *rel.split("/")))
        if file_exists and not LibraryFileOwner.objects.filter(rel_path=rel).exists():
            admin = admin_user()
            if admin is not None:
                _add_owner(rel, admin.pk, source=LibraryFileOwner.SOURCE_ADMIN, actor=actor,
                           action=LibraryOwnershipLog.TO_ADMIN)
                _link_books(rel, [admin.pk])


def link_book(book: Book) -> None:
    """Новая запись каталога (сканер, opdsdb.addbook): владельцы — владельцы
    её файла; у файла без владельцев — администратор (файл в библиотеку
    положили в обход учёта: вручную, до перехода на владение)."""
    rel = book_rel_path(book)
    with transaction.atomic():
        user_ids = owners(rel)
        if not user_ids:
            admin = admin_user()
            if admin is None:
                return
            _add_owner(rel, admin.pk, source=LibraryFileOwner.SOURCE_AUTO, action=LibraryOwnershipLog.TO_ADMIN)
            user_ids = {admin.pk}
        BookOwner.objects.bulk_create([BookOwner(book=book, user_id=u) for u in user_ids], ignore_conflicts=True)


def _library_files(root: str) -> set[str]:
    """Файлы книг в библиотеке (что видит сканер: расширения книг и zip)."""
    exts = tuple(e.lower() for e in config.SOPDS_BOOK_EXTENSIONS.split()) + (".zip",)
    found = set()
    for dirpath, _dirs, names in os.walk(root, followlinks=True):
        for name in names:
            if name.lower().endswith(exts):
                found.add(rel_path(os.path.join(dirpath, name), root))
    return found


def _memory_fingerprints(memory_db) -> dict[str, str]:
    """Путь → отпечаток текста книги из индекса library_memory (переживает
    перемещение, сжатие и правку тегов); пусто — индекса нет."""
    import sqlite3
    if not memory_db or not os.path.isfile(memory_db):
        return {}
    conn = sqlite3.connect(f"file:{memory_db}?mode=ro", uri=True)
    try:
        return {_canonical(p): fp for p, fp in conn.execute(
            "SELECT path, fingerprint FROM books WHERE fingerprint IS NOT NULL")}
    finally:
        conn.close()


def reconcile(*, apply: bool = False, force: bool = False, memory_db=None) -> dict:
    """Сверка владения с диском и каталогом (команда library_owners_check):

    - файл без владельца → администратор;
    - владелец пути, которого нет на диске: файл найден под другим путём по
      отпечатку library_memory → перенос; иначе запись снимается;
    - `BookOwner` записей каталога (avail > 0) = владельцы их файлов.

    Без `apply` — только отчёт. Пропала большая часть файлов разом
    (недоступный диск) — ничего не снимается без `force`."""
    from opds_catalog.opdsdb import DELETION_GUARD_MIN_BOOKS, DELETION_GUARD_SHARE

    root = config.SOPDS_ROOT_LIB
    if not root or not os.path.isdir(root):
        raise ValueError(f"library folder {root!r} is not available")
    on_disk = _library_files(root)
    owned = set(LibraryFileOwner.objects.values_list("rel_path", flat=True).distinct())
    orphans, missing = sorted(on_disk - owned), sorted(owned - on_disk)
    # Файлы, которые сканер сам отдал администратору как «без владельца», —
    # тоже кандидаты на возврат настоящему владельцу (сканер мог увидеть
    # переехавший файл раньше сверки).
    auto_only = sorted(
        set(LibraryFileOwner.objects.filter(rel_path__in=on_disk, source=LibraryFileOwner.SOURCE_AUTO)
            .values_list("rel_path", flat=True))
        - set(LibraryFileOwner.objects.filter(rel_path__in=on_disk).exclude(source=LibraryFileOwner.SOURCE_AUTO)
              .values_list("rel_path", flat=True)))

    fps = _memory_fingerprints(memory_db)
    orphan_fp: dict[str, Optional[str]] = {}

    def fp_of(rel):
        if rel not in orphan_fp:
            fp = fps.get(rel)
            if fp is None:
                from fb2parser_core.library_memory import read_evidence
                fp = read_evidence(Path(root, *rel.split("/"))).fingerprint
            orphan_fp[rel] = fp
        return orphan_fp[rel]

    relinks: dict[str, str] = {}
    for gone in missing:
        fp = fps.get(gone)
        if fp:
            candidates = [o for o in orphans + auto_only if o not in relinks.values() and fp_of(o) == fp]
            if len(candidates) == 1:
                relinks[gone] = candidates[0]
    to_admin = [o for o in orphans if o not in relinks.values()]
    dropped = [m for m in missing if m not in relinks]
    guard = ""
    if len(dropped) > DELETION_GUARD_MIN_BOOKS and len(dropped) > len(owned) * DELETION_GUARD_SHARE:
        guard = (f"{len(dropped)} of {len(owned)} owned files are missing on disk "
                 f"(more than {DELETION_GUARD_SHARE:.0%}) — check that the library is mounted")

    report = {"files_on_disk": len(on_disk), "owned_paths": len(owned), "relinked": relinks,
              "to_admin": to_admin, "dropped": dropped, "guard": guard,
              "book_links_added": 0, "book_links_removed": 0, "applied": False}
    if not apply or (guard and not force):
        return report

    admin = admin_user()
    with transaction.atomic():
        for gone, found in relinks.items():
            auto = LibraryFileOwner.objects.filter(rel_path=found, source=LibraryFileOwner.SOURCE_AUTO)
            BookOwner.objects.filter(book__in=_books_at(found), user_id__in=auto.values("user_id")).delete()
            auto.delete()
            transfer(gone, found)
        if admin is not None:
            for rel in to_admin:
                _add_owner(rel, admin.pk, source=LibraryFileOwner.SOURCE_AUTO, action=LibraryOwnershipLog.TO_ADMIN)
        for rel in dropped:
            release(rel, file_exists=False)

        expected: dict[str, set[int]] = {}
        for rel, uid in LibraryFileOwner.objects.values_list("rel_path", "user_id"):
            expected.setdefault(rel, set()).add(uid)
        current: dict[int, set[int]] = {}
        for bid, uid in BookOwner.objects.values_list("book_id", "user_id"):
            current.setdefault(bid, set()).add(uid)
        add, remove = [], []
        for book in Book.objects.filter(avail__gt=0).only("id", "path", "filename", "cat_type").iterator():
            want, have = expected.get(book_rel_path(book), set()), current.get(book.id, set())
            add += [BookOwner(book_id=book.id, user_id=u) for u in want - have]
            remove += [(book.id, u) for u in have - want]
        BookOwner.objects.bulk_create(add, batch_size=1000, ignore_conflicts=True)
        for bid, uid in remove:
            BookOwner.objects.filter(book_id=bid, user_id=uid).delete()
    report.update(book_links_added=len(add), book_links_removed=len(remove), applied=True)
    return report
