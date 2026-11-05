"""ElevenLabs Speech Engine upstream, using the existing Channels transport.

Only authenticated transcripts enter the ordinary, guarded conversation service.
The provider owns STT/TTS, not prompts, tools, history or screening decisions.
"""

import asyncio
import logging
import re
from contextlib import suppress
from typing import Any
from uuid import UUID

from channels.generic.websocket import AsyncJsonWebsocketConsumer
from channels.layers import get_channel_layer
from django.core import signing

from adapters.channels.eleven import verify_speech_engine_jwt
from screening.domain.errors import DomainError
from screening.domain.voice import sentences
from screening.platform.container import get_container
from web.conversations.voice_api import SALT, configuration, guarded_reply, validate_turn

logger = logging.getLogger(__name__)
# Keep a persisted turn alive across transport interruptions; never duplicate inference.
_turns: dict[tuple[UUID, UUID, str], asyncio.Task[str]] = {}


class SpeechEngineConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self) -> None:
        self.reply_task: asyncio.Task[None] | None = None
        self.conversation_id = ""
        self.last_event = -1
        try:
            headers = {key.lower(): value for key, value in self.scope.get("headers", [])}
            envelope = headers.get(b"x-orbio-session", b"").decode()
            tenant, session_id, version = signing.loads(envelope, salt=SALT, max_age=900)
            self.tenant_id, self.session_id = UUID(tenant), UUID(session_id)
            if self.scope["path"] != f"/api/v1/voice/{tenant}/v1/chat/completions":
                raise ValueError("Wrong tenant")
            service = get_container().conversations
            session = await service.repo.get_session(self.tenant_id, self.session_id)
            if str(session.profile_version_id) != version or session.status != "active":
                raise ValueError("Invalid session")
            config = await configuration(self.tenant_id, session.profile_version_id)
            jwt = headers.get(b"x-elevenlabs-speech-engine-authorization", b"").decode()
            if not config.agent_id.startswith("seng_") or not verify_speech_engine_jwt(
                jwt, config.api_key.get_secret_value()
            ):
                raise ValueError("Invalid provider signature")
        except (ValueError, TypeError, KeyError, signing.BadSignature, DomainError):
            await self.close(code=4401)
            return
        await self.accept()

    async def receive(self, text_data: str | None = None, bytes_data: bytes | None = None) -> None:
        if text_data is None or len(text_data) > 256_000:
            await self.close(code=1008)
            return
        try:
            await super().receive(text_data=text_data)
        except (ValueError, TypeError, KeyError):
            await self.close(code=1008)

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        if not isinstance(content, dict):
            raise ValueError("Invalid message")
        kind = content.get("type")
        if kind == "ping":
            await self.send_json({"type": "pong"})
        elif kind == "init":
            identifier = content.get("conversation_id", "")
            if self.conversation_id or not re.fullmatch(r"conv_[a-zA-Z0-9_-]{1,128}", identifier):
                raise ValueError("Invalid conversation")
            self.conversation_id = identifier
        elif kind == "user_transcript":
            event_id = content.get("event_id")
            messages = content.get("user_transcript")
            if (
                not self.conversation_id
                or type(event_id) is not int
                or event_id < 0
                or not isinstance(messages, list)
            ):
                raise ValueError("Invalid turn")
            # The client/provider cannot supply privileged history to the model.
            key, text = validate_turn(self.session_id, messages, self.conversation_id)
            if event_id <= self.last_event:
                return
            # ElevenLabs can synthesize a "..." turn after silence. That is not
            # a candidate utterance and must not create a message or call the LLM.
            if not text.strip(" .…\t\r\n"):
                await self.send_json(
                    {
                        "type": "agent_response",
                        "event_id": event_id,
                        "content": "",
                        "is_final": True,
                    }
                )
                return
            self.last_event = event_id
            # Speech Engine can delay its browser transcript until TTS starts.
            # Signal our authenticated chat immediately, before inference, so
            # capture pauses rather than generating interruptions while waiting.
            await self.notify_processing(True)
            if self.reply_task:
                self.reply_task.cancel()
            self.reply_task = asyncio.create_task(self.respond(event_id, messages, key))
        elif kind in {"close", "error"}:
            await self.close(code=1000 if kind == "close" else 1011)
        else:
            raise ValueError("Unsupported message")

    async def notify_processing(self, processing: bool) -> None:
        layer = get_channel_layer()
        if layer is not None:
            await layer.group_send(
                f"chat_{self.session_id}",
                {
                    "type": "chat.event",
                    "payload": {"type": "typing", "on": processing},
                },
            )

    async def respond(self, event_id: int, messages: list[dict[str, Any]], key: str) -> None:
        identity = self.tenant_id, self.session_id, key
        task = _turns.get(identity)
        if task is None:
            task = asyncio.create_task(
                guarded_reply(self.tenant_id, self.session_id, messages, self.conversation_id)
            )
            _turns[identity] = task

            def finished(done: asyncio.Task[str]) -> None:
                _turns.pop(identity, None)
                if not done.cancelled() and (error := done.exception()) is not None:
                    logger.warning("Speech Engine turn failed: %s", type(error).__name__)

            task.add_done_callback(finished)
        try:
            reply = await asyncio.wait_for(asyncio.shield(task), timeout=85)
            if event_id != self.last_event:
                return
            # No filler, spaces or unguarded partial generations. The WebSocket
            # responds to pings while the authoritative service prepares its answer.
            for sentence in sentences(reply):
                await self.send_json(
                    {
                        "type": "agent_response",
                        "event_id": event_id,
                        "content": sentence + " ",
                        "is_final": False,
                    }
                )
            await self.send_json(
                {"type": "agent_response", "event_id": event_id, "content": "", "is_final": True}
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("Speech Engine response failed: %s", type(error).__name__)
            await self.close(code=1011)
        finally:
            if event_id == self.last_event:
                await self.notify_processing(False)

    async def disconnect(self, close_code: int) -> None:
        if self.reply_task:
            self.reply_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.reply_task
