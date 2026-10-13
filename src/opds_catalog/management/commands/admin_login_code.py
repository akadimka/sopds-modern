"""Включить/выключить код входа для администраторов (sopds_web_backend/login_codes.py).

Выход из тупика: код не доходит (бот сломан, Telegram недоступен) — на
сервере `manage.py admin_login_code off`, войти по паролю и починить бота.
"""
from django.core.management.base import BaseCommand

from fb2parser_core.settings_manager import SettingsManager
from fb2parser_web.fb2parser_bridge import _config_path


class Command(BaseCommand):
    help = "Sign-in code for administrators via Telegram: on | off | status"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["on", "off", "status"])

    def handle(self, *args, **options):
        sm = SettingsManager(_config_path())
        values = sm.get_autosync_settings()
        if options["action"] != "status":
            sm.set_autosync_settings({"admin_login_code": options["action"] == "on"})
            values = sm.get_autosync_settings()
        state = "on" if values["admin_login_code"] else "off"
        self.stdout.write(f"admin sign-in code: {state}")
