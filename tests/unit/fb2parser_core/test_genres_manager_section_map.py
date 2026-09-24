"""Регрессия для `GenresManager` — docs/quality-roadmap.md, баг №113.

Реальный случай: Genre Combinations предлагал жанр «Фантастика» для
комбинаций вроде `adv_indian, antique_myths, compilation,
autor_collection, network_literature, literature_20` — набор кодов про
индейцев/мифы/путевые заметки, никак не про фантастику. Причина —
`genre_scan_assign()` слепо запоминал КАЖДЫЙ код применённой комбинации
как точную ассоциацию (`assigned`) с выбранным жанром, включая коды
совсем других произведений и коды, вообще не являющиеся жанровым
сигналом (формат публикации: "compilation", "collection" и т.п.).

Решение: третья, объективная ступень резолвинга — официальный справочник
FB2-таксономии (`opds_catalog/fixtures/mygenres.json`, код → секция) +
пользовательская таблица `section_map` (секция → жанр) — и явный список
`excluded_codes` для кодов, которые НИКОГДА не должны учитываться как
жанровый сигнал, даже если справочник о них ничего не знает.

Тесты здесь используют СВОЙ маленький справочник (`reference_path=`),
не реальный `mygenres.json` — реальные данные проверяются отдельно в
`tests/integration/fb2parser_core/test_genres_reference_real_taxonomy.py`.
"""
import json

import pytest

from fb2parser_core.genres_manager import GenresManager


@pytest.fixture
def reference_path(tmp_path):
    path = tmp_path / "mygenres.json"
    path.write_text(json.dumps([
        {"fields": {"genre": "adv_indian", "section": "Приключения", "subsection": "Вестерн, про индейцев"}},
        {"fields": {"genre": "antique_myths", "section": "Фольклор", "subsection": "Мифы. Легенды. Эпос"}},
        {"fields": {"genre": "travel_notes", "section": "Документальная литература", "subsection": "География"}},
        {"fields": {"genre": "network_literature", "section": "Прочее", "subsection": "Самиздат"}},
    ]), encoding="utf-8")
    return path


@pytest.fixture
def gm(tmp_path, reference_path):
    xml_path = tmp_path / "genres.xml"
    xml_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<genres>'
        '<genre name="Фантастика" />'
        '<genre name="Приключения" />'
        '<genre name="Non-Fiction" />'
        '</genres>',
        encoding="utf-8",
    )
    return GenresManager(str(xml_path), reference_path=str(reference_path))


class TestSectionMapPersistence:
    def test_round_trips_through_save_load(self, gm):
        gm.set_section_mapping("Приключения", "Приключения")
        gm.load()
        assert gm.get_section_map() == {"Приключения": "Приключения"}

    def test_empty_genre_name_unsets_mapping(self, gm):
        gm.set_section_mapping("Приключения", "Приключения")
        gm.set_section_mapping("Приключения", "")
        assert gm.get_section_map() == {}

    def test_persisted_across_new_instance(self, gm, tmp_path, reference_path):
        gm.set_section_mapping("Документальная литература", "Non-Fiction")
        gm2 = GenresManager(str(tmp_path / "genres.xml"), reference_path=str(reference_path))
        assert gm2.get_section_map() == {"Документальная литература": "Non-Fiction"}


class TestExcludedCodesPersistence:
    def test_add_and_remove_round_trip(self, gm, tmp_path, reference_path):
        gm.add_excluded_code("Compilation")
        gm2 = GenresManager(str(tmp_path / "genres.xml"), reference_path=str(reference_path))
        assert gm2.get_excluded_codes() == {"compilation"}

        gm2.remove_excluded_code("COMPILATION")
        gm3 = GenresManager(str(tmp_path / "genres.xml"), reference_path=str(reference_path))
        assert gm3.get_excluded_codes() == set()


class TestResolveCodeTierOrder:
    def test_section_map_resolves_when_no_exact_association(self, gm):
        gm.set_section_mapping("Приключения", "Приключения")
        assert gm.resolve_code("adv_indian") == ("Приключения", True)

    def test_exact_association_wins_over_section_map(self, gm):
        gm.set_section_mapping("Приключения", "Приключения")
        gm.associate("adv_indian", "Фантастика")
        assert gm.resolve_code("adv_indian") == ("Фантастика", True)

    def test_unmapped_section_does_not_resolve(self, gm):
        # "Фольклор" не размечен в section_map — antique_myths не должен
        # резолвиться ни во что, а не подхватываться первым попавшимся жанром.
        assert gm.resolve_code("antique_myths") == (None, None)

    def test_pattern_fallback_still_works_for_codes_outside_reference(self, gm):
        gm.associate_pattern("sf", "Фантастика")
        assert gm.resolve_code("sf_cyberpunk") == ("Фантастика", False)

    def test_dangling_section_map_reference_is_ignored(self, gm):
        # Жанр указан в section_map, но узла с таким именем уже нет в дереве
        # (напр. после ручного редактирования genres.xml в обход API).
        gm.section_map["Приключения"] = "Несуществующий Жанр"
        assert gm.resolve_code("adv_indian") == (None, None)


class TestIsDiscriminatingCode:
    def test_excluded_code_is_never_discriminating(self, gm):
        gm.add_excluded_code("compilation")
        assert gm.is_discriminating_code("compilation") is False

    def test_code_with_unmapped_section_is_not_discriminating(self, gm):
        # "Прочее" (network_literature) — известная в справочнике, но
        # намеренно не сопоставленная секция: формат публикации, не жанр.
        assert gm.is_discriminating_code("network_literature") is False

    def test_code_with_mapped_section_is_discriminating(self, gm):
        gm.set_section_mapping("Приключения", "Приключения")
        assert gm.is_discriminating_code("adv_indian") is True

    def test_unknown_code_defaults_to_discriminating(self, gm):
        assert gm.is_discriminating_code("totally_unknown_code") is True

    def test_empty_code_is_not_discriminating(self, gm):
        assert gm.is_discriminating_code("") is False


class TestListSections:
    def test_counts_and_current_mapping(self, gm):
        gm.set_section_mapping("Приключения", "Приключения")
        sections = {s["section"]: s for s in gm.list_sections()}
        assert sections["Приключения"]["count"] == 1
        assert sections["Приключения"]["genre"] == "Приключения"
        assert sections["Фольклор"]["genre"] is None


class TestClearAllAssigned:
    def test_wipes_assigned_but_keeps_patterns_and_section_map(self, gm):
        gm.associate("adv_indian", "Фантастика")
        gm.associate_pattern("sf", "Фантастика")
        gm.set_section_mapping("Приключения", "Приключения")

        gm.clear_all_assigned()

        node = gm.find_node("Фантастика")
        assert node.assigned == set()
        assert node.patterns == ["sf"]
        assert gm.get_section_map() == {"Приключения": "Приключения"}


class TestRenameDeleteKeepSectionMapConsistent:
    def test_rename_updates_section_map_value(self, gm):
        gm.set_section_mapping("Приключения", "Приключения")
        gm.rename_node("Приключения", "Приключения и вестерны")
        assert gm.get_section_map() == {"Приключения": "Приключения и вестерны"}

    def test_delete_removes_dangling_section_map_entry(self, gm):
        gm.set_section_mapping("Приключения", "Приключения")
        gm.delete_node("Приключения")
        assert gm.get_section_map() == {}
