import json
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from asgiref.sync import async_to_sync, sync_to_async
from asgiref.testing import ApplicationCommunicator
from django.core.cache import cache
from django.core.management import call_command
from django.db import connections

from screening.application.ports import NewApplication
from screening.domain.errors import DomainError
from screening.platform.container import get_container
from web.asgi import application
from web.conversations.links import digest_token
from web.core.security import issue_socket_ticket

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.asyncio]


@pytest_asyncio.fixture(autouse=True)
async def _close_thread_connections() -> AsyncIterator[None]:
    yield
    await sync_to_async(connections.close_all)()


@pytest.fixture
def target() -> tuple[UUID, str]:
    call_command("seed_local")
    service = get_container().conversations
    _, link = async_to_sync(service.ingest)(
        "grupo_sazon", NewApplication("ws-test", "Lucía", "", "València", "repartidores")
    )
    token = link.rsplit("/", 1)[1]
    opened = async_to_sync(service.open_session)(token)
    return opened.session_id, token


async def socket(
    session_id: UUID, token: str, origin: str = "http://localhost:5174"
) -> ApplicationCommunicator:
    try:
        session = await get_container().conversations.authorize(token, session_id)
        ticket = issue_socket_ticket(session.tenant_id, session_id, digest_token(token))
    except DomainError:
        ticket = token
    return ApplicationCommunicator(
        application,
        {
            "type": "websocket",
            "path": f"/ws/chat/{session_id}/",
            "query_string": f"ticket={ticket}".encode(),
            "headers": [(b"origin", origin.encode())],
            "scheme": "ws",
            "subprotocols": [],
            "server": ("localhost", 5174),
            "client": ("127.0.0.1", 12345),
        },
    )


async def read_json(comm: ApplicationCommunicator) -> dict[str, Any]:
    event = await comm.receive_output(timeout=3)
    assert event["type"] == "websocket.send", event
    return json.loads(event["text"])


async def send_json(comm: ApplicationCommunicator, content: Any) -> None:
    await comm.send_input({"type": "websocket.receive", "text": json.dumps(content)})


async def ready(target: tuple[UUID, str]) -> ApplicationCommunicator:
    comm = await socket(*target)
    await comm.send_input({"type": "websocket.connect"})
    assert (await comm.receive_output())["type"] == "websocket.accept"
    events = [await read_json(comm) for _ in range(4)]
    assert [e["type"] for e in events] == ["history", "typing", "message", "typing"]
    assert events[0]["messages"] == []
    assert events[1]["on"] is True and events[3]["on"] is False
    assert "asistente virtual en modo de prueba" in events[2]["message"]["text"]
    return comm


async def finish(comm: ApplicationCommunicator) -> None:
    await comm.send_input({"type": "websocket.disconnect", "code": 1000})
    await comm.wait()


async def read_turn(comm: ApplicationCommunicator) -> list[dict[str, Any]]:
    events = []
    while True:
        event = await read_json(comm)
        events.append(event)
        if event.get("type") == "typing" and event.get("on") is False:
            return events


async def test_invalid_token_accepts_then_closes_4403(target: tuple[UUID, str]) -> None:
    comm = await socket(target[0], "false")
    await comm.send_input({"type": "websocket.connect"})
    assert (await comm.receive_output())["type"] == "websocket.accept"
    assert await comm.receive_output() == {"type": "websocket.close", "code": 4403}
    await finish(comm)


async def test_initial_greeting_and_message(target: tuple[UUID, str]) -> None:
    comm = await ready(target)
    await send_json(comm, {"type": "message", "client_message_id": str(uuid4()), "text": "Hola"})
    events = await read_turn(comm)
    assert any(
        e["type"] == "message" and "He recibido: Hola" in e["message"]["text"] for e in events
    )
    await send_json(comm, {"type": "ping"})
    assert await read_json(comm) == {"type": "pong"}
    await finish(comm)


async def test_voice_available_still_starts_chat_with_assistant(
    target: tuple[UUID, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from web.conversations import eleven_direct

    monkeypatch.setattr(eleven_direct, "enabled", AsyncMock(return_value=True))
    comm = await ready(target)
    session = await get_container().conversations.authorize(target[1], target[0])
    messages = await get_container().conversations.repo.list_messages(session.tenant_id, session.id)
    assert len(messages) == 1 and messages[0].direction == "out"
    await finish(comm)
    # Reloading an unanswered greeting must not introduce another opening.
    second = await socket(*target)
    await second.send_input({"type": "websocket.connect"})
    await second.receive_output()
    history = await read_json(second)
    assert [m["id"] for m in history["messages"]] == [str(messages[0].id)]
    assert await second.receive_nothing()
    await finish(second)


async def test_active_voice_does_not_start_a_competing_text_turn(target: tuple[UUID, str]) -> None:
    key = "live-call:" + str(target[0])
    await sync_to_async(cache.set)(key, True, 60)
    comm = await socket(*target)
    try:
        await comm.send_input({"type": "websocket.connect"})
        assert (await comm.receive_output())["type"] == "websocket.accept"
        assert (await read_json(comm))["messages"] == []
        assert await comm.receive_nothing()
        await send_json(
            comm, {"type": "message", "client_message_id": str(uuid4()), "text": "Hola"}
        )
        assert (await read_json(comm))["code"] == "voice_active"
    finally:
        await finish(comm)
        await sync_to_async(cache.delete)(key)
    # A new text connection may greet after the call has ended without a transcript.
    resumed = await ready(target)
    await finish(resumed)


async def test_duplicate_message_has_exactly_one_reply(target: tuple[UUID, str]) -> None:
    comm = await ready(target)
    payload = {"type": "message", "client_message_id": str(uuid4()), "text": "Hola"}
    await send_json(comm, payload)
    await send_json(comm, payload)
    events = [*await read_turn(comm), *await read_turn(comm)]
    assert sum(e["type"] == "message" for e in events) == 1
    assert await comm.receive_nothing()
    await finish(comm)


async def test_persona_pauses_ai_but_candidate_can_continue_with_human(
    target: tuple[UUID, str],
) -> None:
    comm = await ready(target)
    await send_json(comm, {"type": "message", "client_message_id": str(uuid4()), "text": "persona"})
    assert {"type": "status", "status": "handed_off"} in await read_turn(comm)
    await send_json(comm, {"type": "message", "client_message_id": str(uuid4()), "text": "Hola"})
    events = await read_turn(comm)
    assert any(e["type"] == "message" and e["message"]["text"] == "Hola" for e in events)
    assert not any(e["type"] == "message" and e["message"]["direction"] == "out" for e in events)
    await finish(comm)


async def test_invalid_payloads(target: tuple[UUID, str]) -> None:
    comm = await ready(target)
    for content in (
        [],
        {"type": "unknown"},
        {"type": "message", "text": " "},
        {"type": "message", "text": "x" * 2001, "client_message_id": str(uuid4())},
    ):
        await send_json(comm, content)
        assert (await read_json(comm))["type"] == "error"
    await finish(comm)


async def test_reconnect_returns_history_without_another_greeting(target: tuple[UUID, str]) -> None:
    first = await ready(target)
    await send_json(first, {"type": "message", "client_message_id": str(uuid4()), "text": "Hola"})
    await read_turn(first)
    await finish(first)
    second = await socket(*target)
    await second.send_input({"type": "websocket.connect"})
    assert (await second.receive_output())["type"] == "websocket.accept"
    history = await read_json(second)
    assert history["type"] == "history" and len(history["messages"]) == 3
    assert await second.receive_nothing()
    await finish(second)


async def test_untrusted_origin_is_rejected(target: tuple[UUID, str]) -> None:
    comm = await socket(*target, origin="https://other.example")
    await comm.send_input({"type": "websocket.connect"})
    assert (await comm.receive_output())["type"] == "websocket.close"
    comm.stop()
