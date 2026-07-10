"""Сервисы для работы с книжной полкой пользователя."""

from django.contrib.auth.models import User

from opds_catalog.models import bookshelf


def get_bookshelf_books_count(user: User) -> int:
    """Подсчет числа книг на книжной полке пользователя.

    :param user: Пользователь, для котрого требуется подсчитать число книг.
    :type user: User

    :returns: Число книг на книжной полке.
    :rtype: int
    """
    return bookshelf.objects.filter(user=user).count()


