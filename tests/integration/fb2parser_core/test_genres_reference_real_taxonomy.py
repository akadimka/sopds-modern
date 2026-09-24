"""Регрессия на РЕАЛЬНОМ справочнике `opds_catalog/fixtures/mygenres.json`
— docs/quality-roadmap.md, баг №113.

Реальный случай: Genre Combinations предложил жанр «Фантастика» для
`adv_indian, antique_myths, compilation, autor_collection,
network_literature, literature_20` и `adv_indian, travel_notes,
naturalist_notes, compilation, autor_collection, network_literature,
literature_20` (скриншот пользователя) — набор кодов про индейцев/
мифы/путевые заметки, ни один из которых на самом деле не про
фантастику. Причина: `assigned` «Фантастики» накопил эти коды слепым
обучением (`genre_scan_assign()` запоминал КАЖДЫЙ код применённой
комбинации, включая коды не относящихся к делу произведений).

В отличие от `tests/unit/fb2parser_core/test_genres_manager_section_map.py`
(свой маленький справочник, полная изоляция) — здесь намеренно
используется РЕАЛЬНЫЙ committed `mygenres.json`, чтобы застраховаться от
регрессии именно в том виде, в каком баг был найден на практике.
"""
from fb2parser_core.genres_manager import GenresManager


class TestRealReferenceCategorizesMisassignedCodes:
    """Коды, которые исторически осели в assigned «Фантастики», по
    официальному справочнику относятся к совсем другим разделам."""

    def test_adv_indian_is_adventure_not_scifi(self, tmp_path):
        gm = GenresManager(str(tmp_path / "genres.xml"))
        section, subsection = gm._reference["adv_indian"]
        assert section == "Приключения"

    def test_antique_myths_is_folklore(self, tmp_path):
        gm = GenresManager(str(tmp_path / "genres.xml"))
        section, _ = gm._reference["antique_myths"]
        assert section == "Фольклор"

    def test_travel_notes_is_nonfiction(self, tmp_path):
        gm = GenresManager(str(tmp_path / "genres.xml"))
        section, _ = gm._reference["travel_notes"]
        assert section == "Документальная литература"

    def test_network_literature_is_not_a_genre_section(self, tmp_path):
        gm = GenresManager(str(tmp_path / "genres.xml"))
        section, _ = gm._reference["network_literature"]
        assert section == "Прочее"

    def test_compilation_and_friends_are_absent_from_reference(self, tmp_path):
        # Никогда не были в справочнике вообще — не жанр, а формат
        # публикации; ловятся только через excluded_codes, не через секцию.
        gm = GenresManager(str(tmp_path / "genres.xml"))
        for code in ("compilation", "collection", "autor_collection", "fan_translation", "su_publication"):
            assert code not in gm._reference


class TestOriginalScreenshotCombosNoLongerFalselyResolveToScifi:
    """С согласованными 4 сопоставлениями section_map + 5 excluded_codes
    (баг №113, миграция реальных данных) — обе комбинации из скриншота
    больше не резолвятся в «Фантастика» (ожидаемо None — коды из
    «Фольклор»/подобных секций пока не размечены пользователем)."""

    def _make_manager(self, tmp_path):
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
        gm = GenresManager(str(xml_path))
        gm.set_section_mapping("Приключения", "Приключения")
        gm.set_section_mapping("Документальная литература", "Non-Fiction")
        for code in ("compilation", "collection", "autor_collection", "fan_translation", "su_publication"):
            gm.add_excluded_code(code)
        return gm

    def test_combo_one_no_longer_resolves_to_scifi(self, tmp_path):
        gm = self._make_manager(tmp_path)
        combo = "adv_indian, antique_myths, compilation, autor_collection, network_literature, literature_20"
        genre, _ = gm.resolve_combo(combo)
        assert genre != "Фантастика"

    def test_combo_two_no_longer_resolves_to_scifi(self, tmp_path):
        gm = self._make_manager(tmp_path)
        combo = "adv_indian, travel_notes, naturalist_notes, compilation, autor_collection, network_literature, literature_20"
        genre, _ = gm.resolve_combo(combo)
        assert genre != "Фантастика"
