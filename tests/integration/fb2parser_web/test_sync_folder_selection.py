"""Выбор папок для синхронизации — галочками в дереве на главной, без
временного кеша «папкам назначили жанр в этой сессии» (терялся через 3 часа
и при перезапуске — уже размеченную папку синхронизировать было нельзя).

Окно синхронизации показывает отмеченные папки (вложенные в отмеченные —
одной строкой с родителем) и допускает только те, у которых все FB2-файлы на
любой глубине размечены жанрами дерева; старт перепроверяет правило."""
import json
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import User
from django.urls import reverse

import fb2parser_web.fb2parser_bridge as bridge
import fb2parser_web.views as views
from fb2parser_core.genres_manager import GenresManager

pytestmark = pytest.mark.django_db

_FB2 = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0">
<description><title-info>{genres}<book-title>Книга</book-title></title-info></description>
<body><section><p>Текст.</p></section></body>
</FictionBook>
"""


def _book(path, *genres):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_FB2.format(genres="".join(f"<genre>{g}</genre>" for g in genres)), encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch, settings, client):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    gm = GenresManager(str(tmp_path / "genres.xml"), reference_path=str(tmp_path / "none.json"))
    gm.load()
    gm.add_node("Фантастика")
    gm.add_node("Космическая фантастика", "Фантастика")
    monkeypatch.setattr(bridge, "get_genres_manager", lambda: gm)
    started = []
    monkeypatch.setattr(views, "threading",
                        SimpleNamespace(Thread=lambda target, daemon: SimpleNamespace(start=lambda: started.append(1))))
    views.sync_job.reset()
    client.force_login(User.objects.create_user("staff", password="Kx7#vQ2!mLp9", is_staff=True))

    scan = tmp_path / "in"
    _book(scan / "Готово" / "1.fb2", "Фантастика")
    _book(scan / "Готово" / "Серия" / "2.fb2", "Космическая фантастика")
    _book(scan / "Сырые" / "1.fb2", "Фантастика")
    _book(scan / "Сырые" / "Глубоко" / "2.fb2", "sf_social")
    yield client, scan, started
    views.sync_job.reset()


def _open(client, scan, *paths):
    return client.post(reverse("fb2parser:sync"),
                       {"scan_path": str(scan), "paths": json.dumps([str(p) for p in paths])}).content.decode()


def test_window_lists_checked_folders_and_blocks_unmarked_ones(env):
    client, scan, _ = env
    # «Готово/Серия» отмечена вместе с родителем (каскад галочек в дереве) — одна строка
    page = _open(client, scan, scan / "Готово", scan / "Готово" / "Серия", scan / "Сырые")

    assert page.count('data-eligible=') == 2
    assert f'value="{scan / "Готово"}" checked' in page
    assert f'value="{scan / "Сырые"}" disabled' in page
    assert "1 of 2" in page or "1 из 2" in page          # сколько файлов без жанра дерева
    assert "sf_social" not in page and "2.fb2" in page   # какие именно


def test_nothing_checked_explains_where_to_check(env):
    client, scan, _ = env
    page = _open(client, scan)
    assert 'data-eligible=' not in page and "sync-folder-cb" not in page


def test_start_rechecks_the_rule_on_the_server(env):
    client, scan, started = env
    url = reverse("fb2parser:sync_start")

    resp = client.post(url, {"scan_path": str(scan), "paths": json.dumps([str(scan / "Сырые")])})
    assert started == [] and str(scan / "Сырые") in resp.content.decode()

    outside = scan.parent / "чужая"
    _book(outside / "1.fb2", "Фантастика")
    client.post(url, {"scan_path": str(scan), "paths": json.dumps([str(outside)])})
    assert started == []

    client.post(url, {"scan_path": str(scan), "paths": json.dumps([str(scan / "Готово")])})
    assert started == [1]
    assert views.sync_job.get()["allowed_folders"] == {str(scan / "Готово")}
