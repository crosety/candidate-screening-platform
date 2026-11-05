import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from asgiref.testing import ApplicationCommunicator

from tests.contracts.test_twilio_adapter import config
from web.twilio_ingress import application


@pytest.mark.asyncio
async def test_expired_ingress_blocks_even_valid_scoped_route(
    settings: Any, tmp_path: Path
) -> None:
    cfg = config(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    file = tmp_path / "config.json"
    file.write_text(
        json.dumps({**cfg.model_dump(mode="json"), "auth_token": "synthetic-token-for-tests"})
    )
    settings.TWILIO_CONFIG_FILE = str(file)
    communicator = ApplicationCommunicator(
        application,
        {
            "type": "http",
            "method": "POST",
            "path": f"/api/v1/webhooks/twilio/{cfg.tenant_id}/inbound",
        },
    )
    await communicator.send_input({"type": "http.request", "body": b""})
    assert (await communicator.receive_output())["status"] == 404
    await communicator.wait()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    ["/api/v1/health", "/api/v1/login", "/", "/c/private", "/api/v1/webhooks/twilio/wrong/inbound"],
)
async def test_ingress_never_exposes_application(path: str, settings: Any, tmp_path: Path) -> None:
    cfg = config()
    file = tmp_path / "config.json"
    file.write_text(
        json.dumps({**cfg.model_dump(mode="json"), "auth_token": "synthetic-token-for-tests"})
    )
    settings.TWILIO_CONFIG_FILE = str(file)
    communicator = ApplicationCommunicator(
        application, {"type": "http", "method": "POST", "path": path}
    )
    await communicator.send_input({"type": "http.request", "body": b""})
    assert (await communicator.receive_output())["status"] == 404
    await communicator.wait()


@pytest.mark.asyncio
async def test_only_scoped_post_without_query_reaches_internal(
    settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = config()
    file = tmp_path / "config.json"
    file.write_text(
        json.dumps({**cfg.model_dump(mode="json"), "auth_token": "synthetic-token-for-tests"})
    )
    settings.TWILIO_CONFIG_FILE = str(file)
    seen = []

    async def internal(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    monkeypatch.setattr("web.twilio_ingress.internal", internal)
    path = f"/api/v1/webhooks/twilio/{cfg.tenant_id}/inbound"
    for method, query, status in [("POST", b"", 204), ("GET", b"", 404), ("POST", b"x=1", 404)]:
        communicator = ApplicationCommunicator(
            application,
            {
                "type": "http",
                "method": method,
                "path": path,
                "query_string": query,
                "headers": [(b"host", b"untrusted.example"), (b"x-forwarded-host", b"bad.example")],
            },
        )
        await communicator.send_input({"type": "http.request", "body": b""})
        assert (await communicator.receive_output())["status"] == status
        await communicator.wait()
    assert len(seen) == 1 and seen[0]["headers"] == [(b"host", b"localhost")]
    socket = ApplicationCommunicator(application, {"type": "websocket", "path": path})
    await socket.send_input({"type": "websocket.connect"})
    assert (await socket.receive_output())["type"] == "websocket.close"
    await socket.wait()
