"""Жанровая папка библиотеки — только из списка жанров (genres.xml).

Сервер 2026-11-02: в библиотеке были папки «poetry», «prose_military»
(код FB2 вместо жанра) и «Без жанра» (тег пустой или испорчен —
«??????????»). Синхронизация брала первое значение <genre> как есть.
Теперь: имя из списка — как есть, код — через надёжную привязку к жанру,
иначе книга остаётся в исходной папке.
"""
from fb2parser_core.genres_manager import GenresManager
from fb2parser_core.synchronization import SynchronizationService

_GENRES = (
    '<?xml version="1.0" encoding="utf-8"?><genres>'
    '<genre name="Фантастика"><assigned><genre>sf_fantasy</genre></assigned>'
    '<patterns><pattern>sf</pattern></patterns></genre>'
    '<genre name="Детектив"/><genre name="Кинороман"/></genres>'
)


def _service(tmp_path):
    xml = tmp_path / "genres.xml"
    xml.write_text(_GENRES, encoding="utf-8")
    service = SynchronizationService.__new__(SynchronizationService)
    service.genres_manager = GenresManager(xml, reference_path=tmp_path / "no-reference.json")
    return service


def test_genre_name_from_the_list_is_kept(tmp_path):
    service = _service(tmp_path)
    assert service._library_genre("Кинороман") == "Кинороман"
    assert service._library_genre("Кинороман, thriller") == "Кинороман"
    assert service._library_genre("thriller, Детектив") == "Детектив"


def test_genre_name_is_matched_ignoring_case(tmp_path):
    assert _service(tmp_path)._library_genre("фантастика") == "Фантастика"


def test_code_with_exact_association_gives_its_genre(tmp_path):
    assert _service(tmp_path)._library_genre("sf_fantasy") == "Фантастика"


def test_unknown_code_guess_or_damaged_tag_gives_no_genre(tmp_path):
    service = _service(tmp_path)
    assert service._library_genre("poetry") == ""
    assert service._library_genre("prose_military") == ""
    assert service._library_genre("sf_space") == ""          # только «по семейству кода» — не надёжно
    assert service._library_genre("??????????") == ""
    assert service._library_genre("") == ""
