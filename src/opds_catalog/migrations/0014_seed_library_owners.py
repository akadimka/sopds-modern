"""Вся существующая библиотека — во владении суперпользователя
(docs/multiuser-design.md, docs/multiuser-plan.md шаг 1.1).

Владение заполняется по каталогу (`Book` с avail > 0), без обхода диска:
файлы, которых нет в каталоге, добирает команда сверки
`library_owners_check`. Нет активного суперпользователя — ничего не
заполняется (файлы без владельца по правилу принадлежат суперпользователю,
сверка назначит его, когда он появится)."""
import posixpath
import unicodedata

from django.conf import settings
from django.db import migrations

CAT_NORMAL = 0
BATCH = 1000


def _rel_path(path, filename, cat_type):
    # Замороженная копия ownership.rel_path для записей каталога: обычная
    # книга — папка + имя файла, книга в zip/inpx — путь архива.
    p = path if cat_type != CAT_NORMAL else posixpath.join(path.replace("\\", "/"), filename)
    p = posixpath.normpath(p.replace("\\", "/"))
    return unicodedata.normalize("NFC", p.lstrip("/"))


def forwards(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Book = apps.get_model("opds_catalog", "Book")
    LibraryFileOwner = apps.get_model("opds_catalog", "LibraryFileOwner")
    BookOwner = apps.get_model("opds_catalog", "BookOwner")
    admin = User.objects.filter(is_superuser=True, is_active=True).order_by("id").first()
    if admin is None:
        return
    seen, files, links = set(), [], []
    for book_id, path, filename, cat_type in (
            Book.objects.filter(avail__gt=0).values_list("id", "path", "filename", "cat_type").iterator()):
        rel = _rel_path(path, filename, cat_type)
        if rel not in seen:
            seen.add(rel)
            files.append(LibraryFileOwner(rel_path=rel, user=admin, source="migration"))
        links.append(BookOwner(book_id=book_id, user=admin))
    LibraryFileOwner.objects.bulk_create(files, batch_size=BATCH, ignore_conflicts=True)
    BookOwner.objects.bulk_create(links, batch_size=BATCH, ignore_conflicts=True)


def backwards(apps, schema_editor):
    apps.get_model("opds_catalog", "LibraryFileOwner").objects.filter(source="migration").delete()
    apps.get_model("opds_catalog", "BookOwner").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("opds_catalog", "0013_library_ownership"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
