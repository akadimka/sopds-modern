from django.contrib.auth import logout
from django.contrib.auth.models import AnonymousUser
from django.utils import translation
from django.utils.deprecation import MiddlewareMixin

from opds_catalog.sopds_config import sopds_cfg as config


class OpdsOnlySessionMiddleware(MiddlewareMixin):
    """Сессия, созданная входом читалки (HTTP Basic в /opds/), действует
    только для /opds/: на сайте и в /fb2parser/ её владелец — аноним. Иначе
    пароль из настроек читалки (а у администратора — без кода из Telegram)
    давал бы полный вход на сайт. Сессия по паролю для читалок гаснет, если
    этот пароль сменили или отозвали."""

    def process_request(self, request):
        from sopds_web_backend import opds_passwords

        mark = request.session.get(opds_passwords.SESSION_KEY)
        if not mark:
            return
        if not request.path_info.startswith("/opds/"):
            request.user = AnonymousUser()
            return
        if request.user.is_authenticated and not opds_passwords.session_still_valid(request.user, mark):
            logout(request)

_SUPPORTED = {'en', 'ru', 'en-us', 'en-gb'}


class SOPDSLocaleMiddleware(MiddlewareMixin):
    """Язык: cookie django_language → сессия → config.json → en."""

    def process_request(self, request):
        lang = (
            request.COOKIES.get('django_language')
            or request.session.get('_language')
            or config.SOPDS_LANGUAGE
            or 'en'
        )
        # нормализуем: 'en-US' → 'en', 'ru' → 'ru'
        lang_short = lang.lower().split('-')[0]
        if lang_short not in ('en', 'ru'):
            lang_short = 'en'
        translation.activate(lang_short)
        request.LANGUAGE_CODE = lang_short
        request.LANG = lang_short
