"""Optional tunnel ingress. Never expose the console, ingest or candidate API."""

import os
import re
from uuid import UUID

from asgiref.typing import (
    ASGIReceiveCallable,
    ASGISendCallable,
    HTTPResponseBodyEvent,
    HTTPResponseStartEvent,
    Scope,
    WebSocketCloseEvent,
)

from web.asgi import application as internal
from web.conversations.speech_engine import SpeechEngineConsumer

speech_engine = SpeechEngineConsumer.as_asgi()


async def application(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
    path = str(scope.get("path", ""))
    allowed = bool(
        re.fullmatch(
            r"/api/v1/(?:webhooks/whatsapp/[a-f0-9-]{36}|voice/[a-f0-9-]{36}/v1/chat/completions)",
            path,
        )
    )
    # An explicitly scoped tunnel must never fall back to the wider ingress.
    voice_tenant = os.environ.get("EXTERNAL_VOICE_TENANT_ID")
    if voice_tenant is not None:
        try:
            tenant = str(UUID(voice_tenant))
        except ValueError:
            allowed = False
        else:
            allowed = (
                path == f"/api/v1/voice/{tenant}/v1/chat/completions"
                and (scope["type"] == "websocket" or scope.get("method") == "POST")
                and not scope.get("query_string", b"")
            )
    if scope["type"] == "websocket" and voice_tenant is not None and allowed:
        # Same isolated, tenant-scoped callback; no other WS route is exposed.
        await speech_engine(scope, receive, send)
        return
    if scope["type"] != "http" or not allowed:
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
    if voice_tenant is not None:
        # This listener serves one signed callback, not a host-routed application.
        http_scope = scope.copy()
        http_scope["headers"] = [
            (key, value)
            for key, value in scope.get("headers", [])
            if key.lower() not in {b"host", b"x-forwarded-host", b"forwarded"}
        ] + [(b"host", b"localhost")]
        await internal(http_scope, receive, send)
        return
    await internal(scope, receive, send)
