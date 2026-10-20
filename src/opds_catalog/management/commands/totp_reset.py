"""Отключить приложение-аутентификатор у пользователя (потерян телефон).

После сброса пользователь входит по паролю — для суперпользователей при
включённом «втором факторе» дальше спрашивается Telegram-код (если его
есть куда доставить; нет — `manage.py admin_login_code off`). Новое
приложение подключается в профиле заново, с новым секретом.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from sopds_web_backend import totp


class Command(BaseCommand):
    help = "Remove the authenticator app (TOTP) of a user, e.g. after a lost phone"

    def add_arguments(self, parser):
        parser.add_argument("username")

    def handle(self, *args, **options):
        user = get_user_model().objects.filter(username=options["username"]).first()
        if user is None:
            raise CommandError(f"user not found: {options['username']}")
        had = totp.device_for(user) is not None
        totp.disable(user)
        self.stdout.write(f"authenticator app for {user.username}: "
                          f"{'removed' if had else 'was not connected'}")
