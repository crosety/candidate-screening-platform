"""Native ElevenAgents protocol. Tool execution stays on our server, not the browser."""

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from websockets.asyncio.client import connect

Emit = Callable[[dict[str, Any]], Awaitable[None]]
Tool = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]
Transcript = Callable[[str, str, str], Awaitable[None]]


class ElevenDirectBridge:
    def __init__(self, emit: Emit, tool: Tool, transcript: Transcript) -> None:
        self.emit, self.tool, self.transcript = emit, tool, transcript
        self.outgoing: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(maxsize=100)
        self.finished = asyncio.Event()
        self.conversation_id = ""
        self.calls: dict[str, str] = {}
        self.tool_tasks: set[asyncio.Task[None]] = set()
        self.last_audio_at = 0.0
        self.last_response_at = 0.0
        self.playback_until = 0.0

    def final_response_finished(self, terminal_since: float, now: float) -> bool:
        return (
            self.last_response_at >= terminal_since
            and now > max(self.last_audio_at, self.playback_until) + 2
        )

    async def audio(self, pcm: str) -> None:
        # Backpressure is bounded; don't silently drop audio or accumulate indefinitely.
        self.outgoing.put_nowait({"user_audio_chunk": pcm})

    def stop(self) -> None:
        self.finished.set()

    async def run(
        self,
        url: str,
        context: str,
        opening: str,
        max_seconds: int = 300,
        *,
        channel_context: str = "",
    ) -> None:
        async with connect(url, max_size=2**20, open_timeout=15) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "conversation_initiation_client_data",
                        "dynamic_variables": {
                            "orbio_context": context,
                            "orbio_opening": opening,
                            "orbio_channel_context": channel_context,
                        },
                    }
                )
            )

            async def sender() -> None:
                while True:
                    item = await self.outgoing.get()
                    if item is None:
                        return
                    await ws.send(json.dumps(item))

            async def reader() -> None:
                async for raw in ws:
                    event = json.loads(raw)
                    kind = event.get("type")
                    if kind == "ping":
                        await self.outgoing.put(
                            {"type": "pong", "event_id": event["ping_event"]["event_id"]}
                        )
                    elif kind == "conversation_initiation_metadata":
                        meta = event["conversation_initiation_metadata_event"]
                        if any(
                            meta.get(k) != "pcm_16000"
                            for k in ("user_input_audio_format", "agent_output_audio_format")
                        ):
                            raise ValueError("Unsupported audio format")
                        self.conversation_id = meta["conversation_id"]
                        await self.emit({"type": "ready", "sample_rate": 16000})
                    elif kind == "audio":
                        self.last_audio_at = monotonic()
                        duration = (
                            len(
                                base64.b64decode(
                                    event["audio_event"]["audio_base_64"], validate=True
                                )
                            )
                            / 32000
                        )
                        self.playback_until = (
                            max(self.playback_until, self.last_audio_at) + duration
                        )
                        await self.emit(
                            {"type": "audio", "delta": event["audio_event"]["audio_base_64"]}
                        )
                    elif kind == "interruption":
                        self.playback_until = monotonic()
                        await self.emit({"type": "user_speaking"})
                    elif kind == "user_transcript":
                        data = event["user_transcription_event"]
                        await self.transcript(
                            "in", data["user_transcript"], str(data.get("event_id", ""))
                        )
                    elif kind == "agent_response":
                        self.last_response_at = monotonic()
                        data = event["agent_response_event"]
                        await self.transcript(
                            "out", data["agent_response"], str(data.get("event_id", ""))
                        )
                    elif kind == "client_tool_call":
                        task = asyncio.create_task(self._tool(event["client_tool_call"]))
                        self.tool_tasks.add(task)
                        task.add_done_callback(self.tool_tasks.discard)
                    elif kind in ("error", "client_error"):
                        raise ValueError("Provider error")

            tasks = [
                asyncio.create_task(sender()),
                asyncio.create_task(reader()),
                asyncio.create_task(self.finished.wait()),
            ]
            try:
                done, _ = await asyncio.wait(
                    tasks, timeout=max_seconds, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    task.result()
            finally:
                # Finish already authorized writes before releasing session ownership.
                if self.tool_tasks:
                    await asyncio.gather(*self.tool_tasks, return_exceptions=True)
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _tool(self, call: dict[str, Any]) -> None:
        identity = str(call["tool_call_id"])
        if identity in self.calls:
            return
        self.calls[identity] = "in_progress"
        await self.emit({"type": "backend", "busy": True})
        try:
            result = await self.tool(identity, str(call["tool_name"]), call.get("parameters", {}))
        except Exception:
            result = {
                "error": "tool_unavailable",
                "next_step": "Ofrece ayuda humana; no afirmes que se ejecutó.",
            }
        serialized = json.dumps(result, ensure_ascii=False)
        self.calls[identity] = serialized
        try:
            await asyncio.wait_for(
                self.outgoing.put(
                    {
                        "type": "client_tool_result",
                        "tool_call_id": identity,
                        "result": serialized,
                        "is_error": "error" in result,
                    }
                ),
                2,
            )
        except TimeoutError:
            await self.emit(
                {
                    "type": "error",
                    "message": "La conexión de voz se ha detenido. Puedes continuar por escrito.",
                }
            )
            self.stop()
        await self.emit({"type": "backend", "busy": False})
