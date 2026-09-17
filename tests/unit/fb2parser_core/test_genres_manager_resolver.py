"""Регрессия для `GenresManager` — docs/quality-roadmap.md, баги №82 и №84.

Реальный случай (папка "Сборник - «Фантом Пресс»", Test1): 501 файл, 103
разных сочетания сырых кодов `<genre>` — назначать корневой жанр вручную
для каждой из 103 комбинаций непрактично, особенно если то же самое
пришлось бы повторять для каждой новой папки. Механизм точных ассоциаций
(`assigned`) в `GenresManager` уже существовал, но нигде не
использовался для автоматического разрешения — а для стандартных кодов
FB2Genre (`sf_*`, `det_*`...) даже точных ассоциаций недостаточно: их
слишком много, чтобы перечислять по одному.

Решение (по итогам обсуждения с пользователем): двухуровневый резолвер —
точная ассоциация побеждает всегда, грубое правило по семейству кода
(часть до первого "_") — запасной вариант; при неоднозначности между
несколькими корневыми жанрами — приоритетный список.

Баг №84: пользователь заметил, что UI показывал точную ассоциацию (то,
что он САМ уже подтвердил раньше) той же жёлтой "это только
предположение" плашкой, что и грубое совпадение по семейству — резолвер
должен явно возвращать, БЫЛ ли результат получен через точную
ассоциацию, чтобы UI мог их различать.
"""
import pytest

from fb2parser_core.genres_manager import GenresManager


@pytest.fixture
def gm(tmp_path):
    xml_path = tmp_path / "genres.xml"
    xml_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<genres>'
        '<genre name="Фантастика" />'
        '<genre name="Детектив" />'
        '<genre name="Современная проза" />'
        '</genres>',
        encoding="utf-8",
    )
    return GenresManager(str(xml_path))


class TestPatternPersistence:
    def test_pattern_association_round_trips_through_save_load(self, gm):
        gm.associate_pattern("sf", "Фантастика")
        gm.load()
        node = gm.find_node("Фантастика")
        assert node.patterns == ["sf"]

    def test_pattern_association_is_case_insensitive_and_deduped(self, gm):
        gm.associate_pattern("SF", "Фантастика")
        gm.associate_pattern("sf", "Фантастика")
        node = gm.find_node("Фантастика")
        assert node.patterns == ["sf"]

    def test_remove_pattern_association(self, gm):
        gm.associate_pattern("sf", "Фантастика")
        gm.remove_pattern_association("sf", "Фантастика")
        gm.load()
        node = gm.find_node("Фантастика")
        assert node.patterns == []


class TestResolveCode:
    def test_exact_association_wins_over_pattern(self, gm):
        # "sf_romance" грубо подпадает под правило "sf" → Фантастика, но
        # точная ассоциация переопределяет его в "Современная проза".
        gm.associate_pattern("sf", "Фантастика")
        gm.associate("sf_romance", "Современная проза")
        assert gm.resolve_code("sf_romance") == ("Современная проза", True)

    def test_pattern_fallback_when_no_exact_association(self, gm):
        gm.associate_pattern("sf", "Фантастика")
        assert gm.resolve_code("sf_cyberpunk") == ("Фантастика", False)
        assert gm.resolve_code("sf_heroic") == ("Фантастика", False)

    def test_unknown_code_does_not_resolve(self, gm):
        gm.associate_pattern("sf", "Фантастика")
        assert gm.resolve_code("totally_unknown_code") == (None, None)

    def test_pattern_matches_family_before_first_underscore_only(self, gm):
        # "detective" (реальный код FB2Genre, без "_") не должен ошибочно
        # совпасть с правилом "det" — семейство "detective" целиком, а не "det".
        gm.associate_pattern("det", "Детектив")
        assert gm.resolve_code("detective") == (None, None)
        assert gm.resolve_code("det_classic") == ("Детектив", False)


class TestResolveCombo:
    def test_all_codes_agree_on_one_genre_via_pattern(self, gm):
        gm.associate_pattern("sf", "Фантастика")
        assert gm.resolve_combo("sf_action, sf_space, sf_heroic") == ("Фантастика", False)

    def test_exact_association_marks_combo_as_confirmed(self, gm):
        # Баг №84: если хотя бы один код комбинации точно ассоциирован с
        # победившим жанром — вся комбинация помечается как подтверждённая
        # (is_exact=True), даже если остальные коды комбинации разрешились
        # только по грубому правилу.
        gm.associate_pattern("sf", "Фантастика")
        gm.associate("sf_action", "Фантастика")
        assert gm.resolve_combo("sf_action, sf_space") == ("Фантастика", True)

    def test_conflicting_genres_use_priority_order(self, gm):
        gm.associate_pattern("sf", "Фантастика")
        gm.associate("foreign_prose", "Современная проза")
        combo = "sf_action, foreign_prose"
        assert gm.resolve_combo(combo, priority_order=["Современная проза", "Фантастика"]) == ("Современная проза", True)
        assert gm.resolve_combo(combo, priority_order=["Фантастика", "Современная проза"]) == ("Фантастика", False)

    def test_partially_unresolved_combo_still_resolves_from_known_codes(self, gm):
        gm.associate_pattern("sf", "Фантастика")
        assert gm.resolve_combo("sf_action, totally_unknown_code") == ("Фантастика", False)

    def test_fully_unresolved_combo_returns_none(self, gm):
        gm.associate_pattern("sf", "Фантастика")
        assert gm.resolve_combo("totally_unknown_a, totally_unknown_b") == (None, None)

    def test_empty_combo_returns_none(self, gm):
        assert gm.resolve_combo("") == (None, None)
