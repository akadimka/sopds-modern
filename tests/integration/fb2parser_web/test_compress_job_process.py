"""Кнопка «Сжать» — отдельный процесс с низким приоритетом (баг №159)."""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse

import fb2parser_web.views as views

pytestmark = pytest.mark.django_db


@pytest.fixture
def client_staff(client):
    client.force_login(User.objects.create_superuser("staff", password="Kx7#vQ2!mLp9"))
    views.compress_job.finish()
    views.compress_job.reset()
    yield client
    views.compress_job.finish()
    views.compress_job.reset()


def test_status_clears_a_dead_compress_process(client_staff, monkeypatch):
    from fb2parser_core import sync_lock

    assert views.compress_job.try_start(pid=999999, library_path="/lib")
    monkeypatch.setattr(sync_lock, "_pid_alive", lambda pid: False)

    client_staff.get(reverse("fb2parser:compress_status"))

    state = views.compress_job.get()
    assert state["running"] is False and state["error"]
    assert views.compress_job.try_start()


def test_compress_process_runs_at_low_priority():
    cmd = views._low_priority_cmd(["python", "manage.py", "fb2parser_compress"], have_ionice=True)
    assert cmd == ["nice", "-n", "19", "ionice", "-c3", "python", "manage.py", "fb2parser_compress"]
    assert views._low_priority_cmd(["x"], have_ionice=False) == ["nice", "-n", "19", "x"]
