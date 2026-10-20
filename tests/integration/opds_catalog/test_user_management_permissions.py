"""Управление пользователями (/web/settings/users/). Роли две: «Пользователь»
и «Суперпользователь» (is_superuser + is_staff); «admin» — только имя
учётной записи. Управляет пользователями только суперпользователь; снять
роль с себя (и удалить себя) нельзя — иначе можно остаться без
суперпользователя. Новые пароли проходят AUTH_PASSWORD_VALIDATORS.
"""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse

_STRONG = "Kx7#vQ2!mLp9"


@pytest.fixture
def plain_static(settings):
    # Ответ с ошибкой рендерит полную страницу с {% static %}.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser("root", password=_STRONG)


@pytest.fixture
def super_client(client, superuser):
    client.force_login(superuser)
    return client


@pytest.mark.parametrize("flags", [{}, {"is_staff": True}])
def test_only_superuser_manages_users(client, superuser, flags):
    """Обычный пользователь и бывший «Admin» (is_staff без суперправ, роли
    такой больше нет) — без доступа: пароль суперпользователя не сбросить,
    его не удалить."""
    client.force_login(User.objects.create_user("someone", password=_STRONG, **flags))
    assert client.get(reverse("web:users_list")).status_code == 302
    client.post(reverse("web:user_edit", args=[superuser.pk]), {"role": "user", "password": "Other#Pass123"})
    client.post(reverse("web:user_delete", args=[superuser.pk]))
    superuser.refresh_from_db()
    assert superuser.check_password(_STRONG) and superuser.is_superuser


def test_role_superuser_sets_both_flags(super_client):
    super_client.post(reverse("web:user_create"), {"username": "boss", "password": _STRONG, "role": "superuser"})
    super_client.post(reverse("web:user_create"), {"username": "reader", "password": _STRONG, "role": "user"})
    boss, reader = User.objects.get(username="boss"), User.objects.get(username="reader")
    assert (boss.is_superuser, boss.is_staff) == (True, True)
    assert (reader.is_superuser, reader.is_staff) == (False, False)
    super_client.post(reverse("web:user_edit", args=[boss.pk]), {"role": "user"})
    boss.refresh_from_db()
    assert (boss.is_superuser, boss.is_staff) == (False, False)


def test_superuser_manages_other_superuser(super_client):
    other = User.objects.create_superuser("root2", password=_STRONG)
    response = super_client.post(reverse("web:user_edit", args=[other.pk]),
                                 {"role": "superuser", "password": "Other#Pass123"})
    assert response.status_code == 302
    other.refresh_from_db()
    assert other.check_password("Other#Pass123") and other.is_superuser


def test_cannot_remove_own_superuser_role_or_delete_self(super_client, superuser, plain_static):
    response = super_client.post(reverse("web:user_edit", args=[superuser.pk]), {"role": "user"})
    assert response.status_code == 422
    super_client.post(reverse("web:user_delete", args=[superuser.pk]))
    superuser.refresh_from_db()
    assert superuser.is_superuser and superuser.is_staff


def test_users_page_names_the_role_superuser(super_client):
    page = super_client.get(reverse("web:users_list")).content.decode("utf-8")
    assert 'value="superuser"' in page and ">Admin<" not in page and 'value="admin"' not in page


def test_weak_password_rejected_on_create(super_client, plain_static):
    response = super_client.post(reverse("web:user_create"),
                                 {"username": "newbie", "password": "123", "role": "user"})
    assert response.status_code == 422
    assert not User.objects.filter(username="newbie").exists()


def test_weak_password_rejected_on_edit(super_client, plain_static):
    user = User.objects.create_user("reader", password=_STRONG)
    response = super_client.post(reverse("web:user_edit", args=[user.pk]),
                                 {"role": "user", "password": "123"})
    assert response.status_code == 422
    user.refresh_from_db()
    assert user.check_password(_STRONG)


def test_edit_missing_user_is_404(super_client):
    response = super_client.post(reverse("web:user_edit", args=[999999]), {"role": "user"})
    assert response.status_code == 404
