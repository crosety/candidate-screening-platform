"""Candidate-authenticated bridge. No public callback or browser API key."""

import asyncio
import base64
import binascii
import hashlib
import json
import secrets
from contextlib import suppress
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.core.cache import cache
from openai.types.live.initial_item_param import InitialItemParam

from adapters.channels.gpt_live import GptLiveBridge
from screening.application.ports import LlmUsage, SessionRecord
from screening.domain.errors import DomainError, NotAllowed
from screening.platform.container import get_container
from web.conversations.consumers import valid_socket_link
from web.conversations.links import digest_token
from web.core.security import limited


async def enabled(session: SessionRecord) -> bool:
    if not settings.GPT_LIVE_ENABLED:
        return False
    c = get_container()
    app = await c.conversations.repo.get_application(session.tenant_id, session.application_id)
    version = await c.conversations.versions.get(session.tenant_id, session.profile_version_id)
    # The local opt-in does not publish a profile or enable voice on other tenants.
    return app.is_test and "voice" in version.content.channels


def issue_ticket(session: SessionRecord, token: str, transport: str = "gpt_live") -> str:
    ticket = secrets.token_urlsafe(32)
    cache.set(
        "live-ticket:" + hashlib.sha256(ticket.encode()).hexdigest(),
        {
            "tenant": str(session.tenant_id),
            "session": str(session.id),
            "version": str(session.profile_version_id),
            "digest": digest_token(token),
            "transport": transport,
        },
        60,
    )
    return ticket


def consume_ticket(ticket: str) -> dict[str, str]:
    key = "live-ticket:" + hashlib.sha256(ticket.encode()).hexdigest()
    if not cache.add(key + ":used", True, 60):
        raise NotAllowed("Ticket usado")
    result = cache.get(key)
    cache.delete(key)
    if not isinstance(result, dict):
        raise NotAllowed("Ticket no válido")
    return result


class GptLiveConsumer(AsyncJsonWebsocketConsumer):
    async def receive(
        self, text_data: str | None = None, bytes_data: bytes | None = None, **kwargs: Any
    ) -> None:
        try:
            if bytes_data is not None or text_data is None or len(text_data) > 25000:
                raise ValueError
            await self.receive_json(json.loads(text_data))
        except ValueError:
            if self.bridge:
                self.bridge.stop()
            await self.close(code=4400)

    async def connect(self) -> None:
        self.bridge: GptLiveBridge | None = None
        self.task: asyncio.Task[None] | None = None
        self.lease = ""
        self.disconnected = False
        self.service = get_container().conversations
        try:
            self.session_id = UUID(self.scope["url_route"]["kwargs"]["session_id"])
            ticket = parse_qs(self.scope["query_string"].decode()).get("ticket", [""])[0]
            payload = await sync_to_async(consume_ticket)(ticket)
            if payload.get("transport") != "gpt_live":
                raise NotAllowed("Transporte no válido")
            self.session = await self.service.repo.get_session(
                UUID(payload["tenant"]), self.session_id
            )
            if payload["session"] != str(self.session_id) or payload["version"] != str(
                self.session.profile_version_id
            ):
                raise NotAllowed("Ámbito no válido")
            self.digest = payload["digest"]
            if not await self._authorized() or not await enabled(self.session):
                raise NotAllowed("Voz no disponible")
            lease = "live-call:" + str(self.session_id)
            if not await sync_to_async(cache.add)(lease, True, 240):
                await self._limit("Ya hay una llamada activa para esta conversación.")
                return
            self.lease = lease
            if await sync_to_async(limited)("live_daily", str(self.session.tenant_id), 5, 86400):
                await self._limit(
                    "Se ha alcanzado el límite local de cinco pruebas de voz en 24 horas."
                )
                await self._release()
                return
        except (DomainError, KeyError, ValueError, UnicodeError):
            await self.close(code=4403)
            return
        await self.accept()
        self.task = asyncio.create_task(self._run())

    async def _limit(self, message: str) -> None:
        # This occurs only after candidate authorization. Accept to deliver the
        # concrete local limit instead of an opaque HTTP handshake failure.
        await self.accept()
        await self.send_json({"type": "error", "message": message})
        await self.close(code=4429)

    async def _authorized(self) -> bool:
        return self.session.status == "active" and await valid_socket_link(
            self.digest, self.session.tenant_id, self.session.application_id
        )

    async def _emit(self, event: dict[str, Any]) -> None:
        if not self.disconnected:
            await self.send_json(event)

    async def _record(self, kind: str, data: dict[str, Any]) -> None:
        await self.service.repo.add_event(self.session.tenant_id, self.session_id, kind, data)
        if kind == "gpt_live_usage":
            await get_container().sink.record_llm_call(
                LlmUsage(
                    self.session.tenant_id,
                    self.session_id,
                    None,
                    "voice",
                    data["model"],
                    "live",
                    0,
                    0,
                    0,
                    0,
                    data["cost_usd_micros"],
                    data["elapsed_ms"],
                    "finalized" if data["finalized"] else "usage_incomplete",
                )
            )

    async def _delegate(self, text: str, identity: str) -> str:
        from web.conversations.voice_api import guarded_reply

        if not await self._authorized():
            raise NotAllowed("Enlace revocado")
        # The ordinary conversation service owns all execution and output guards.
        return await guarded_reply(
            self.session.tenant_id,
            self.session_id,
            [{"role": "user", "content": text}],
            "gpt-live:" + identity,
        )

    async def _run(self) -> None:
        try:
            # Idempotent under the canonical turn lock, including a chat/voice race.
            # A first call must have a guarded interview question, not invent one.
            await self.service.start(self.session.tenant_id, self.session_id, channel="voice")
            messages = await self.service.repo.list_messages(
                self.session.tenant_id, self.session_id
            )
            history: list[InitialItemParam] = []
            # Speech context is bounded; the backend keeps its complete canonical history.
            for message in messages[-12:]:
                text = message.text[:2000]
                if message.direction == "in":
                    history.append(
                        {"role": "user", "content": [{"type": "input_text", "text": text}]}
                    )
                else:
                    history.append(
                        {"role": "assistant", "content": [{"type": "output_text", "text": text}]}
                    )
            if self.disconnected:
                return
            self.bridge = GptLiveBridge(
                self._emit, self._delegate, self._record, voice_id=settings.GPT_LIVE_VOICE_ID
            )
            await self.bridge.run(history)
            if not self.disconnected:
                await self._emit({"type": "closed", "reason": self.bridge.close_reason})
        except Exception:
            # No raw exception, URL, credentials or provider payload reaches UI/logs.
            await self._emit(
                {
                    "type": "error",
                    "message": "No se pudo mantener la voz. Puedes continuar por escrito.",
                }
            )
        finally:
            await self._release()
            if not self.disconnected:
                await self.close(code=1000)

    async def receive_json(self, content: Any, **kwargs: Any) -> None:
        if not isinstance(content, dict) or content.get("type") not in {"audio", "stop", "ping"}:
            await self.close(code=4400)
            return
        if content["type"] == "stop":
            if self.bridge:
                self.bridge.stop()
            return
        if not await self._authorized():
            if self.bridge:
                self.bridge.stop()
            await self.close(code=4403)
            return
        if content["type"] == "ping":
            await self._emit({"type": "pong"})
            return
        try:
            chunk = content.get("audio")
            if not isinstance(chunk, str) or len(chunk) > 24000:
                raise ValueError
            pcm = base64.b64decode(chunk, validate=True)
            if not 2 <= len(pcm) <= 16000 or len(pcm) % 2:
                raise ValueError
            if await sync_to_async(limited)("live_audio", str(self.session_id), 800, 60):
                raise ValueError
            if self.bridge:
                await self.bridge.audio(chunk)
        except (ValueError, binascii.Error):
            if self.bridge:
                self.bridge.stop()
            await self.close(code=4400)

    async def _release(self) -> None:
        lease, self.lease = self.lease, ""
        if lease:
            await sync_to_async(cache.delete)(lease)

    async def disconnect(self, code: int) -> None:
        self.disconnected = True
        if self.bridge:
            self.bridge.stop()
        if self.task:
            with suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(self.task), 12)
        await self._release()
