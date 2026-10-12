"""Смена жанра в файле, где жанр не кодируется кодировкой файла (баг №139).

Реальный случай: «Кувайкова Анна - Хеллиана Валанди 1. Друзей не выбирают.
Эпизод 1.fb2» — повреждённый файл из обрывков трёх FB2 с разными
кодировками, первый объявлен encoding="latin-1". Кириллического жанра в
latin-1 нет, и запись с errors='replace' молча превращала его в «??????????».
"""
from fb2parser_core.genre_assign import GenreAssignmentService

_FB2 = (
    '<?xml version="1.0" encoding="latin-1"?>\n'
    '<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">'
    "<description><title-info><genre>sf</genre><book-title>Book</book-title>"
    "</title-info></description><body><section><p>Text</p></section></body></FictionBook>\n"
)


def test_unencodable_genre_is_an_error_and_file_untouched(tmp_path):
    path = tmp_path / "book.fb2"
    path.write_bytes(_FB2.encode("latin-1"))
    before = path.read_bytes()
    assert GenreAssignmentService()._assign_genre_to_file(path, "Фантастика") is False
    assert path.read_bytes() == before


def test_encodable_genre_still_written(tmp_path):
    path = tmp_path / "book.fb2"
    path.write_bytes(_FB2.encode("latin-1"))
    assert GenreAssignmentService()._assign_genre_to_file(path, "fantasy") is True
    assert b"<genre>fantasy</genre>" in path.read_bytes()
