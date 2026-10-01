"""Профиль: `profile, _ = get_or_create(...)` затенял gettext `_` флагом
created, и первое же сообщение об ошибке (`_("Current password is
incorrect.")`) падало TypeError — 500 вместо формы с ошибкой."""
import pytest
from django.contrib.auth.models import User
from django.urls import reverse

_PW = "Kx7#vQ2!mLp9"


@pytest.fixture
def user_client(client, db):
    client.force_login(User.objects.create_user("reader", password=_PW))
    return client


@pytest.mark.parametrize("data", [
    {"action": "password", "old_password": "wrong", "new_password": "Nn8#new!pass", "confirm_password": "Nn8#new!pass"},
    {"action": "password", "old_password": _PW, "new_password": "Nn8#new!pass", "confirm_password": "other"},
    {"action": "delete", "confirm_password": "wrong"},
])
def test_profile_form_errors_render(user_client, data):
    response = user_client.post(reverse("web:profile"), data)
    assert response.status_code == 200
    assert User.objects.filter(username="reader").exists()
