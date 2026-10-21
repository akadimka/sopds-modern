"""Имена пользователей без учёта регистра: `Admin` при входе — это `admin`.

Имя хранится так, как его ввели; при входе (сайт — через него и /admin/, читалки OPDS) и
при создании пользователя оно сравнивается без учёта регистра. Сравнение —
в Python (`casefold`): в SQLite `iexact`/`lower()` не различают регистр
только у латиницы, а имена бывают кириллическими («Анна» / «анна»).

Пользователи, заведённые раньше с именами, различающимися только регистром
(`anna` и `Anna`), продолжают входить, но только точным написанием, —
страница пользователей их показывает (`case_conflicts`)."""
from collections import defaultdict
from typing import List

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.core.exceptions import PermissionDenied


def _key(username: str) -> str:
    return username.strip().casefold()


def find_user(username: str, **filters):
    """Пользователь по имени: точное совпадение, иначе единственное — без учёта
    регистра. Неоднозначно (`anna` и `Anna` при вводе `ANNA`) — None."""
    if not username:
        return None
    users = get_user_model().objects.filter(**filters)
    user = users.filter(username=username).first()
    if user is not None:
        return user
    key = _key(username)
    pks = [pk for pk, name in users.values_list("pk", "username") if _key(name) == key]
    return users.get(pk=pks[0]) if len(pks) == 1 else None


def username_taken(username: str) -> bool:
    key = _key(username)
    return any(_key(name) == key for name in get_user_model().objects.values_list("username", flat=True))


def case_conflicts() -> List[List[str]]:
    """Группы имён, различающихся только регистром (заведены до этой проверки)."""
    groups = defaultdict(list)
    for name in get_user_model().objects.order_by("username").values_list("username", flat=True):
        groups[_key(name)].append(name)
    return [names for names in groups.values() if len(names) > 1]


class CaseInsensitiveModelBackend(ModelBackend):
    """ModelBackend, который находит пользователя без учёта регистра имени.

    Неудача — `PermissionDenied`: цепочка бэкендов прерывается, и стоящий за
    ним ModelBackend (он там для старых сессий) не проверяет пароль второй
    раз. Django при этом, как обычно, шлёт `user_login_failed` (axes)."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None:
            username = kwargs.get(get_user_model().USERNAME_FIELD)
        if username is None or password is None:
            return None
        user = find_user(username)
        if user is None:
            get_user_model()().set_password(password)   # время ответа — как при неверном пароле
        elif user.check_password(password) and self.user_can_authenticate(user):
            return user
        raise PermissionDenied
