"""Smoke: каждая страница /fb2parser/ из меню открывается у администратора.

Страховка для чистки мёртвого кода: удалённый «неиспользуемый» метод,
который на самом деле зовёт какой-нибудь шаблон или view, сразу даст 500.
Только GET-страницы без побочных эффектов.
"""
import pytest
from django.urls import reverse

PAGES = [
    "dashboard", "scan", "statistics", "compress", "normalize", "sync", "genres",
    "fb2parser_settings", "archive", "database", "log", "search", "new_books",
    "series_gaps", "integrity",
]


@pytest.mark.django_db
@pytest.mark.parametrize("name", PAGES)
def test_page_renders(admin_client, name):
    response = admin_client.get(reverse(f"fb2parser:{name}"))
    assert response.status_code == 200


@pytest.mark.django_db
def test_pages_require_staff(client):
    response = client.get(reverse("fb2parser:dashboard"))
    assert response.status_code == 302
