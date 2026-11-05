import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from django.http import StreamingHttpResponse

from web.conversations import voice_api


@pytest.mark.asyncio
async def test_stream_starts_before_inference_and_retries_share_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = asyncio.Event()

    async def slow(*args: object) -> StreamingHttpResponse:
        await ready.wait()
        return StreamingHttpResponse(voice_api.events("Respuesta revisada."))

    complete = AsyncMock(side_effect=slow)
    monkeypatch.setattr(voice_api, "complete", complete)
    monkeypatch.setattr(voice_api, "KEEPALIVE_SECONDS", 0.01)
    tenant, session = uuid4(), uuid4()
    messages = [{"role": "user", "content": "Nueva intervención"}]
    first = voice_api.stream_complete(tenant, session, messages)
    second = voice_api.stream_complete(tenant, session, messages)
    first_stream, second_stream = first.__aiter__(), second.__aiter__()
    assert voice_api.WAITING_NOTICE.encode() in await anext(first_stream)
    assert voice_api.WAITING_NOTICE.encode() in await anext(second_stream)
    assert b'"content": " "' in await anext(first_stream)
    assert complete.await_count == 1
    ready.set()
    left = b"".join([chunk async for chunk in first_stream])
    right = b"".join([chunk async for chunk in second_stream])
    assert b"Respuesta revisada" in left and b"Respuesta revisada" in right
    assert left.endswith(b"data: [DONE]\n\n")
    assert not voice_api._inflight


@pytest.mark.asyncio
async def test_stream_failure_is_private_and_terminates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        voice_api, "complete", AsyncMock(side_effect=ValueError("private-provider-secret"))
    )
    response = voice_api.stream_complete(uuid4(), uuid4(), [{"role": "user", "content": "Hola"}])
    output = b"".join([chunk async for chunk in response])
    assert b"voice_turn_failed" in output
    assert b"private-provider-secret" not in output
    assert output.endswith(b"data: [DONE]\n\n")
    assert not voice_api._inflight


@pytest.mark.asyncio
async def test_disconnected_stream_does_not_cancel_persisting_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready, persisted = asyncio.Event(), asyncio.Event()

    async def slow(*args: object) -> StreamingHttpResponse:
        await ready.wait()
        persisted.set()
        return StreamingHttpResponse(voice_api.events("Guardada."))

    monkeypatch.setattr(voice_api, "complete", slow)
    response = voice_api.stream_complete(uuid4(), uuid4(), [{"role": "user", "content": "Hola"}])
    stream = response.streaming_content
    await anext(stream)
    await stream.aclose()
    assert len(voice_api._inflight) == 1
    pending = list(voice_api._inflight.values())
    ready.set()
    await asyncio.gather(*pending)
    await asyncio.sleep(0)
    assert persisted.is_set()
    assert not voice_api._inflight


@pytest.mark.parametrize("messages", [[], "invalid", [None], [{"role": "user", "content": 2}]])
def test_invalid_turn_is_rejected_before_streaming(messages: object) -> None:
    with pytest.raises(ValueError):
        voice_api.validate_turn(uuid4(), messages)


def test_provider_buffer_does_not_change_turn_identity() -> None:
    session = uuid4()
    incoming = [{"role": "user", "content": "Hola"}]
    original, _ = voice_api.validate_turn(session, incoming)
    retry, _ = voice_api.validate_turn(
        session, [*incoming, {"role": "assistant", "content": voice_api.WAITING_NOTICE}]
    )
    next_turn, _ = voice_api.validate_turn(session, [*incoming, *incoming])
    assert original == retry
    assert original != next_turn
