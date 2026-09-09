"""Тесты middleware SOPDS.

Проверяет:
- SOPDSLocaleMiddleware: установка локали из настроек

FetchFromCacheMiddleware больше не существует в opds_catalog.middleware —
кэширование теперь идёт через стандартный django.middleware.cache
(см. MIDDLEWARE в sopds/settings/base.py), отдельного класса-обёртки нет.
"""

import pytest
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest

pytestmark = pytest.mark.django_db


def _request_with_session() -> HttpRequest:
    """HttpRequest с рабочим request.session — SOPDSLocaleMiddleware читает
    его напрямую (request.session.get('_language')), а бывает в цепочке
    ПОСЛЕ django.contrib.sessions.middleware.SessionMiddleware в реальном
    запросе (см. MIDDLEWARE), поэтому в проде session всегда есть."""
    request = HttpRequest()
    SessionMiddleware(lambda r: None).process_request(request)
    return request


class TestSOPDSLocaleMiddleware:
    """Тесты SOPDSLocaleMiddleware."""

    def test_locale_set_on_request(self, override_config) -> None:
        """Проверяет, что middleware устанавливает LANG и LANGUAGE_CODE из config.json."""
        from opds_catalog.middleware import SOPDSLocaleMiddleware

        request = _request_with_session()
        middleware = SOPDSLocaleMiddleware(lambda r: None)
        with override_config(SOPDS_LANGUAGE="ru"):
            middleware.process_request(request)

        assert request.LANG == "ru"
        assert request.LANGUAGE_CODE == "ru"

    def test_locale_activates_translation(self) -> None:
        """Проверяет, что middleware активирует перевод."""
        from django.utils import translation

        from opds_catalog.middleware import SOPDSLocaleMiddleware

        request = _request_with_session()
        middleware = SOPDSLocaleMiddleware(lambda r: None)
        middleware.process_request(request)

        assert translation.get_language() is not None
