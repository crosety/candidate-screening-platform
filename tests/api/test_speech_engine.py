import asyncio
import json
from uuid import UUID, uuid4

import pytest
from asgiref.testing import ApplicationCommunicator
from django.test import Client

from adapters.channels.eleven import VoiceAgent
from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from tests.contracts.test_speech_engine import signed_jwt
from web.conversations import speech_engine
from web.conversations.voice_api import session_token
from web.external_asgi import application

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


async def setup(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch, invalid: str = ""
) -> ApplicationCommunicator:
    _, tenant, app = phone
    service = get_container().conversations
    link = await service.repo.get_or_create_link(tenant, app)
    opened = await service.open_session(link)
    session = await service.repo.get_session(tenant, opened.session_id)
    monkeypatch.setenv("EXTERNAL_VOICE_TENANT_ID", str(tenant))

    async def config(tenant_id: UUID, version_id: UUID) -> VoiceAgent:
        assert tenant_id == tenant and version_id == session.profile_version_id
        return VoiceAgent(
            agent_id="seng_test",
            api_key="private-key",
            callback_secret="unused",
            callback_url="https://callback.invalid",
        )

    monkeypatch.setattr(speech_engine, "configuration", config)
    token = session_token(
        tenant if invalid != "tenant" else uuid4(),
        session.id,
        session.profile_version_id if invalid != "version" else uuid4(),
    )
    headers = [
        (b"x-orbio-session", ("bad" if invalid == "session" else token).encode()),
        (
            b"x-elevenlabs-speech-engine-authorization",
            ("bad" if invalid == "provider" else signed_jwt()).encode(),
        ),
    ]
    comm = ApplicationCommunicator(
        application,
        {
            "type": "websocket",
            "path": f"/api/v1/voice/{tenant}/v1/chat/completions",
            "headers": headers,
            "query_string": b"",
        },
    )
    await comm.send_input({"type": "websocket.connect"})
    return comm


async def send(comm: ApplicationCommunicator, payload: object) -> None:
    await comm.send_input({"type": "websocket.receive", "text": json.dumps(payload)})


async def read(comm: ApplicationCommunicator) -> dict[str, object]:
    result = await comm.receive_output()
    assert result["type"] == "websocket.send"
    return json.loads(result["text"])


@pytest.mark.parametrize("invalid", ["tenant", "version", "session", "provider"])
async def test_socket_rejects_invalid_scope_or_provider(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    comm = await setup(phone, monkeypatch, invalid)
    assert (await comm.receive_output())["code"] == 4401
    await comm.send_input({"type": "websocket.disconnect", "code": 4401})
    await comm.wait()


async def test_pings_continue_while_guardian_runs_without_filler(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    ready = asyncio.Event()
    calls = []

    async def reply(tenant: UUID, session: UUID, messages: list[dict[str, str]], scope: str) -> str:
        calls.append(scope)
        await ready.wait()
        return "Respuesta validada. ¿Seguimos?"

    monkeypatch.setattr(speech_engine, "guarded_reply", reply)
    comm = await setup(phone, monkeypatch)
    assert (await comm.receive_output())["type"] == "websocket.accept"
    await send(comm, {"type": "init", "conversation_id": "conv_test"})
    turn = {
        "type": "user_transcript",
        "event_id": 3,
        "user_transcript": [{"role": "user", "content": "Hola"}],
    }
    await send(comm, turn)
    await send(comm, turn)
    await send(comm, {"type": "ping"})
    assert await read(comm) == {"type": "pong"}
    assert await comm.receive_nothing(timeout=0.05)
    ready.set()
    chunks = [await read(comm) for _ in range(3)]
    assert chunks[-1] == {"type": "agent_response", "event_id": 3, "content": "", "is_final": True}
    assert "Respuesta validada" in str(chunks)
    assert "revisando" not in str(chunks)
    assert calls == ["conv_test"]
    await comm.send_input({"type": "websocket.disconnect", "code": 1000})
    await comm.wait()


async def test_interrupted_turn_never_sends_stale_response(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    first = asyncio.Event()

    async def reply(tenant: UUID, session: UUID, messages: list[dict[str, str]], scope: str) -> str:
        if messages[-1]["content"] == "primero":
            await first.wait()
            return "Respuesta anterior."
        return "Respuesta nueva."

    monkeypatch.setattr(speech_engine, "guarded_reply", reply)
    comm = await setup(phone, monkeypatch)
    await comm.receive_output()
    await send(comm, {"type": "init", "conversation_id": "conv_interrupt"})
    for event_id, text in [(1, "primero"), (2, "segundo")]:
        await send(
            comm,
            {
                "type": "user_transcript",
                "event_id": event_id,
                "user_transcript": [{"role": "user", "content": text}],
            },
        )
    assert (await read(comm))["content"] == "Respuesta nueva. "
    assert (await read(comm))["is_final"] is True
    first.set()
    assert await comm.receive_nothing(timeout=0.05)
    await comm.send_input({"type": "websocket.disconnect", "code": 1000})
    await comm.wait()


async def test_silence_does_not_infer_or_persist_a_turn(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*args: object) -> str:
        raise AssertionError("Silence is not a candidate message")

    monkeypatch.setattr(speech_engine, "guarded_reply", forbidden)
    comm = await setup(phone, monkeypatch)
    await comm.receive_output()
    await send(comm, {"type": "init", "conversation_id": "conv_silence"})
    await send(
        comm,
        {
            "type": "user_transcript",
            "event_id": 1,
            "user_transcript": [{"role": "user", "content": "..."}],
        },
    )
    assert await read(comm) == {
        "type": "agent_response",
        "event_id": 1,
        "content": "",
        "is_final": True,
    }
    await send(comm, {"type": "ping"})
    assert await read(comm) == {"type": "pong"}
    await comm.send_input({"type": "websocket.disconnect", "code": 1000})
    await comm.wait()


async def test_processing_signal_precedes_inference_and_clears_on_disconnect(
    phone: tuple[Client, UUID, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    started = asyncio.Event()
    ready = asyncio.Event()
    finished = asyncio.Event()
    signals: list[dict[str, object]] = []

    class Layer:
        async def group_send(self, group: str, message: dict[str, object]) -> None:
            assert group.startswith("chat_")
            signals.append(message)

    async def reply(tenant: UUID, session: UUID, messages: list[dict[str, str]], scope: str) -> str:
        assert signals == [{"type": "chat.event", "payload": {"type": "typing", "on": True}}]
        started.set()
        await ready.wait()
        finished.set()
        return "Respuesta guardada."

    monkeypatch.setattr(speech_engine, "get_channel_layer", Layer)
    monkeypatch.setattr(speech_engine, "guarded_reply", reply)
    comm = await setup(phone, monkeypatch)
    await comm.receive_output()
    await send(comm, {"type": "init", "conversation_id": "conv_processing"})
    await send(
        comm,
        {
            "type": "user_transcript",
            "event_id": 1,
            "user_transcript": [{"role": "user", "content": "Hola"}],
        },
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    await comm.send_input({"type": "websocket.disconnect", "code": 1000})
    await comm.wait()
    assert signals[-1] == {"type": "chat.event", "payload": {"type": "typing", "on": False}}
    ready.set()
    await asyncio.wait_for(finished.wait(), timeout=1)
