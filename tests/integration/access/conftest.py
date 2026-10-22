"""Две личные библиотеки (Анна и Борис) и суперпользователь — общая основа
тестов изоляции (docs/multiuser-plan.md шаги 1.6–1.9).

Книги показываются по имени файла («Автор - Название»), поэтому у каждой —
свой файл с таким именем (копия тестовой FB2 во временной библиотеке):
иначе на странице нельзя отличить чужую книгу от своей."""
import os
import shutil

import pytest
from django.contrib.auth.models import User

from opds_catalog.models import (
    Author,
    Book,
    BookOwner,
    Catalog,
    Genre,
    Series,
    bauthor,
    bgenre,
    bseries,
)


@pytest.fixture
def lib(override_config, test_rootlib, simple_fb2, tmp_path):
    with override_config(SOPDS_AUTH=True, SOPDS_ROOT_LIB=str(tmp_path), SOPDS_DOUBLES_HIDE=False):
        users = {n: User.objects.create_user(n) for n in ("anna", "boris")}   # без пароля — вход через force_login, хеширование медленное
        users["admin"] = User.objects.create_user("admin", is_superuser=True, is_staff=True)
        root = Catalog.objects.create(cat_name=".", path=".")
        boris_cat = Catalog.objects.create(cat_name="Тайник Бориса", path="Тайник Бориса", parent=root)
        genre = Genre.objects.create(genre="sf", section="Фантастика", subsection="Космос")
        books = {}
        for who, title, cat in (("anna", "Дневник Анны", root), ("boris", "Секрет Бориса", boris_cat)):
            name = f"Автор {title.split()[1]}"
            filename = f"{name} - {title}.fb2"
            shutil.copy(os.path.join(test_rootlib, simple_fb2), tmp_path / filename)
            b = Book.objects.create(filename=filename, path=".", catalog=cat, avail=2, format="fb2",
                                    docdate="", lang="ru", title=title, search_title=title.upper(),
                                    annotation="", lang_code=1)
            bauthor.objects.create(book=b, author=Author.objects.create(
                full_name=name, search_full_name=name.upper(), lang_code=1))
            bseries.objects.create(book=b, ser=Series.objects.create(
                ser=f"Серия {title}", search_ser=f"СЕРИЯ {title}".upper(), lang_code=1))
            bgenre.objects.create(book=b, genre=genre)
            BookOwner.objects.create(book=b, user=users[who])
            books[who] = b
        yield users, books, boris_cat
