from constance import config
from django.utils import translation
from django.utils.deprecation import MiddlewareMixin


class SOPDSLocaleMiddleware(MiddlewareMixin):
    """Устанавливаем локаль сервера для всех запросов"""

    def process_request(self, request):
        request.LANG = config.SOPDS_LANGUAGE
        translation.activate(request.LANG)
        request.LANGUAGE_CODE = request.LANG
