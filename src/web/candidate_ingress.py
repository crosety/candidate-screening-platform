"""Temporary candidate-only surface. Never expose staff, admin, ingestion or source files."""

import asyncio
import hashlib
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

from asgiref.typing import ASGIReceiveCallable, ASGIReceiveEvent, ASGISendCallable, Scope

from web.asgi import application as internal


async def response(
    send: ASGISendCallable,
    status: int,
    body: bytes = b"Not found",
    content_type: str = "text/plain; charset=utf-8",
) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", content_type.encode()),
                (b"cache-control", b"no-store"),
                (b"referrer-policy", b"no-referrer"),
                (b"x-content-type-options", b"nosniff"),
                (b"x-frame-options", b"DENY"),
                (b"permissions-policy", b"microphone=(self), camera=()"),
                (
                    b"content-security-policy",
                    b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    b"connect-src 'self'; img-src 'self' data:; media-src 'self' blob:; "
                    b"frame-ancestors 'none'; base-uri 'none'",
                ),
            ],
            "trailers": False,
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


def authorized_digest(
    tenant: UUID, digest: str, session: str = "", *, test_only: bool = False
) -> bool:
    from django.utils import timezone

    from web.conversations.models import Application, CandidateLink, Session
    from web.tenancy.context import tenant_context

    with tenant_context(tenant):
        link = CandidateLink.objects.filter(
            tenant_id=tenant, token=digest, revoked_at__isnull=True, expires_at__gt=timezone.now()
        ).first()
        if not link:
            return False
        app = Application.objects.filter(pk=link.application_id, erased_at__isnull=True).first()
        if not app or (test_only and not app.is_test):
            return False
        return (
            not session
            or Session.objects.filter(pk=session, application_id=link.application_id).exists()
        )


async def application(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
    from django.conf import settings
    from django.core.cache import cache

    from adapters.channels.twilio_sms import SmsConfig
    from web.conversations.links import digest_token

    if scope["type"] not in {"http", "websocket"}:
        return
    try:
        config = SmsConfig.model_validate_json(
            await asyncio.to_thread(Path(settings.TWILIO_SMS_CONFIG_FILE).read_text)
        )
        if not config.active:
            raise ValueError("expired")
    except (ValueError, OSError):
        if scope["type"] == "http":
            await response(send, 410, b"El acceso temporal ha terminado. Conserva tu enlace.")
        else:
            await send({"type": "websocket.close", "code": 1008, "reason": "Expired"})
        return
    path = scope.get("path", "")
    headers = dict(scope.get("headers", []))
    public_host = urlsplit(config.candidate_base_url).netloc.encode()
    # Only this exact tunnel origin; proxy headers cannot select another destination.
    host_ok = headers.get(b"host") == public_host
    query = scope.get("query_string", b"")
    phone_callback = re.fullmatch(
        r"/api/v1/webhooks/twilio-voice/([0-9a-f-]{36})/([0-9a-f-]{36})/(answer|status|media)", path
    )
    if host_ok and phone_callback and phone_callback[1] == str(config.tenant_id):
        # The separate listener still owns signature, call binding and transport guards.
        from web.telephone_ingress import application as telephone

        await telephone(scope, receive, send)
        return
    allowed = False
    copied = scope.copy()
    if scope["type"] == "http":
        method = scope.get("method")
        if host_ok and method == "GET" and not query:
            candidate = re.fullmatch(r"/c/([a-zA-Z0-9_-]{32,128})", path)
            asset = re.fullmatch(r"/assets/([a-zA-Z0-9_.-]+\.(?:js|css|woff2?|png|svg))", path)
            link_allowed = False
            if candidate is not None:
                link_allowed = await asyncio.to_thread(
                    authorized_digest, config.tenant_id, digest_token(candidate[1])
                )
            if link_allowed:
                file = Path(settings.CANDIDATE_DIST_DIR) / "index.html"
            elif asset:
                file = Path(settings.CANDIDATE_DIST_DIR) / "assets" / asset[1]
            else:
                file = None
            if file and file.is_file():
                await response(
                    send,
                    200,
                    await asyncio.to_thread(file.read_bytes),
                    mimetypes.guess_type(file.name)[0] or "application/octet-stream",
                )
                return
        api_path = path in {
            "/api/v1/chat/sessions",
            "/api/v1/chat/telephone/status",
            "/api/v1/chat/telephone/request",
            "/api/v1/chat/sms-link",
        }
        voice = re.fullmatch(r"/api/v1/chat/sessions/([0-9a-f-]{36})/voice(?:/config)?", path)
        callback = re.fullmatch(
            r"/api/v1/webhooks/twilio-sms/([0-9a-f-]{36})/([0-9a-f-]{36})/status", path
        )
        if host_ok and method == "POST" and not query and (api_path or voice or callback):
            body = b""
            try:
                async with asyncio.timeout(5):
                    while True:
                        event = await receive()
                        if event["type"] != "http.request":
                            raise ValueError("disconnected")
                        body += event.get("body", b"")
                        if len(body) > 24000:
                            raise ValueError("oversized")
                        if not event.get("more_body"):
                            break
                if callback:
                    allowed = callback[1] == str(config.tenant_id)
                else:
                    origin = headers.get(b"origin", b"").decode()
                    if origin != config.candidate_base_url:
                        raise ValueError("origin")
                    payload = json.loads(body)
                    token = payload.get("token", "")
                    session = voice[1] if voice else str(payload.get("session_id", ""))
                    allowed = isinstance(token, str) and await asyncio.to_thread(
                        authorized_digest,
                        config.tenant_id,
                        digest_token(token),
                        session,
                        test_only=bool(voice),
                    )
            except (ValueError, TypeError, AttributeError, TimeoutError):
                allowed = False
            original_receive = receive
            consumed = False

            async def replay() -> ASGIReceiveEvent:
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await original_receive()

            receive = replay
        if not allowed:
            await response(send, 404)
            return
    else:
        match = re.fullmatch(r"/ws/(chat|voice/direct)/([0-9a-f-]{36})/", path)
        if host_ok and headers.get(b"origin", b"").decode() == config.candidate_base_url and match:
            params = parse_qs(query.decode())
            ticket = params.get("ticket", [""])[0]
            key = ("socket:" if match[1] == "chat" else "live-ticket:") + hashlib.sha256(
                ticket.encode()
            ).hexdigest()
            payload = await asyncio.to_thread(cache.get, key)
            if (
                isinstance(payload, dict)
                and set(params) == {"ticket"}
                and len(params["ticket"]) == 1
            ):
                tenant = payload.get("tenant_id", payload.get("tenant"))
                digest = str(payload.get("token_digest", payload.get("digest", "")))
                allowed = tenant == str(config.tenant_id) and await asyncio.to_thread(
                    authorized_digest,
                    config.tenant_id,
                    digest,
                    match[2],
                    test_only=match[1] == "voice/direct",
                )
        if not allowed:
            await send({"type": "websocket.close", "code": 1008, "reason": "Not found"})
            return
    copied["headers"] = [
        (k, v)
        for k, v in scope.get("headers", [])
        if k.lower()
        not in {b"host", b"origin", b"cookie", b"authorization", b"x-forwarded-host", b"forwarded"}
    ]
    copied["headers"] = [
        *copied["headers"],
        (b"host", b"localhost"),
        (b"origin", b"http://localhost"),
    ]
    # Expiry also closes already-open sockets; server state still owns their authorization.
    from django.utils import timezone

    try:
        async with asyncio.timeout(max(0, (config.expires_at - timezone.now()).total_seconds())):
            await internal(copied, receive, send)
    except TimeoutError:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1000, "reason": "Window ended"})
