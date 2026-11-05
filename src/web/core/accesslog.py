"""Log route templates only; never candidate URLs, query strings or request bodies."""

import logging
import time
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

logger = logging.getLogger("security.access")


class RedactedAccessLog:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        started = time.monotonic()
        response = self.get_response(request)
        match = request.resolver_match
        route = match.route if match is not None else "unmatched"
        logger.info(
            "request method=%s route=%s status=%s elapsed_ms=%d",
            request.method,
            route,
            response.status_code,
            round((time.monotonic() - started) * 1000),
        )
        return response
