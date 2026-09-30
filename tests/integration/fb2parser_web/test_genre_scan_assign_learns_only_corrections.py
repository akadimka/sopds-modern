"""Регрессия для `genre_scan_assign()` — docs/quality-roadmap.md, баг №113.

Реальный случай: при каждом «Apply» на странице Genre Combinations
запоминался КАЖДЫЙ код применённой комбинации как точная ассоциация с
выбранным жанром — включая коды совсем других произведений
(`adv_indian`/`antique_myths` — вестерн/фольклор, не фантастика) и коды-
форматы публикации ("compilation" и т.п.), которые вообще не жанровый
сигнал. Со временем это загрязняло `assigned` любого жанра.

Проверяем на одном вызове `genre_scan_assign()` с комбо из трёх кодов:
- один код без какого-либо резолвинга вообще (discriminating, пробел) —
  ДОЛЖЕН запомниться;
- один код, чья секция явно исключена (`excluded_codes`) — НЕ должен
  запомниться, что бы к нему ни применили;
- один код, уже корректно резолвящийся в ТОТ ЖЕ жанр через section_map
  — НЕ должен переподтверждаться повторной точной ассоциацией (незачем).
"""
import json

import pytest
from django.test import RequestFactory

from fb2parser_core.genres_manager import GenresManager
from fb2parser_web.views import genre_scan_assign, genre_scan_job

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook>
<description>
<title-info>
<genre>{genre1}</genre>
<genre>{genre2}</genre>
<genre>{genre3}</genre>
<author><first-name>Тест</first-name></author>
<book-title>Книга</book-title>
</title-info>
</description>
<body><section><p>Текст</p></section></body>
</FictionBook>
"""


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture(autouse=True)
def _reset_genre_scan_job():
    genre_scan_job.reset()
    yield
    genre_scan_job.reset()


@pytest.fixture
def gm(tmp_path):
    reference_path = tmp_path / "mygenres.json"
    reference_path.write_text(json.dumps([
        {"fields": {"genre": "adv_indian", "section": "Приключения", "subsection": "Вестерн"}},
        {"fields": {"genre": "compilation_like", "section": "Прочее", "subsection": "Самиздат"}},
    ]), encoding="utf-8")

    xml_path = tmp_path / "genres.xml"
    xml_path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<genres><genre name="Приключения" /><genre name="Фантастика" /></genres>',
        encoding="utf-8",
    )
    manager = GenresManager(str(xml_path), reference_path=str(reference_path))
    # "Приключения" секция уже размечена на жанр "Приключения" — код
    # adv_indian корректно резолвится сам по себе, без ручной ассоциации.
    manager.set_section_mapping("Приключения", "Приключения")
    return manager


class TestGenreScanAssignLearnsOnlyRealCorrections:
    def test_learns_gap_skips_excluded_and_skips_already_correct(self, tmp_path, rf, admin_user, monkeypatch, gm):
        folder = tmp_path / "books"
        folder.mkdir()
        fb2 = folder / "1.fb2"
        fb2.write_text(_FB2_TEMPLATE.format(
            genre1="totally_new_code",   # пробел — должен запомниться на "Приключения"
            genre2="excluded_format",    # исключённый код — не должен запомниться
            genre3="adv_indian",         # уже верно резолвится через section_map — не переподтверждать
        ), encoding="utf-8")
        gm.add_excluded_code("excluded_format")

        monkeypatch.setattr("fb2parser_web.fb2parser_bridge.get_genres_manager", lambda: gm)

        combo = "totally_new_code, excluded_format, adv_indian"
        genre_scan_job.update(
            done=True, running=False,
            results={combo: [str(fb2)]},
            errors=[],
        )

        request = rf.post(
            "/fb2parser/genre-scan/assign/",
            data=json.dumps({"mappings": {combo: "Приключения"}}),
            content_type="application/json",
        )
        request.user = admin_user
        response = genre_scan_assign(request)
        data = json.loads(response.content)
        assert data["results"][combo]["success"] == 1

        node = gm.find_node("Приключения")
        assert "totally_new_code" in node.assigned
        assert "excluded_format" not in node.assigned
        assert "adv_indian" not in node.assigned


def _post_mappings(rf, admin_user, mappings):
    request = rf.post(
        "/fb2parser/genre-scan/assign/",
        data=json.dumps({"mappings": mappings}),
        content_type="application/json",
    )
    request.user = admin_user
    return json.loads(genre_scan_assign(request).content)


class TestGenreScanAssignKeepsOneExactLinkPerCode:
    """Код не должен получать вторую точную привязку: при нескольких
    побеждает первая по дереву, и обучение ничего не меняло бы."""

    def _book(self, folder, name, codes):
        path = folder / name
        path.write_text(_FB2_TEMPLATE.format(genre1=codes[0], genre2=codes[1], genre3=codes[1]), encoding="utf-8")
        return str(path)

    def test_code_already_linked_is_not_linked_again(self, tmp_path, rf, admin_user, monkeypatch, gm):
        folder = tmp_path / "books"
        folder.mkdir()
        gm.associate("thriller_x", "Фантастика")
        monkeypatch.setattr("fb2parser_web.fb2parser_bridge.get_genres_manager", lambda: gm)

        combo = "thriller_x, fresh_a"
        genre_scan_job.update(done=True, running=False, errors=[],
                              results={combo: [self._book(folder, "1.fb2", ["thriller_x", "fresh_a"])]})
        _post_mappings(rf, admin_user, {combo: "Приключения"})

        assert gm.exact_genres("thriller_x") == ["Фантастика"]
        assert gm.exact_genres("fresh_a") == ["Приключения"]

    def test_code_applied_with_different_genres_in_one_batch_not_learned(self, tmp_path, rf, admin_user, monkeypatch, gm):
        folder = tmp_path / "books"
        folder.mkdir()
        monkeypatch.setattr("fb2parser_web.fb2parser_bridge.get_genres_manager", lambda: gm)

        combo_a, combo_b = "shared_code, only_a", "shared_code, only_b"
        genre_scan_job.update(done=True, running=False, errors=[], results={
            combo_a: [self._book(folder, "a.fb2", ["shared_code", "only_a"])],
            combo_b: [self._book(folder, "b.fb2", ["shared_code", "only_b"])],
        })
        _post_mappings(rf, admin_user, {combo_a: "Приключения", combo_b: "Фантастика"})

        assert gm.exact_genres("shared_code") == []
        assert gm.exact_genres("only_a") == ["Приключения"]
        assert gm.exact_genres("only_b") == ["Фантастика"]
