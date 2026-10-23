"""Связей с GitHub в коде нет: в подвале сайта — подпись вместо ссылки,
исходящие запросы (Wikidata, Telegram) подписаны как `sopds-modern/<версия>`
без адреса хостинга кода (по просьбе владельца репозитория, 2026-10-23)."""
import io
import json
from types import SimpleNamespace

import pytest
from django.test import Client
from django.urls import reverse

from fb2parser_core import gender_lookup
from fb2parser_core.user_agent import USER_AGENT


def test_user_agent_has_no_code_hosting():
    assert USER_AGENT.startswith("sopds-modern") and "github" not in USER_AGENT.lower()


def test_wikidata_requests_are_signed_without_github(monkeypatch, tmp_path):
    agents = []

    class Response(io.BytesIO):
        status = 200

    def fake_urlopen(req, timeout=None):
        agents.append(req.get_header("User-agent"))
        return Response(json.dumps({"search": [], "query": {"search": []}}).encode())

    monkeypatch.setattr(gender_lookup.urllib.request, "urlopen", fake_urlopen)
    settings = SimpleNamespace(config_path=str(tmp_path / "config.json"),      # кэш — во временной папке
                               get_writer_occupation_qids=lambda: [])
    service = gender_lookup.GenderLookupService(settings=settings)
    service._wikidata_lookup("Иван Петров")
    assert agents and all(a.startswith(USER_AGENT) and "github" not in a.lower() for a in agents)


@pytest.mark.django_db
def test_footer_has_signature_instead_of_github(django_user_model, settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    client = Client()
    client.force_login(django_user_model.objects.create_user("reader"))
    html = client.get(reverse("web:main")).content.decode("utf-8")
    footer = html.split('class="sopds-footer"', 1)[1].split("</div>", 1)[0]
    assert "by aKaDiMkA in 2026 (c)" in footer and "github" not in footer.lower()
    assert "github.com" not in html.lower()
