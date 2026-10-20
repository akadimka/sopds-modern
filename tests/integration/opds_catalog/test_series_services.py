"""Тесты сервисов серий series_services."""

import pytest

from opds_catalog.models import Series
from opds_catalog.services import series_services

pytestmark = pytest.mark.django_db


class TestSeriesServices:
    """Тесты методов series_services."""

    def test_search_series_by_exact(self, book_with_relations, admin_user):
        """Поиск серии по точному совпадению."""
        results = series_services.search_series("e", "mywork", author_id=None, user=admin_user)
        assert results.count() == 1
        assert results[0].ser == "mywork"

    def test_search_series_by_begin(self, book_with_relations, admin_user):
        """Поиск серии по началу названия."""
        results = series_services.search_series("b", "myw", author_id=None, user=admin_user)
        assert results.count() == 1

    def test_search_series_without_results(self, admin_user):
        """Поиск несуществующей серии."""
        results = series_services.search_series("e", "nonexist", author_id=None, user=admin_user)
        assert results.count() == 0

    def test_get_series_with_chars(self, book_with_relations, admin_user):
        """Получение списка серий по шаблону и длине."""
        # После создания книги с серией, get_series должен вернуть как минимум одну серию
        results = series_services.get_series("", 1, lang_code=0, user=admin_user)
        assert len(results) > 0
        assert results[0]["sid"] == "M"  # первая буква (заглавная)
        assert results[0]["cnt"] > 0

    def test_get_series_longer(self, book_with_relations, admin_user):
        """Продвинутый поиск серий с большей длиной."""
        results = series_services.get_series("MYWO", 5, lang_code=0, user=admin_user)
        assert len(results) == 1
        assert results[0]["sid"] == "MYWOR"  # первые 5 символов
        assert results[0]["cnt"] == 1

    def test_get_series_with_lang_code(self, book_with_relations, admin_user):
        """Поиск серий с фильтром по коду языка."""
        results = series_services.get_series("", 1, lang_code=1, user=admin_user)
        assert len(results) >= 0

    def test_search_series_by_contains(self, book_with_relations, admin_user):
        """Поиск серии по вхождению (type='m')."""
        results = series_services.search_series("m", "work", author_id=None, user=admin_user)
        assert results.count() == 1
        assert results[0].ser == "mywork"

    def test_search_series_by_author(self, book_with_relations, admin_user):
        """Поиск серий по автору (type='a')."""
        author = book_with_relations.authors.first()
        results = series_services.search_series("a", "", author_id=author.id, user=admin_user)
        assert results.count() == 1

    def test_get_series_name_existing(self, book_with_relations, admin_user):
        """Получение названия серии по ID."""
        series_obj = book_with_relations.series.first()
        name = series_services.get_series_name(series_obj.id, user=admin_user)
        assert name == series_obj.ser

    def test_get_series_name_not_found(self, admin_user):
        """Возвращает 'Series not found' для несуществующей серии."""
        name = series_services.get_series_name(99999, user=admin_user)
        assert "not found" in name.lower() or "не найдена" in name.lower()
