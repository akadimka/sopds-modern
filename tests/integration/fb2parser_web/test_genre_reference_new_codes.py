"""Справочник кодов в Менеджере жанров: отметки «новый» (впервые найден
сканом жанров, ещё не разобран) и «без жанра», фильтры, ссылка из статуса
скана — чтобы новые коды не искать по всему справочнику."""
import json

import pytest
from django.contrib.auth.models import User
from django.template.loader import render_to_string
from django.urls import reverse

import fb2parser_web.fb2parser_bridge as bridge
import fb2parser_web.views as views
from fb2parser_core.genres_manager import GenresManager

pytestmark = pytest.mark.django_db

_REFERENCE = [
    {"model": "opds_catalog.genre", "pk": 1, "fields": {"genre": "sf_social", "section": "Фантастика",
                                                       "subsection": "Социальная фантастика"}},
    {"model": "opds_catalog.genre", "pk": 2, "fields": {"genre": "humor_prose", "section": "Юмор",
                                                       "subsection": "Юмористическая проза"}},
]


@pytest.fixture
def env(tmp_path, monkeypatch, settings, client):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    ref = tmp_path / "mygenres.json"
    ref.write_text(json.dumps(_REFERENCE, ensure_ascii=False), encoding="utf-8")
    xml = tmp_path / "genres.xml"

    def make_gm():
        gm = GenresManager(str(xml), reference_path=str(ref))
        gm.load()
        return gm

    gm = make_gm()
    gm.add_node("Фантастика")
    gm.set_section_mapping("Фантастика", "Фантастика")
    monkeypatch.setattr(bridge, "get_genres_manager", make_gm)
    monkeypatch.setattr(views, "_new_codes_path", lambda: str(tmp_path / "new_codes.json"))
    client.force_login(User.objects.create_user("staff", password="Kx7#vQ2!mLp9", is_staff=True))
    return client, make_gm


def _rows(page):
    import re
    return re.findall(r'<tr data-code="([^"]+)" data-new="(\d)" data-nogenre="(\d)"', page)


def test_scan_registers_new_codes_and_reference_marks_them_first(env):
    client, make_gm = env
    gm = make_gm()
    assert gm.register_discovered_codes({"sf_social", "popadancy", "litrpg"}) == 2
    views._new_codes_add(gm.last_discovered)

    page = client.get(reverse("fb2parser:genres")).content.decode("utf-8")
    rows = _rows(page)
    # новые — первыми; «без жанра» — у новых и у humor_prose (раздел «Юмор» не сопоставлен)
    assert [c for c, new, _ng in rows if new == "1"] == ["litrpg", "popadancy"]
    assert rows[:2] == [("litrpg", "1", "1"), ("popadancy", "1", "1")]
    assert ("humor_prose", "0", "1") in rows and ("sf_social", "0", "0") in rows
    assert 'data-filter="new"' in page and "(2)</button>" in page


def test_decisions_remove_the_new_mark(env):
    client, make_gm = env
    views._new_codes_add(["popadancy", "litrpg", "compilation_x"])

    client.post(reverse("fb2parser:genres_code_assign_set"), {"code": "popadancy", "genre": "Фантастика"})
    client.post(reverse("fb2parser:genres_excluded_code_add"), {"code": "compilation_x"})
    assert views._new_codes_load() == {"litrpg"}

    client.post(reverse("fb2parser:genres_new_codes_clear"))
    assert views._new_codes_load() == set()


def test_scan_status_links_new_codes_to_the_reference():
    html = render_to_string("fb2parser/main_scan_statusbar.html",
                            {"state": {"done": True, "processed": 5, "results": {}, "errors": [],
                                       "discovered_codes_count": 3}})
    assert reverse("fb2parser:genres") + "?codes=new" in html and "<strong>3</strong>" in html
    html0 = render_to_string("fb2parser/main_scan_statusbar.html",
                             {"state": {"done": True, "processed": 5, "results": {}, "errors": [],
                                        "discovered_codes_count": 0}})
    assert "?codes=new" not in html0
