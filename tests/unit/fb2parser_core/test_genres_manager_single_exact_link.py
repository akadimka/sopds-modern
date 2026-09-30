"""Код должен иметь не больше одной точной привязки к жанру.

Обучение при применении жанра к набору кодов (`associate_many`) добавляло
код к новому жанру, не убирая у старого; при нескольких привязках
побеждает первая по дереву, поэтому обучение ничего не меняло, а только
копило противоречия (`thriller` оказался привязан к «Фантастике»,
«Триллеру» и «Современной прозе» сразу и всегда давал «Фантастику»).
"""
import json

import pytest

from fb2parser_core.genres_manager import GenresManager


@pytest.fixture
def gm(tmp_path):
    reference = tmp_path / "mygenres.json"
    reference.write_text(json.dumps([
        {"fields": {"genre": "thriller", "section": "Детективы и Триллеры", "subsection": "Триллер"}},
    ]), encoding="utf-8")
    xml = tmp_path / "genres.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?><genres>'
        '<genre name="Фантастика" />'
        '<genre name="Детектив"><genre name="Триллер" /></genre>'
        '</genres>',
        encoding="utf-8",
    )
    return GenresManager(str(xml), reference_path=str(reference))


def test_explicit_associate_moves_code_instead_of_duplicating(gm):
    gm.associate("thriller", "Фантастика")
    gm.associate("thriller", "Триллер")
    assert gm.exact_genres("thriller") == ["Триллер"]
    assert gm.resolve_code("thriller") == ("Триллер", True)


def test_associate_to_unknown_genre_keeps_existing_link(gm):
    gm.associate("thriller", "Триллер")
    gm.associate("thriller", "Нет такого жанра")
    assert gm.exact_genres("thriller") == ["Триллер"]


def test_learning_skips_code_already_linked_elsewhere(gm):
    gm.associate("thriller", "Триллер")
    gm.associate_many([("thriller", "Фантастика"), ("sf_space", "Фантастика")])
    assert gm.exact_genres("thriller") == ["Триллер"]
    assert gm.exact_genres("sf_space") == ["Фантастика"]


def test_learning_same_code_twice_in_batch_keeps_first(gm):
    gm.associate_many([("new_code", "Фантастика"), ("new_code", "Детектив")])
    assert gm.exact_genres("new_code") == ["Фантастика"]


def test_exact_genres_lists_contradictions_in_tree_order(gm, tmp_path):
    xml = tmp_path / "genres.xml"
    xml.write_text(
        '<?xml version="1.0" encoding="utf-8"?><genres>'
        '<genre name="Фантастика"><assigned><genre>thriller</genre></assigned></genre>'
        '<genre name="Детектив"><genre name="Триллер"><assigned><genre>thriller</genre></assigned></genre></genre>'
        '</genres>',
        encoding="utf-8",
    )
    gm.load()
    assert gm.exact_genres("thriller") == ["Фантастика", "Триллер"]
    row = next(r for r in gm.list_reference_codes() if r["code"] == "thriller")
    assert row["exact_genres"] == ["Фантастика", "Триллер"]
