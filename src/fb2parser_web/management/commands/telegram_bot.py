"""Процесс Telegram-бота автосинхронизации: кнопки решений для админа.

    python manage.py telegram_bot

Работает постоянно (под systemd — см. DEPLOY.md, шаг 19a); токен и chat id
берёт из настроек при каждом опросе, так что после их смены перезапуск не
нужен. Подробности — fb2parser_core/telegram_bot.py.
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Telegram-бот автосинхронизации (кнопки решений по спорным порциям)."

    def handle(self, *args, **options):
        from fb2parser_core.telegram_bot import AdminBot
        from fb2parser_web.fb2parser_bridge import get_autosync_service
        from sopds_web_backend.telegram_library import LibraryHandler

        self.stdout.write("Telegram-бот запущен (Ctrl+C — остановить).")
        try:
            AdminBot(get_autosync_service, extra_handler=LibraryHandler()).run_forever()
        except KeyboardInterrupt:
            self.stdout.write("Остановлен.")
