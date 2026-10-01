"""Тексты интерфейса /fb2parser/, которые формирует сам views.py (заголовки
страниц, описания списков настроек, HTML-фрагменты ошибок), раньше были
захардкожены по-русски и показывались по-русски даже в английском UI."""
import pytest
from django.urls import reverse


@pytest.mark.django_db
@pytest.mark.parametrize("lang, title, desc", [
    ("ru-RU", "FB2Parser — Настройки", "Частицы иностранных имён"),
    ("en-US", "FB2Parser — Settings", "Particles of foreign names"),
])
def test_settings_page_follows_interface_language(admin_client, override_config, lang, title, desc):
    with override_config(SOPDS_LANGUAGE=lang):
        html = admin_client.get(reverse("fb2parser:fb2parser_settings")).content.decode()
    assert title in html
    assert desc in html


@pytest.mark.django_db
@pytest.mark.parametrize("lang, text", [("ru-RU", "Укажите путь к CSV-файлу."), ("en-US", "Enter the path to a CSV file.")])
def test_error_fragment_follows_interface_language(admin_client, override_config, lang, text):
    with override_config(SOPDS_LANGUAGE=lang):
        html = admin_client.get(reverse("fb2parser:names_from_csv")).content.decode()
    assert text in html
