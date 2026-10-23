"""Режим «На реконструкции» с сервера — в том числе аварийный выход, если
суперпользователь не может войти (sopds_web_backend/maintenance.py).

    python manage.py maintenance status
    python manage.py maintenance on [--message "Вернёмся к 18:00"]
    python manage.py maintenance off
"""
from django.core.management.base import BaseCommand

from opds_catalog.sopds_config import sopds_cfg as config


class Command(BaseCommand):
    help = "Turn the maintenance mode (site, OPDS and bot closed for users) on or off"

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["on", "off", "status"])
        parser.add_argument("--message", default=None, help="text for users while the site is closed")

    def handle(self, *args, action, message, **options):
        if action != "status":
            if message is not None:
                config.SOPDS_MAINTENANCE_MESSAGE = message
            config.SOPDS_MAINTENANCE = action == "on"
        state = "ON" if config.SOPDS_MAINTENANCE else "off"
        text = config.SOPDS_MAINTENANCE_MESSAGE or "(default text)"
        self.stdout.write(f"maintenance: {state}; message: {text}")
