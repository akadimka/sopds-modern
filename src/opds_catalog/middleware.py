from opds_catalog.sopds_config import sopds_cfg as config
from django.utils import translation
from django.utils.deprecation import MiddlewareMixin

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
