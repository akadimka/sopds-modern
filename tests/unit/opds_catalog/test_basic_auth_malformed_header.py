"""OPDS Basic-auth: мусор в заголовке Authorization (не base64, не UTF-8,
без ":") ронял view необработанным исключением (500) вместо 401."""
import base64

import pytest
from django.http import HttpResponse
from django.test import RequestFactory

from opds_catalog.decorators import sopds_auth_validate


@sopds_auth_validate
def _view(request):
    return HttpResponse("ok")


@pytest.mark.django_db
@pytest.mark.parametrize("value", [
    "Basic !!!not-base64!!!",
    "Basic " + base64.b64encode(b"\xff\xfe").decode(),
    "Basic " + base64.b64encode(b"no-colon").decode(),
])
def test_malformed_basic_auth_is_401(value, override_config):
    from django.contrib.auth.models import AnonymousUser

    request = RequestFactory().get("/opds/", HTTP_AUTHORIZATION=value)
    request.user = AnonymousUser()
    request.session = {}
    with override_config(SOPDS_AUTH=True):
        response = _view(request)
    assert response.status_code == 401
