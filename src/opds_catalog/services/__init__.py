"""Сервисные функции opds_catalog."""

from enum import StrEnum


class SearchType(StrEnum):
    """Типы поиска в OPDS-каталоге."""

    # Общие типы поиска (для книг, авторов, серий)
    BY_SUBSTRING = "m"  # Поиск по подстроке (contains)
    BY_START_WITH = "b"  # Поиск по началу строки (startswith)
    BY_EXACT_MATCH = "e"  # Точное совпадение (exact)

    # Специфичные типы поиска для книг
    BY_AUTHOR = "a"  # Поиск по автору
    BY_SERIES = "s"  # Поиск по серии
    BY_AUTHOR_AND_SERIES = "as"  # Поиск по автору и серии
    BY_GENRE = "g"  # Поиск по жанру
    BY_USER = "u"  # Поиск по пользователю (книжная полка)
    DOUBLES = "d"  # Поиск дубликатов
    BY_ID = "i"  # Поиск по ID книги
    ADDED_ON = "n"  # Добавленные в этот день («Новости» на главной)
