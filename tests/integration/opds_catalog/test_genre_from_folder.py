import os
import shutil

import pytest

from opds_catalog import opdsdb
from opds_catalog.models import Book
from opds_catalog.opdsdb import unknown_genre
from opds_catalog.sopdscan import opdsScanner

TEST_FB2 = "262001.fb2"  # содержит внутренний тег жанра "antique" — намеренно
# другой, чтобы проверить, что папка его перекрывает, а не дополняет.


def _fixture_fb2_path() -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "data", TEST_FB2
    )


@pytest.mark.django_db
class TestGenreFromFolder:
    """Регрессия: жанр книги должен определяться папкой 1-го уровня библиотеки
    (genre/author/...), а не внутренними тегами FB2 — пользователь физически
    переименовывает жанровые папки на диске и ожидает, что очередной скан
    (ручной или автоматический) применит новое имя как жанр к файлам внутри.
    """

    def test_genre_taken_from_top_level_folder_not_fb2_tags(self, tmp_path, override_config):
        genre_dir = tmp_path / "Детективы" / "Тестовый Автор"
        genre_dir.mkdir(parents=True)
        shutil.copyfile(_fixture_fb2_path(), genre_dir / TEST_FB2)

        with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
            opdsdb.clear_all()
            opdsScanner().scan_all()

        book = Book.objects.get(filename=TEST_FB2)
        assert book.genres.count() == 1
        genre = book.genres.get()
        assert genre.genre == "Детективы"
        assert genre.section == "Детективы"
        # Внутренний тег файла ("antique") не должен просочиться в жанр.
        assert not book.genres.filter(genre="antique").exists()

    def test_renaming_genre_folder_on_disk_changes_genre_on_rescan(self, tmp_path, override_config):
        old_dir = tmp_path / "Фантастика" / "Тестовый Автор"
        old_dir.mkdir(parents=True)
        shutil.copyfile(_fixture_fb2_path(), old_dir / TEST_FB2)

        with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
            opdsdb.clear_all()
            opdsScanner().scan_all()
            assert Book.objects.get(filename=TEST_FB2).genres.get().section == "Фантастика"

            # Переименование папки-жанра на физическом уровне (не в БД).
            new_dir = tmp_path / "Мистика" / "Тестовый Автор"
            new_dir.parent.mkdir(parents=True, exist_ok=True)
            old_dir.rename(new_dir)

            opdsScanner().scan_all()

        # Старая запись (по прежнему пути) остаётся в БД мягко удалённой
        # (avail=0, SOPDS_DELETE_LOGICAL по умолчанию) — интересует только
        # актуальная, всё ещё доступная книга по новому пути.
        book = Book.objects.get(filename=TEST_FB2, avail__gt=0)
        assert book.genres.count() == 1
        assert book.genres.get().section == "Мистика"

    def test_book_at_library_root_falls_back_to_unknown_genre(self, tmp_path, override_config):
        shutil.copyfile(_fixture_fb2_path(), tmp_path / TEST_FB2)

        with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
            opdsdb.clear_all()
            opdsScanner().scan_all()

        book = Book.objects.get(filename=TEST_FB2)
        assert book.genres.get().section == str(unknown_genre)


@pytest.mark.django_db
class TestAuthorAndGenreForBooksInsideZip:
    """Для книги из zip rel_path кончается именем архива; оно не уровень
    папок. Раньше «Жанр/сборник.zip» давал автора «сборник.zip», а zip в
    корне библиотеки — жанр с именем архива."""

    ZIP = os.path.join(os.path.dirname(_fixture_fb2_path()), "books.zip")

    def _scan(self, tmp_path, override_config, *folders):
        target = tmp_path.joinpath(*folders) if folders else tmp_path
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.ZIP, target / "сборник.zip")
        with override_config(SOPDS_ROOT_LIB=str(tmp_path)):
            opdsdb.clear_all()
            opdsScanner().scan_all()
        return Book.objects.get(filename="539603.fb2")

    def test_zip_in_genre_author_folder(self, tmp_path, override_config):
        book = self._scan(tmp_path, override_config, "Фантастика", "Логинов Святослав")
        assert book.authors.get().full_name == "Логинов Святослав"
        assert book.genres.get().section == "Фантастика"

    def test_zip_in_genre_folder_has_no_author_named_after_archive(self, tmp_path, override_config):
        book = self._scan(tmp_path, override_config, "Фантастика")
        assert book.authors.get().full_name != "сборник.zip"
        assert book.genres.get().section == "Фантастика"

    def test_zip_at_root_has_unknown_genre(self, tmp_path, override_config):
        book = self._scan(tmp_path, override_config)
        assert book.genres.get().section == str(unknown_genre)
