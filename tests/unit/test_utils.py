"""Тесты чистых утилитарных функций (без БД, без ФС) — unit."""

import pytest

from opds_catalog.utils import get_lang_name


class TestOpdsUtils:
    """Тесты чистых утилитарных функций (без БД, без ФС)."""

    def test_get_lang_name(self) -> None:
        """Проверка преобразования кода языка в его наименование"""
        lang = "ru"
        expected_lang_name = "Russian"
        lang_name = get_lang_name(lang)
        assert lang_name == expected_lang_name

    def test_get_lang_name_unknown(self) -> None:
        """Проверка что неизвестный код возвращается как есть"""
        lang_name = get_lang_name("xx")
        assert lang_name == "xx"

    def test_to_int_valid(self) -> None:
        """Проверка to_int с валидным значением"""
        from opds_catalog.utils import to_int

        assert to_int("42") == 42
        assert to_int(42) == 42

    def test_to_int_invalid(self) -> None:
        """Проверка to_int с невалидным значением"""
        from opds_catalog.utils import to_int

        assert to_int("abc") == 0
        assert to_int(None) == 0
        assert to_int([1, 2, 3]) == 0

    def test_to_int_custom_default(self) -> None:
        """Проверка to_int с кастомным default"""
        from opds_catalog.utils import to_int

        assert to_int("abc", -1) == -1
