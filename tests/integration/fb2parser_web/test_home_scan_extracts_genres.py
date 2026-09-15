"""Регрессия для `main_scan_start`/`scan_results` (главная страница Home) —
docs/quality-roadmap.md, баг №79.

Реальный случай: большая кнопка "▶ Scan" на Home раньше всегда запускала
`opdsScanner.scan_all()` (полный OPDS-каталожный скан) вне зависимости от
того, какая папка отмечена в дереве — а панель результатов показывала
агрегат по ВСЕЙ БД каталога, так что выбор папки вообще ни на что не
влиял (баги №77/№78). OPDS-каталогизация для читалок остаётся доступна
через отдельную кнопку "Scan" в самом SOPDS Modern
(`sopds_web_backend.sopds_scan_start`, свой собственный job/`scan_all()`,
не затронута этим фиксом) — поэтому кнопку на Home полностью
переключили на извлечение жанров из выбранных папок
(`genre_scan_service.scan_fb2_genres`, тот же инструмент, что и Actions
→ Genre Combinations), а НЕ на OPDS-скан.
"""
from pathlib import Path

import pytest
from django.test import RequestFactory

from fb2parser_web.views import dashboard, genre_scan_job, main_scan_start, scan_results

_FB2_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<FictionBook>
<description>
<title-info>
<genre>{genre}</genre>
<author><first-name>Тест</first-name></author>
<book-title>Книга</book-title>
</title-info>
</description>
<body><section><p>Текст</p></section></body>
</FictionBook>
"""


def _write_fb2(path: Path, genre: str):
    path.write_text(_FB2_TEMPLATE.format(genre=genre), encoding="utf-8")


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture(autouse=True)
def _reset_genre_scan_job():
    genre_scan_job.reset()
    yield
    genre_scan_job.reset()


class TestHomeScanButtonExtractsGenres:
    def test_main_scan_start_runs_genre_extraction_not_opds_scan(self, tmp_path, rf, admin_user, monkeypatch):
        folder = tmp_path / "books"
        folder.mkdir()
        _write_fb2(folder / "1.fb2", "детектив")
        _write_fb2(folder / "2.fb2", "фантастика")

        called = {"opds": False}

        def _fail_if_called(*a, **kw):
            called["opds"] = True

        monkeypatch.setattr("fb2parser_web.views._run_scan_thread", _fail_if_called)

        request = rf.post("/fb2parser/main-scan/start/", {"root": str(folder)})
        request.user = admin_user
        response = main_scan_start(request)
        assert response.status_code == 200

        # Фоновый поток — ждём его синхронно через прямой вызов вместо
        # threading, чтобы не гоняться за таймингом в тесте: раз job уже
        # запущен (try_start), дожидаемся завершения опросом.
        import time
        for _ in range(50):
            if not genre_scan_job.get()["running"]:
                break
            time.sleep(0.05)

        assert called["opds"] is False
        state = genre_scan_job.get()
        assert state["done"] is True
        assert set(state["results"].keys()) == {"детектив", "фантастика"}

    def test_scan_results_reflects_genre_scan_job_not_opds_catalog(self, rf, admin_user):
        genre_scan_job.update(
            done=True, running=False,
            results={"детектив": ["a.fb2", "b.fb2"]}, errors=["bad.fb2: oops"],
            processed=2, total=2,
        )
        request = rf.get("/fb2parser/scan-results/")
        request.user = admin_user
        response = scan_results(request)
        content = response.content.decode("utf-8")
        assert "детектив" in content
        assert "oops" in content

    def test_dashboard_state_comes_from_genre_scan_job(self, rf, admin_user, monkeypatch):
        # Полный рендер dashboard.html требует собранных staticfiles (не
        # относится к сути фикса) — проверяем напрямую контекст, который
        # dashboard() передаёт в render(), без реального шаблона.
        genre_scan_job.update(done=True, running=False, processed=3, total=3,
                              results={"фантастика": ["x.fb2"]}, errors=[])
        captured = {}

        def _fake_render(request, template_name, context=None):
            captured["context"] = context
            from django.http import HttpResponse
            return HttpResponse("")

        monkeypatch.setattr("fb2parser_web.views.render", _fake_render)

        request = rf.get("/fb2parser/")
        request.user = admin_user
        request.session = {}
        dashboard(request)

        assert captured["context"]["state"]["processed"] == 3
        assert captured["context"]["state"]["results"] == {"фантастика": ["x.fb2"]}
