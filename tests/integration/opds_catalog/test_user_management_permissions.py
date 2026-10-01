"""Управление пользователями (/web/settings/users/): Admin (is_staff) не
должен иметь возможности сбросить пароль или удалить суперпользователя —
иначе любой Admin (или XSS от его имени) получает полный доступ к /admin/.
Новые пароли проходят AUTH_PASSWORD_VALIDATORS.
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
def staff_client(client, db):
    staff = User.objects.create_user("staff", password=_STRONG, is_staff=True)
    client.force_login(staff)
    return client


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser("root", password=_STRONG)


def test_staff_cannot_reset_superuser_password(staff_client, superuser):
    response = staff_client.post(reverse("web:user_edit", args=[superuser.pk]),
                                 {"role": "admin", "password": "Other#Pass123"})
    assert response.status_code == 403
    superuser.refresh_from_db()
    assert superuser.check_password(_STRONG)


def test_staff_cannot_delete_superuser(staff_client, superuser):
    response = staff_client.post(reverse("web:user_delete", args=[superuser.pk]))
    assert response.status_code == 403
    assert User.objects.filter(pk=superuser.pk).exists()


def test_superuser_can_manage_superuser(client, superuser):
    other = User.objects.create_superuser("root2", password=_STRONG)
    client.force_login(superuser)
    response = client.post(reverse("web:user_edit", args=[other.pk]),
                           {"role": "admin", "password": "Other#Pass123"})
    assert response.status_code == 302
    other.refresh_from_db()
    assert other.check_password("Other#Pass123")


def test_staff_still_manages_regular_users(staff_client):
    user = User.objects.create_user("reader", password=_STRONG)
    response = staff_client.post(reverse("web:user_edit", args=[user.pk]),
                                 {"role": "user", "password": "Other#Pass123"})
    assert response.status_code == 302
    user.refresh_from_db()
    assert user.check_password("Other#Pass123")


def test_weak_password_rejected_on_create(staff_client, plain_static):
    response = staff_client.post(reverse("web:user_create"),
                                 {"username": "newbie", "password": "123", "role": "user"})
    assert response.status_code == 422
    assert not User.objects.filter(username="newbie").exists()


def test_weak_password_rejected_on_edit(staff_client, plain_static):
    user = User.objects.create_user("reader", password=_STRONG)
    response = staff_client.post(reverse("web:user_edit", args=[user.pk]),
                                 {"role": "user", "password": "123"})
    assert response.status_code == 422
    user.refresh_from_db()
    assert user.check_password(_STRONG)


def test_edit_missing_user_is_404(staff_client):
    response = staff_client.post(reverse("web:user_edit", args=[999999]), {"role": "user"})
    assert response.status_code == 404
