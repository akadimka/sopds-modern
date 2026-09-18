"""Регрессия для `GenresManager.associate_many()` — docs/quality-roadmap.md,
баг №102.

Найдено при архитектурном аудите: `associate()` безусловно делает полный
`load()` (перепарсить весь `genres.xml`) и, при новой ассоциации,
`save()` (пересериализовать всё дерево + temp-file+replace) на КАЖДЫЙ
вызов. Единственный вызывающий (`genre_scan_assign()` в
`fb2parser_web/views.py`) вызывал `associate()` в цикле — один раз на
каждый код каждого набора комбо за батч, то есть до M полных циклов
чтения+перезаписи файла таксономии вместо одного load, N мутаций,
одного save.
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
        '</genres>',
        encoding="utf-8",
    )
    return GenresManager(str(xml_path))


def _count_calls(obj, method_name):
    """Оборачивает метод счётчиком вызовов, сохраняя оригинальное поведение."""
    original = getattr(obj, method_name)
    calls = []

    def _wrapper(*args, **kwargs):
        calls.append((args, kwargs))
        return original(*args, **kwargs)

    setattr(obj, method_name, _wrapper)
    return calls


class TestAssociateManyAppliesAllPairs:
    def test_all_pairs_applied_in_memory(self, gm):
        gm.associate_many([
            ("sf_action", "Фантастика"),
            ("sf_cyberpunk", "Фантастика"),
            ("det_classic", "Детектив"),
        ])

        fant = gm.find_node("Фантастика")
        det = gm.find_node("Детектив")
        assert "sf_action" in fant.assigned
        assert "sf_cyberpunk" in fant.assigned
        assert "det_classic" in det.assigned

    def test_persisted_to_disk(self, gm, tmp_path):
        gm.associate_many([("sf_action", "Фантастика"), ("det_classic", "Детектив")])

        gm2 = GenresManager(str(tmp_path / "genres.xml"))
        assert "sf_action" in gm2.find_node("Фантастика").assigned
        assert "det_classic" in gm2.find_node("Детектив").assigned


class TestAssociateManyBatchesIO:
    def test_saves_exactly_once_for_many_new_pairs(self, gm):
        save_calls = _count_calls(gm, "save")

        gm.associate_many([
            ("sf_action", "Фантастика"),
            ("sf_cyberpunk", "Фантастика"),
            ("sf_space", "Фантастика"),
            ("det_classic", "Детектив"),
            ("det_noir", "Детектив"),
        ])

        assert len(save_calls) == 1

    def test_loads_exactly_once_regardless_of_pair_count(self, gm):
        load_calls = _count_calls(gm, "load")

        gm.associate_many([(f"code{i}", "Фантастика") for i in range(20)])

        assert len(load_calls) == 1

    def test_no_save_when_all_pairs_already_associated(self, gm):
        gm.associate("sf_action", "Фантастика")
        save_calls = _count_calls(gm, "save")

        gm.associate_many([("sf_action", "Фантастика")])

        assert len(save_calls) == 0
