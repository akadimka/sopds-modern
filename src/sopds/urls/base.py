"""
URL configuration for sopds project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf.urls.i18n import set_language
from django.contrib import admin
from django.urls import include, path, reverse_lazy
from django.views.generic import RedirectView

from sopds_web_backend import views as web_views

# /admin/ — только суперпользователь (роль — is_superuser, как на сайте и в
# fb2parser; одного is_staff мало).
admin.site.has_permission = lambda request: request.user.is_active and request.user.is_superuser

urlpatterns = [
    path("i18n/setlang/", set_language, name="set_language"),
    path("opds/", include("opds_catalog.urls", namespace="opds")),
    path("web/", include("sopds_web_backend.urls", namespace="web")),
    path("fb2parser/", include("fb2parser_web.urls", namespace="fb2parser")),
    # Вход в админку — через общую форму сайта: у неё второй шаг (код из
    # Telegram для суперпользователей), у стандартной формы Django его нет.
    path("admin/login/", RedirectView.as_view(url=reverse_lazy("web:login"), query_string=True)),
    path("admin/", admin.site.urls),
    path("sw.js", web_views.service_worker, name="service_worker"),
    path("", RedirectView.as_view(url=reverse_lazy("web:main"))),
]
