"""Одна роль «Суперпользователь» вместо пары Admin (is_staff) /
суперпользователь: «admin» — только имя учётной записи, роль —
superuser; суперпользователь видит всю библиотеку (docs/multiuser-design.md).

Бывший Admin (is_staff без is_superuser) становится суперпользователем;
суперпользователю без is_staff флаг ставится — он нужен fb2parser и /admin/.
Обратно не откатывается: прежнее деление не восстановить (кто был кем).
"""
from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    User.objects.filter(is_staff=True, is_superuser=False).update(is_superuser=True)
    User.objects.filter(is_superuser=True, is_staff=False).update(is_staff=True)


class Migration(migrations.Migration):

    dependencies = [
        ("sopds_web_backend", "0007_profiles_admin_library_public"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
