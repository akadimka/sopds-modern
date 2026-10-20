"""Профили всем пользователям (раньше `UserProfile` создавался только при
первом заходе на страницу профиля) и «библиотека открыта всем» у
суперпользователя — после перехода на владение книгами пользователи видят то
же, что раньше (docs/multiuser-design.md, docs/multiuser-plan.md шаг 1.1)."""
from django.conf import settings
from django.db import migrations


def forwards(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    UserProfile = apps.get_model("sopds_web_backend", "UserProfile")
    for user in User.objects.all().iterator():
        UserProfile.objects.get_or_create(user=user)
    admin = User.objects.filter(is_superuser=True, is_active=True).order_by("id").first()
    if admin is not None:
        UserProfile.objects.filter(user=admin).update(library_public=True)


class Migration(migrations.Migration):

    dependencies = [
        ("sopds_web_backend", "0006_userprofile_library_fields"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
