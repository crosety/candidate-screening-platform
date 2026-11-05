import logging

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import ResolverMatch

from web.core.accesslog import RedactedAccessLog


def test_access_log_excludes_tokens_queries_and_body(caplog: pytest.LogCaptureFixture) -> None:
    request = RequestFactory().post(
        "/c/private-link-token?ticket=private-ticket", {"password": "private-password"}
    )
    request.resolver_match = ResolverMatch(lambda r: HttpResponse(), (), {}, route="c/<str:token>")
    with caplog.at_level(logging.INFO, logger="security.access"):
        response = RedactedAccessLog(lambda r: HttpResponse(status=403))(request)
    assert response.status_code == 403
    assert "route=c/<str:token> status=403" in caplog.text
    assert "private-" not in caplog.text and "password" not in caplog.text
