"""Миграция «одна роль — Суперпользователь» (sopds_web_backend 0008):
бывший Admin (is_staff без is_superuser) становится суперпользователем,
суперпользователю без is_staff ставится is_staff; обычные — без изменений."""
import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

BEFORE = [("sopds_web_backend", "0007_profiles_admin_library_public")]
AFTER = [("sopds_web_backend", "0008_single_superuser_role")]


@pytest.mark.django_db(transaction=True)
def test_roles_are_merged_into_superuser():
    executor = MigrationExecutor(connection)
    executor.migrate(BEFORE)
    U = executor.loader.project_state(BEFORE).apps.get_model("auth", "User")
    U.objects.create(username="old_admin", is_staff=True)
    U.objects.create(username="root", is_superuser=True)
    U.objects.create(username="reader")
    try:
        MigrationExecutor(connection).migrate(AFTER)
        flags = {u.username: (u.is_superuser, u.is_staff) for u in User.objects.all()}
        assert flags == {"old_admin": (True, True), "root": (True, True), "reader": (False, False)}
    finally:
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
