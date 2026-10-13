"""Тесты LoginView: валидация next_url, защита от open redirect."""

import pytest
from django.urls import reverse


@pytest.mark.django_db
class TestLoginViewRedirect:
    """Проверка редиректов LoginView при успешной аутентификации."""

    def test_login_redirect_success(self, client, django_user) -> None:
        """POST с валидным next -> редирект на этот URL."""
        response = client.post(
            reverse("web:login") + "?next=/web/authors",
            {"username": "test", "password": "secret"},
        )
        assert response.status_code == 302
        assert response["Location"] == "/web/authors"

    def test_login_redirect_fallback(self, client, django_user) -> None:
        """POST с next=http://evil.com -> редирект на web:main (защита от open redirect)."""
        response = client.post(
            reverse("web:login") + "?next=http://evil.com",
            {"username": "test", "password": "secret"},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("web:main")

    def test_login_redirect_empty_next(self, client, django_user) -> None:
        """POST без next -> редирект на web:main по умолчанию."""
        response = client.post(
            reverse("web:login"),
            {"username": "test", "password": "secret"},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("web:main")

    def test_login_redirect_relative(self, client, django_user) -> None:
        """POST с next=/web/books -> редирект на /web/books (относительный разрешён)."""
        response = client.post(
            reverse("web:login") + "?next=/web/books",
            {"username": "test", "password": "secret"},
        )
        assert response.status_code == 302
        assert response["Location"] == "/web/books"

    def test_login_redirect_protocol_relative(self, client, django_user) -> None:
        """POST с next=//evil.com -> редирект на web:main (protocol-relative URL bypass)."""
        response = client.post(
            reverse("web:login") + "?next=//evil.com",
            {"username": "test", "password": "secret"},
        )
        assert response.status_code == 302
        assert response["Location"] == reverse("web:main")

    def test_login_redirect_same_host_full_url(self, client, django_user) -> None:
        """POST с next=http://testserver/web/ -> редирект разрешён (свой хост)."""
        response = client.post(
            reverse("web:login") + "?next=http://testserver/web/",
            {"username": "test", "password": "secret"},
        )
        assert response.status_code == 302
        assert response["Location"] == "http://testserver/web/"


@pytest.mark.django_db
class TestLoginViewCredentialEnumeration:
    """Неактивный аккаунт не должен отличаться в ответе от неверных кред —
    иначе сообщение подтверждало бы, что логин и пароль верны (перечисление
    валидных учёток)."""

    _WRONG = "User does not exist or the password is incorrect!"

    def test_inactive_account_shows_generic_message(self, django_user_model) -> None:
        from django.test import Client
        django_user_model.objects.create_user(username="ghost", password="secret", is_active=False)
        resp = Client().post(reverse("web:login"), {"username": "ghost", "password": "secret"})
        body = resp.content.decode()
        assert self._WRONG in body
        assert "not active" not in body

    def test_wrong_password_shows_same_message(self, django_user) -> None:
        from django.test import Client
        resp = Client().post(reverse("web:login"), {"username": "test", "password": "nope"})
        body = resp.content.decode()
        assert self._WRONG in body
        assert "not active" not in body

    def test_unknown_user_shows_same_message(self) -> None:
        from django.test import Client
        resp = Client().post(reverse("web:login"), {"username": "nobody", "password": "x"})
        assert self._WRONG in resp.content.decode()
