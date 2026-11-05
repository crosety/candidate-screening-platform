"""Isolated public listener: signed Twilio call callbacks, no candidate or staff API."""

import asyncio
import re
from uuid import UUID

from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope

from web.asgi import application as internal


async def application(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
    from screening.domain.errors import NotAllowed
    from web.conversations.telephone import configuration
    from web.conversations.telephone_stream import TelephoneConsumer

    if scope["type"] not in {"http", "websocket"}:
        return

    match = re.fullmatch(
        r"/api/v1/webhooks/twilio-voice/([0-9a-f-]{36})/([0-9a-f-]{36})/(answer|status|media)",
        scope.get("path", ""),
    )
    allowed = False
    if match and not scope.get("query_string"):
        try:
            tenant, attempt, kind = UUID(match[1]), UUID(match[2]), match[3]
            await asyncio.to_thread(configuration, tenant, active=kind != "status")
            allowed = (
                scope["type"] == "http"
                and scope.get("method") == "POST"
                and kind in {"answer", "status"}
            ) or (scope["type"] == "websocket" and kind == "media")
        except (ValueError, NotAllowed):
            allowed = False
    if not allowed or not match:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": "Not found"})
        elif scope["type"] == "http":
            await send(
                {"type": "http.response.start", "status": 404, "headers": [], "trailers": False}
            )
            await send({"type": "http.response.body", "body": b"Not found", "more_body": False})
        return
    if scope["type"] == "websocket":
        # The consumer checks the provider's signature, not browser Origin. This does
        # not relax Origin validation on the normal browser WebSocket routes.
        copied = dict(scope)
        copied["url_route"] = {"kwargs": {"tenant_id": str(tenant), "attempt_id": str(attempt)}}
        await TelephoneConsumer.as_asgi()(copied, receive, send)
    else:
        copied_http = scope.copy()
        copied_http["headers"] = [
            (k, v)
            for k, v in scope.get("headers", [])
            if k.lower() not in {b"host", b"x-forwarded-host", b"forwarded"}
        ] + [(b"host", b"localhost")]
        await internal(copied_http, receive, send)
