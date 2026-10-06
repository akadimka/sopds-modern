"""Назначение жанра архивированным книгам и сохранение переводов строк.

Автосинхронизация переписывает <genre> у скачанных книг, а они часто лежат
в .fb2.zip. Раньше назначение жанра папке такие файлы пропускало, а
назначение отдельному файлу записывало XML поверх архива (файл .zip
переставал быть архивом); XML из архива всегда читался как UTF-8, так что
кириллица в cp1251 портилась. Текстовая запись на Windows превращала
"\r\n" в "\r\r\n".
"""
import zipfile

from fb2parser_core.genre_assign import GenreAssignmentService

_FB2 = (
    '<?xml version="1.0" encoding="{enc}"?>{nl}'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">{nl}'
    '<description>{nl}<title-info>{nl}<genre>sf_action</genre>{nl}'
    '<book-title>Тёмная башня</book-title>{nl}</title-info>{nl}</description>{nl}'
    '<body><section><p>Текст книги.</p></section></body>{nl}</FictionBook>{nl}'
)


class _Silent:
    def log(self, *_a, **_k):
        pass


def _zip(path, inner, data):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(inner, data)


def test_folder_assignment_rewrites_cp1251_fb2_zip_in_place(tmp_path):
    book = tmp_path / "Порция" / "Книга.fb2.zip"
    book.parent.mkdir()
    _zip(book, "Книга.fb2", _FB2.format(enc="windows-1251", nl="\n").encode("cp1251"))

    done = GenreAssignmentService(logger=_Silent()).assign_genre_to_folder(str(book.parent), "Фантастика")

    assert done == 1
    with zipfile.ZipFile(book) as z:
        assert z.namelist() == ["Книга.fb2"]
        text = z.read("Книга.fb2").decode("cp1251")
    assert "<genre>Фантастика</genre>" in text and "sf_action" not in text
    assert "Тёмная башня" in text


def test_file_assignment_keeps_fb2_zip_an_archive(tmp_path):
    book = tmp_path / "Книга.fb2.zip"
    _zip(book, "Книга.fb2", _FB2.format(enc="utf-8", nl="\n").encode("utf-8"))

    result = GenreAssignmentService(logger=_Silent()).assign_genre_to_files([str(book)], "Детектив")

    assert result == {str(book): True}
    assert zipfile.is_zipfile(book)
    with zipfile.ZipFile(book) as z:
        assert "<genre>Детектив</genre>" in z.read("Книга.fb2").decode("utf-8")


def test_crlf_line_endings_are_preserved(tmp_path):
    book = tmp_path / "Книга.fb2"
    book.write_bytes(_FB2.format(enc="utf-8", nl="\r\n").encode("utf-8"))

    assert GenreAssignmentService(logger=_Silent()).assign_genre_to_files([str(book)], "Детектив")[str(book)]

    data = book.read_bytes()
    assert b"\r\r\n" not in data
    assert "<genre>Детектив</genre>".encode() in data
