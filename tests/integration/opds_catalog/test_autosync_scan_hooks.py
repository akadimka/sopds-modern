"""Ручной полный скан ведёт себя как плановый: автосинхронизация папки
наблюдения → скан библиотеки → Telegram (opds_catalog/autosync_hooks.py).
Точечный пересмотр подпапки автосинхронизацию не запускает.
"""
from types import SimpleNamespace

import pytest

import opds_catalog.autosync_hooks as hooks


def _result(**kw):
    base = dict(status="done", mode="auto", moved=[{}, {}], pending=3, kept=0, removed=0, would_move=0, error="")
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def calls(monkeypatch):
    order = []
    autosync = (SimpleNamespace(cfg={}), _result())

    def fake_run(logger=None):
        order.append("autosync")
        return autosync

    def fake_notify(value, logger=None):
        assert value is autosync
        order.append("notify")
        return {}

    monkeypatch.setattr(hooks, "run_autosync", fake_run)
    monkeypatch.setattr(hooks, "notify_autosync", fake_notify)
    return order


class _FakeScanner:
    def __init__(self, order, *args, **kwargs):
        self.order = order
        self.books_added = self.bad_books = self.books_deleted = self.books_skipped = 0

    def scan_all(self):
        self.order.append("scan")

    def scan_path(self, path):
        self.order.append("scan_path")


@pytest.mark.django_db
def test_sopds_scan_button_runs_autosync_scan_notify(calls, monkeypatch):
    import opds_catalog.sopdscan as sopdscan
    import sopds_web_backend.views as views

    monkeypatch.setattr(sopdscan, "opdsScanner", lambda *a, **k: _FakeScanner(calls))
    views._run_sopds_scan()

    assert calls == ["autosync", "scan", "notify"]
    state = views.sopds_scan_job.get()
    assert state["done"] and state["autosync"]["moved"] == 2 and state["autosync"]["pending"] == 3


@pytest.mark.django_db
@pytest.mark.parametrize("scoped, expected", [(False, ["autosync", "scan", "notify"]), (True, ["scan_path"])])
def test_fb2parser_scan_runs_autosync_only_for_full_scan(calls, monkeypatch, tmp_path, scoped, expected):
    import fb2parser_web.views as views
    from opds_catalog import opdsdb

    monkeypatch.setattr(views, "_is_scoped_scan", lambda root: scoped)
    monkeypatch.setattr(views, "_TrackingScanner", lambda logger: _FakeScanner(calls))
    monkeypatch.setattr(views, "_count_files", lambda root: 0)
    for name in ("avail_check_prepare_scoped", "books_del_scoped", "cleanup_orphan_entities"):
        monkeypatch.setattr(opdsdb, name, lambda *a, **k: None)
    views.scan_job.try_start(root=str(tmp_path))

    views._run_scan_thread(str(tmp_path))

    assert calls == expected
    assert (views.scan_job.get()["autosync"] is not None) is (not scoped)


def test_summary_texts():
    assert hooks.summary((None, _result(status="off"))) is None
    assert hooks.summary(None)["status"] == "error"
    s = hooks.summary((None, _result(mode="dry_run", would_move=5, moved=[])))
    assert s["mode"] == "dry_run" and s["would_move"] == 5 and s["moved"] == 0


def test_summary_partial_renders_in_russian():
    from django.template.loader import render_to_string
    from django.utils import translation

    with translation.override("ru"):
        html = render_to_string("autosync_scan_summary.html",
                                {"a": {"status": "done", "mode": "auto", "moved": 2, "pending": 3}})
    assert "Автосинхронизация: перемещено 2, ждут решения 3." in html and "Входящие" in html
