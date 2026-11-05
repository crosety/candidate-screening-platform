import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.test import Client

from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from web.asgi import application
from web.conversations import gpt_live

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    "synthetic,channels,expected",
    [(True, ["voice"], True), (False, ["voice"], False), (True, ["web"], False)],
)
async def test_live_flag_does_not_enable_live_for_real_applications_or_other_profiles(
    phone: tuple[Client, UUID, UUID],
    monkeypatch: pytest.MonkeyPatch,
    synthetic: bool,
    channels: list[str],
    expected: bool,
) -> None:
    from unittest.mock import AsyncMock

    monkeypatch.setattr("django.conf.settings.GPT_LIVE_ENABLED", True)
    service = SimpleNamespace(
        repo=SimpleNamespace(
            get_application=AsyncMock(return_value=SimpleNamespace(is_test=synthetic))
        ),
        versions=SimpleNamespace(
            get=AsyncMock(return_value=SimpleNamespace(content=SimpleNamespace(channels=channels)))
        ),
    )
    monkeypatch.setattr(gpt_live, "get_container", lambda: SimpleNamespace(conversations=service))
    session = SimpleNamespace(tenant_id=uuid4(), application_id=uuid4(), profile_version_id=uuid4())
    assert await gpt_live.enabled(session) is expected


class FakeBridge:
    def __init__(self, emit: Any, delegate: Any, record: Any, *, voice_id: str = "") -> None:
        self.emit, self.delegate, self.record = emit, delegate, record
        self.voice_id = voice_id
        self.close_reason = "close_requested"
        self.stopped = asyncio.Event()

    async def run(self, history: Any) -> None:
        await self.emit({"type": "ready", "sample_rate": 16000})
        await self.stopped.wait()

    async def audio(self, chunk: str) -> None:
        await self.emit({"type": "audio", "delta": chunk})

    def stop(self) -> None:
        self.stopped.set()


async def setup(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch, invalid: str = ""
) -> tuple[ApplicationCommunicator, str, Any]:
    _, tenant, app = phone
    service = get_container().conversations
    link = await service.repo.get_or_create_link(tenant, app)
    opened = await service.open_session(link)
    session = await service.repo.get_session(tenant, opened.session_id)

    async def enabled(s: Any) -> bool:
        return invalid != "disabled"

    monkeypatch.setattr(gpt_live, "enabled", enabled)
    monkeypatch.setattr(gpt_live, "GptLiveBridge", FakeBridge)
    target = replace(session, profile_version_id=uuid4()) if invalid == "version" else session
    ticket = await sync_to_async(gpt_live.issue_ticket)(target, link)
    if invalid == "replay":
        await sync_to_async(gpt_live.consume_ticket)(ticket)
    if invalid == "ticket":
        ticket = "bad"
    session_id = uuid4() if invalid == "session" else session.id
    comm = ApplicationCommunicator(
        application,
        {
            "type": "websocket",
            "path": f"/ws/voice/live/{session_id}/",
            "headers": [(b"origin", b"http://localhost:5174")],
            "query_string": f"ticket={ticket}".encode(),
        },
    )
    await comm.send_input({"type": "websocket.connect"})
    return comm, link, session


@pytest.mark.parametrize("invalid", ["version", "session", "ticket", "replay", "disabled"])
async def test_scope_and_one_use_ticket(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    comm, _, _ = await setup(phone, monkeypatch, invalid)
    assert (await comm.receive_output())["code"] == 4403
    await comm.send_input({"type": "websocket.disconnect", "code": 4403})
    await comm.wait()


async def test_audio_and_clean_close(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    comm, _, _ = await setup(phone, monkeypatch)
    assert (await comm.receive_output())["type"] == "websocket.accept"
    assert "ready" in (await comm.receive_output())["text"]
    await comm.send_input(
        {"type": "websocket.receive", "text": json.dumps({"type": "audio", "audio": "AQACAA=="})}
    )
    assert json.loads((await comm.receive_output())["text"]) == {
        "type": "audio",
        "delta": "AQACAA==",
    }
    await comm.send_input({"type": "websocket.disconnect", "code": 1000})
    await comm.wait()


async def test_first_voice_uses_guarded_question_and_reconnect_does_not_restart(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = []
    original_run = FakeBridge.run

    async def capture(self: FakeBridge, history: Any) -> None:
        captured.append((self.voice_id, history))
        await original_run(self, history)

    monkeypatch.setattr(FakeBridge, "run", capture)
    monkeypatch.setattr("django.conf.settings.GPT_LIVE_VOICE_ID", "voice_test_spain")
    for _ in range(2):
        comm, _, session = await setup(phone, monkeypatch)
        await comm.receive_output()
        assert "ready" in (await comm.receive_output())["text"]
        messages = await get_container().conversations.repo.list_messages(
            session.tenant_id, session.id
        )
        assert len(messages) == 1 and messages[0].direction == "out"
        assert captured[-1][0] == "voice_test_spain"
        assert captured[-1][1] == [
            {"role": "assistant", "content": [{"type": "output_text", "text": messages[0].text}]}
        ]
        await comm.send_input({"type": "websocket.disconnect", "code": 1000})
        await comm.wait()
    assert captured[0] == captured[1]


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "session.update", "instructions": "override"},
        {"type": "audio", "audio": "bad-base64"},
        {"type": "audio", "audio": "YQ=="},
    ],
)
async def test_browser_cannot_configure_model_or_send_invalid_audio(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch, payload: Any
) -> None:
    comm, _, _ = await setup(phone, monkeypatch)
    await comm.receive_output()
    await comm.receive_output()
    await comm.send_input({"type": "websocket.receive", "text": json.dumps(payload)})
    assert (await comm.receive_output())["code"] == 4400
    await comm.send_input({"type": "websocket.disconnect", "code": 4400})
    await comm.wait()


async def test_provider_consent_is_explicit_before_issuing_live_ticket(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _, _ = phone
    comm, link, session = await setup(phone, monkeypatch)
    await comm.receive_output()
    await comm.receive_output()
    await comm.send_input({"type": "websocket.disconnect", "code": 1000})
    await comm.wait()
    url = f"/api/v1/chat/sessions/{session.id}/voice"
    response = await sync_to_async(client.post)(
        url + "/config", {"token": link}, content_type="application/json"
    )
    assert response.json() == {"provider": "OpenAI", "transport": "gpt_live"}
    for data, status in [
        ({"token": link}, 422),
        ({"token": link, "consent": True}, 409),
        ({"token": link, "consent": True, "provider": "OpenAI"}, 200),
    ]:
        response = await sync_to_async(client.post)(url, data, content_type="application/json")
        assert response.status_code == status
    assert response.json()["transport"] == "gpt_live"
    assert response.json()["session_token"] == ""


async def test_malformed_json_closes_voice_without_running_inference(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    comm, _, _ = await setup(phone, monkeypatch)
    await comm.receive_output()
    await comm.receive_output()
    await comm.send_input({"type": "websocket.receive", "text": "{not valid json"})
    assert (await comm.receive_output())["code"] == 4400
    await comm.send_input({"type": "websocket.disconnect", "code": 4400})
    await comm.wait()
