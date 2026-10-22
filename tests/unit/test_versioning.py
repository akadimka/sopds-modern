"""Версии vX.YY.ZZZ (CLAUDE.md, «Версии и выпуски»): единственный источник —
pyproject.toml, показ с ведущими нулями; номер выпуска — по самому
значимому изменению с прошлого тега (несовместимое → X, feat → YY,
fix/security/perf/data → ZZZ, остальное номер не меняет)."""
import importlib.util
import shutil
from pathlib import Path

import pytest
from django.conf import settings

from sopds.version import display, display_version, parse, raw_version

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("version_script", ROOT / "scripts" / "version.py")
vs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vs)


@pytest.mark.parametrize("raw, shown", [("2.27.1", "2.27.001"), ("0.1.0", "0.01.000"), ("v10.5.120", "10.05.120"),
                                        ("1.0.0RC1", "1.0.0RC1")])
def test_display_pads_and_keeps_unknown(raw, shown):
    assert display(raw) == shown


def test_parse_accepts_padded_and_tag_forms():
    assert parse("2.27.1") == parse("2.27.001") == parse("v2.27.001") == (2, 27, 1)
    with pytest.raises(ValueError):
        parse("1.0.0RC1")


def test_single_source_is_pyproject():
    assert raw_version() == vs.re.search(r'(?m)^version = "([^"]+)"',
                                         (ROOT / "pyproject.toml").read_text(encoding="utf-8")).group(1)
    assert settings.VERSION == display_version()
    assert "\nversion = \"%s\"" % raw_version() in (ROOT / "uv.lock").read_text(encoding="utf-8")   # не разошлись


@pytest.mark.parametrize("subject, body, level", [
    ("feat: online now in the header", "", vs.MINOR),
    ("Feat: old style capitalised", "", vs.MINOR),
    ("feat(web): scoped feature", "", vs.MINOR),
    ("fix: align buttons", "", vs.PATCH),
    ("security: harden accounts", "", vs.PATCH),
    ("data: genre associations", "", vs.PATCH),
    ("feat!: memcached is required", "", vs.MAJOR),
    ("fix(deploy)!: new service unit", "", vs.MAJOR),
    ("chore: rework layout", "BREAKING: run `systemctl daemon-reload` on the server", vs.MAJOR),
    ("docs: plan", "", None),
    ("test: more cases", "", None),
    ("chore: cleanup", "", None),
    ("Текущее состояние master", "", None),
])
def test_classify(subject, body, level):
    assert vs.classify(subject, body) == level


def test_release_takes_the_strongest_change_and_resets_lower_digits():
    v = (2, 27, 1)
    assert vs.bump(v, vs.strongest([vs.PATCH, None, vs.PATCH])) == (2, 27, 2)
    assert vs.bump(v, vs.strongest([vs.PATCH, vs.MINOR, vs.MINOR])) == (2, 28, 0)   # три feat — одна версия
    assert vs.bump(v, vs.strongest([vs.MINOR, vs.MAJOR])) == (3, 0, 0)
    assert vs.bump(v, vs.strongest([None])) == v


def test_changelog_section_groups_by_level():
    commits = [("a", "feat: online now", "", vs.MINOR), ("b", "fix: buttons", "", vs.PATCH),
               ("c", "docs: plan", "", None), ("d", "feat!: memcached", "", vs.MAJOR)]
    text = vs.changelog_section((3, 0, 0), commits, "2026-10-22")
    assert text.startswith("## [v3.00.000] — 2026-10-22")
    assert "### ⚠ Требует действий на сервере\n\n- memcached" in text
    assert "### Добавлено\n\n- online now" in text and "### Исправлено\n\n- buttons" in text
    assert "plan" not in text


def test_release_writes_pyproject_and_lock(tmp_path, monkeypatch):
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy(ROOT / name, tmp_path / name)
    monkeypatch.setattr(vs, "ROOT", tmp_path)
    vs._write_pyproject((3, 4, 5))
    assert '\nversion = "3.4.5"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    lock = (tmp_path / "uv.lock").read_text(encoding="utf-8")
    assert 'name = "sopds-modern"\nversion = "3.4.5"' in lock


@pytest.mark.django_db
def test_footer_shows_padded_version(client, django_user_model, settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    client.force_login(django_user_model.objects.create_user("reader"))
    html = client.get("/web/").content.decode("utf-8")
    assert f"SOPDS-MODERN v{display_version()}" in html and "v." + display_version() not in html
