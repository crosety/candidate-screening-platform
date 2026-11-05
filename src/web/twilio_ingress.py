"""Separate public listener: exactly two signed callbacks for one configured tenant."""

import asyncio

from asgiref.typing import (
    ASGIReceiveCallable,
    ASGISendCallable,
    HTTPResponseBodyEvent,
    HTTPResponseStartEvent,
    Scope,
    WebSocketCloseEvent,
)
from django.conf import settings

from web.asgi import application as internal


async def application(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
    from pathlib import Path

    from adapters.channels.twilio import TwilioConfig

    try:
        raw = await asyncio.to_thread(Path(settings.TWILIO_CONFIG_FILE).read_text)
        config = TwilioConfig.model_validate_json(raw)
        allowed = (
            config.active
            and scope["type"] == "http"
            and scope.get("method") == "POST"
            and not scope.get("query_string", b"")
            and scope.get("path")
            in {
                f"/api/v1/webhooks/twilio/{config.tenant_id}/inbound",
                f"/api/v1/webhooks/twilio/{config.tenant_id}/status",
            }
        )
    except (OSError, ValueError):
        allowed = False
    if not allowed:
        if scope["type"] == "http":
            start: HTTPResponseStartEvent = {
                "type": "http.response.start",
                "status": 404,
                "headers": [],
                "trailers": False,
            }
            body: HTTPResponseBodyEvent = {
                "type": "http.response.body",
                "body": b"Not found",
                "more_body": False,
            }
            await send(start)
            await send(body)
        elif scope["type"] == "websocket":
            close: WebSocketCloseEvent = {
                "type": "websocket.close",
                "code": 1008,
                "reason": "Not found",
            }
            await send(close)
        return
    assert scope["type"] == "http"
    copied = scope.copy()
    copied["headers"] = [
        (k, v)
        for k, v in scope.get("headers", [])
        if k.lower() not in {b"host", b"x-forwarded-host", b"forwarded"}
    ] + [(b"host", b"localhost")]
    await internal(copied, receive, send)
