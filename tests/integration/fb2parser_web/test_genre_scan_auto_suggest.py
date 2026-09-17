"""Регрессия для `genre_scan_results()`/`genre_scan_assign()` —
docs/quality-roadmap.md, баг №82.

Реальный случай: папка "Сборник - «Фантом Пресс»" (Test1) — 501 файл,
103 разных сочетания сырых кодов `<genre>`. Проверяем, что панель
результатов подсказывает корневой жанр для узнаваемых наборов
(`GenresManager.resolve_combo()`), а подтверждённое назначение (авто или
вручную) сразу запоминается как точная ассоциация — следующий скан
разрешит тот же код уже без подсказки-по-паттерну.
"""
import json

import pytest
from django.test import RequestFactory

from fb2parser_core.genres_manager import GenresManager
from fb2parser_web.views import genre_scan_assign, genre_scan_job, genre_scan_results


@pytest.fixture
def genres_xml(tmp_path):
    path = tmp_path / "genres.xml"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<genres><genre name="Фантастика" /><genre name="Детектив" /></genres>',
        encoding="utf-8",
    )
    return path


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture(autouse=True)
def _reset_genre_scan_job():
    genre_scan_job.reset()
    yield
    genre_scan_job.reset()


@pytest.fixture
def patched_gm(genres_xml, monkeypatch):
    gm = GenresManager(str(genres_xml))
    gm.associate_pattern("sf", "Фантастика")
    monkeypatch.setattr("fb2parser_web.fb2parser_bridge.get_genres_manager", lambda: GenresManager(str(genres_xml)))
    return gm


class TestGenreScanResultsSuggestsRootGenre:
    def test_recognized_family_is_suggested(self, rf, admin_user, patched_gm):
        genre_scan_job.update(
            done=True, running=False,
            results={"sf_action, sf_space": ["a.fb2", "b.fb2"], "totally_unknown": ["c.fb2"]},
            errors=[],
        )
        request = rf.get("/fb2parser/genre-scan/results/")
        request.user = admin_user
        response = genre_scan_results(request)
        content = response.content.decode("utf-8")
        assert 'data-suggested="Фантастика"' in content
        assert "totally_unknown" in content and 'data-suggested' not in content.split("totally_unknown")[1][:200]


class TestGenreScanAssignLearnsAssociations:
    def test_successful_assign_persists_exact_association(self, tmp_path, rf, admin_user, patched_gm, monkeypatch):
        folder = tmp_path / "books"
        folder.mkdir()
        fb2 = folder / "1.fb2"
        fb2.write_text(
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>"
            "<FictionBook><description><title-info>"
            "<genre>new_family_code</genre>"
            "<author><first-name>Т</first-name><last-name>А</last-name></author>"
            "<book-title>Книга</book-title>"
            "</title-info></description>"
            "<body><section><p>Текст</p></section></body></FictionBook>",
            encoding="utf-8",
        )
        genre_scan_job.update(
            done=True, running=False,
            results={"new_family_code": [str(fb2)]},
            errors=[],
        )

        request = rf.post(
            "/fb2parser/genre-scan/assign/",
            data=json.dumps({"mappings": {"new_family_code": "Детектив"}}),
            content_type="application/json",
        )
        request.user = admin_user
        response = genre_scan_assign(request)
        data = json.loads(response.content)
        assert data["results"]["new_family_code"]["success"] == 1

        # Тот же код теперь должен разрешаться автоматически — без
        # какого-либо правила по паттерну, чисто по точной ассоциации.
        gm2 = GenresManager(str(patched_gm.xml_path))
        assert gm2.resolve_code("new_family_code") == ("Детектив", True)
