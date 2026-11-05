import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from websockets.asyncio.server import serve

from adapters.agent_langchain.tool_guard import JevToolGuard
from adapters.channels.eleven_direct import ElevenDirectBridge
from adapters.fakes.guard import ScriptedReplyGuard
from adapters.fakes.tracing import InMemoryTraceSink
from screening.application.ports import GuardVerdict
from tests.contracts.test_jev_guard import context, response_data


@pytest.mark.parametrize("probability,approved", [(0.01, True), (0.30, False), (0.95, False)])
async def test_tools_evaluate_all_rules_and_escalate(
    tmp_path: Path, probability: float, approved: bool
) -> None:
    key = tmp_path / "key"
    key.write_text("synthetic-key")
    data = response_data(probability)
    data["answers"] = {
        k: {"type": "boolean", "probability": probability} for k in JevToolGuard.rules
    }
    sink = InMemoryTraceSink()
    fallback = ScriptedReplyGuard([GuardVerdict(approved=False, reason="Do not execute")])
    guard = JevToolGuard(
        str(key),
        fallback,
        sink,
        synthetic_only=True,
        is_synthetic=AsyncMock(return_value=True),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=data)),
    )
    result = await guard.review(
        context(), '{"tool":"interview_book","arguments":{"slot_id":"x"}}', "policy"
    )
    assert result.approved is approved
    assert sink.capability_calls[0].capability == "platform.tool_guard.jev"


async def test_protocol_audio_transcripts_tool_gate_and_duplicate_call() -> None:
    events = []
    transcript = AsyncMock()
    execute = AsyncMock(return_value={"valid": True})
    returned = asyncio.Event()

    async def emit(event: dict) -> None:
        events.append(event)

    b = ElevenDirectBridge(emit, execute, transcript)

    async def upstream(ws: object) -> None:
        initial = json.loads(await ws.recv())
        assert initial["dynamic_variables"]["orbio_context"] == "history"
        await ws.send(
            json.dumps(
                {
                    "type": "conversation_initiation_metadata",
                    "conversation_initiation_metadata_event": {
                        "conversation_id": "synthetic",
                        "user_input_audio_format": "pcm_16000",
                        "agent_output_audio_format": "pcm_16000",
                    },
                }
            )
        )
        await ws.send(
            json.dumps(
                {
                    "type": "user_transcript",
                    "user_transcription_event": {"user_transcript": "Hola", "event_id": 1},
                }
            )
        )
        await ws.send(json.dumps({"type": "audio", "audio_event": {"audio_base_64": "AQACAA=="}}))
        call = {
            "type": "client_tool_call",
            "client_tool_call": {
                "tool_name": "credential_verify",
                "tool_call_id": "call1",
                "parameters": {"number": "CERT-1"},
            },
        }
        await ws.send(json.dumps(call))
        await ws.send(json.dumps(call))
        response = json.loads(await ws.recv())
        assert response["type"] == "client_tool_result" and response["is_error"] is False
        returned.set()

    async with serve(upstream, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await b.run(f"ws://127.0.0.1:{port}", "history", "Hello", max_seconds=3)
    assert returned.is_set()
    execute.assert_awaited_once_with("call1", "credential_verify", {"number": "CERT-1"})
    transcript.assert_awaited_once_with("in", "Hola", "1")
    assert {"type": "audio", "delta": "AQACAA=="} in events


async def test_tool_failure_is_reported_without_success_and_reader_stays_available():
    emit = AsyncMock()
    bridge = ElevenDirectBridge(emit, AsyncMock(side_effect=RuntimeError("private")), AsyncMock())
    await bridge._tool({"tool_call_id": "failure", "tool_name": "openings_find", "parameters": {}})
    result = await bridge.outgoing.get()
    assert result["is_error"] is True
    assert "private" not in result["result"]
    assert "tool_unavailable" in result["result"]
    assert not bridge.finished.is_set()
    bridge.tool = AsyncMock(return_value={"status": "handed_off"})
    await bridge._tool({"tool_call_id": "human", "tool_name": "handoff_request", "parameters": {}})
    result = await bridge.outgoing.get()
    assert result["is_error"] is False and "handed_off" in result["result"]


def test_terminal_close_waits_for_buffered_audio_not_just_last_packet():
    bridge = ElevenDirectBridge(AsyncMock(), AsyncMock(), AsyncMock())
    bridge.last_response_at = 101
    bridge.last_audio_at = 102
    bridge.playback_until = 116
    assert not bridge.final_response_finished(100, 105)
    assert not bridge.final_response_finished(100, 117)
    assert bridge.final_response_finished(100, 119)
    assert not bridge.final_response_finished(110, 119)
