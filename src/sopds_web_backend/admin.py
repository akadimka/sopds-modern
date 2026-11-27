"""Пользователи в /admin/ — только просмотр и мелкие правки.

Пароль, роль и создание пользователей — только на сайте («Настройки →
Пользователи», профиль): там смена пароля отзывает пароли для читалок и
другие устройства (`views._after_password_change`), пароль, заданный за
другого, — временный, роль ставит оба флага сразу (`is_superuser` +
`is_staff`), а критичные изменения подтверждаются паролем. Стандартная
форма Django всё это обходит (проверка безопасности 2026-11-27).
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.urls import reverse


class SiteManagedUserAdmin(UserAdmin):
    readonly_fields = ("password", "is_superuser", "is_staff", "groups", "user_permissions",
                       "last_login", "date_joined")

    def has_add_permission(self, request):
        return False

    def user_change_password(self, request, id, form_url=""):
        return redirect(reverse("web:users_list"))


if admin.site.is_registered(User):
    admin.site.unregister(User)
admin.site.register(User, SiteManagedUserAdmin)
