import pytest
from asgiref.testing import ApplicationCommunicator
from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope

from web.external_asgi import application


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/api/v1/docs", "/api/v1/tenants", "/api/v1/ingest/demo/applications", "/"]
)
async def test_ingress_never_routes_console_or_candidate_paths(path: str) -> None:
    communicator = ApplicationCommunicator(application, {"type": "http", "path": path})
    start = await communicator.receive_output()
    assert start["status"] == 404
    assert (await communicator.receive_output())["body"] == b"Not found"
    await communicator.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", ["", "invalid", "123"])
async def test_voice_ingress_invalid_scope_fails_closed(
    tenant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXTERNAL_VOICE_TENANT_ID", tenant)
    communicator = ApplicationCommunicator(
        application,
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/voice/12345678-1234-1234-1234-123456789012/v1/chat/completions",
        },
    )
    assert (await communicator.receive_output())["status"] == 404
    await communicator.receive_output()
    await communicator.wait()


@pytest.mark.asyncio
async def test_scoped_voice_ingress_only_forwards_exact_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = "12345678-1234-1234-1234-123456789012"
    path = f"/api/v1/voice/{tenant}/v1/chat/completions"
    monkeypatch.setenv("EXTERNAL_VOICE_TENANT_ID", tenant)
    forwarded: list[Scope] = []

    async def internal(scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
        forwarded.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": [], "trailers": False})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    monkeypatch.setattr("web.external_asgi.internal", internal)
    for route, method, query, expected in [
        (path, "POST", b"", 204),
        (path, "GET", b"", 404),
        (path, "POST", b"x=1", 404),
        (path + "/", "POST", b"", 404),
        (path.replace(tenant, "aaaaaaaa-1234-1234-1234-123456789012"), "POST", b"", 404),
        (f"/api/v1/webhooks/whatsapp/{tenant}", "POST", b"", 404),
        ("/api/v1/health", "GET", b"", 404),
    ]:
        communicator = ApplicationCommunicator(
            application,
            {
                "type": "http",
                "method": method,
                "path": route,
                "query_string": query,
                "headers": [(b"host", b"untrusted.example"), (b"authorization", b"Bearer test")],
            },
        )
        assert (await communicator.receive_output())["status"] == expected
        await communicator.receive_output()
        await communicator.wait()
    assert len(forwarded) == 1
    assert forwarded[0]["type"] == "http"
    assert forwarded[0]["headers"] == [(b"authorization", b"Bearer test"), (b"host", b"localhost")]
