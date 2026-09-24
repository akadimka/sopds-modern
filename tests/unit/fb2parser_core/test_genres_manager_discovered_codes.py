"""Регрессия для `GenresManager.register_discovered_codes()` —
docs/quality-roadmap.md, баг №114.

Пользователь попросил: при каждом скане жанров (кнопки "Scan genres" на
Home и "Start scan" на Genre Combinations — обе идут через один и тот же
`_run_genre_scan_thread()`) новые, ранее неизвестные справочнику коды
должны сразу дописываться в сам справочник (`mygenres.json`) — но ТОЛЬКО
если это латиница; если в значении `<genre>` встретилась кириллица —
это не код жанра, а мусор/ошибка метаданных, и заносить его в справочник
не нужно.
"""
import json

import pytest

from fb2parser_core.genres_manager import GenresManager


@pytest.fixture
def reference_path(tmp_path):
    path = tmp_path / "mygenres.json"
    path.write_text(json.dumps([
        {"model": "opds_catalog.genre", "pk": 1, "fields": {"genre": "sf_action", "section": "Фантастика", "subsection": "Боевая фантастика"}},
        {"model": "opds_catalog.genre", "pk": 5, "fields": {"genre": "detective", "section": "Детективы и Триллеры", "subsection": "Детектив"}},
    ]), encoding="utf-8")
    return path


@pytest.fixture
def gm(tmp_path, reference_path):
    xml_path = tmp_path / "genres.xml"
    xml_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<genres><genre name="Фантастика" /><genre name="Детектив" /></genres>',
        encoding="utf-8",
    )
    return GenresManager(str(xml_path), reference_path=str(reference_path))


class TestRegisterDiscoveredCodes:
    def test_new_latin_code_is_appended_to_reference_file(self, gm, reference_path):
        gm.register_discovered_codes(["totally_new_code"])

        data = json.loads(reference_path.read_text(encoding="utf-8"))
        genres = {item["fields"]["genre"]: item["fields"] for item in data}
        assert "totally_new_code" in genres
        assert genres["totally_new_code"]["section"] == GenresManager.NEW_CODES_SECTION

    def test_returns_count_of_actually_added_codes(self, gm):
        # Баг №114, продолжение: показывается в UI статуса скана
        # ("Codes added to reference:") — должно быть числом РЕАЛЬНО
        # добавленных кодов, не длиной входного списка.
        count = gm.register_discovered_codes(["new_one", "sf_action", "Кириллица", "new_two"])
        assert count == 2

    def test_returns_zero_when_nothing_added(self, gm):
        assert gm.register_discovered_codes(["sf_action", "Кириллица"]) == 0

    def test_new_pk_continues_from_max_existing(self, gm, reference_path):
        gm.register_discovered_codes(["totally_new_code"])

        data = json.loads(reference_path.read_text(encoding="utf-8"))
        new_entry = next(item for item in data if item["fields"]["genre"] == "totally_new_code")
        assert new_entry["pk"] == 6  # max existing pk (5) + 1

    def test_cyrillic_value_is_never_added(self, gm, reference_path):
        gm.register_discovered_codes(["Русский жанр"])

        data = json.loads(reference_path.read_text(encoding="utf-8"))
        assert len(data) == 2  # файл не тронут
        assert not any(item["fields"]["genre"] == "русский жанр" for item in data)

    def test_mixed_latin_and_cyrillic_only_adds_latin(self, gm, reference_path):
        gm.register_discovered_codes(["new_western_code", "Испорченный тег"])

        data = json.loads(reference_path.read_text(encoding="utf-8"))
        genres = [item["fields"]["genre"] for item in data]
        assert "new_western_code" in genres
        assert not any("испорченный" in g.lower() for g in genres)

    def test_already_known_code_is_not_duplicated(self, gm, reference_path):
        gm.register_discovered_codes(["sf_action"])  # уже есть в справочнике

        data = json.loads(reference_path.read_text(encoding="utf-8"))
        assert len(data) == 2

    def test_duplicate_and_empty_codes_in_input_are_handled(self, gm, reference_path):
        gm.register_discovered_codes(["dup_code", "dup_code", "", "  ", None])

        data = json.loads(reference_path.read_text(encoding="utf-8"))
        genres = [item["fields"]["genre"] for item in data]
        assert genres.count("dup_code") == 1

    def test_new_code_immediately_resolvable_after_mapping_new_section(self, gm):
        gm.register_discovered_codes(["brand_new_code"])
        gm.set_section_mapping(GenresManager.NEW_CODES_SECTION, "Фантастика")

        assert gm.resolve_code("brand_new_code") == ("Фантастика", True)

    def test_new_code_unmapped_by_default_is_not_discriminating(self, gm):
        gm.register_discovered_codes(["brand_new_code"])

        assert gm.is_discriminating_code("brand_new_code") is False

    def test_no_write_when_nothing_new_or_all_rejected(self, gm, reference_path):
        before = reference_path.read_text(encoding="utf-8")
        gm.register_discovered_codes(["sf_action", "Кириллица", ""])
        after = reference_path.read_text(encoding="utf-8")
        assert before == after
