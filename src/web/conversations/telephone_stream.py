"""Twilio Media Streams transport using the same guarded voice runtime as the web."""

import asyncio
import json
import logging
import re
from time import monotonic
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.core.cache import cache
from django.utils import timezone

from adapters.channels.twilio import valid_twilio_signature
from adapters.channels.twilio_voice import SPANISH_MOBILE, TelephoneCodec, TwilioVoiceClient
from screening.application.ports import MessageRecord
from screening.domain.errors import NotAllowed
from screening.platform.container import get_container
from screening.platform.voice_runtime import VoiceRuntime
from web.conversations.consumers import valid_socket_link
from web.conversations.eleven_direct import ElevenDirectConsumer, enabled, human_owns, voice_opening
from web.conversations.models import TelephoneAttempt
from web.conversations.telephone import FINAL, configuration, live_session
from web.tenancy.context import tenant_context

logger = logging.getLogger(__name__)


class TelephoneConsumer(ElevenDirectConsumer):
    async def opening(self, history: list[MessageRecord]) -> str:
        # A new phone call needs its own identity and purpose, even after web chat.
        service = self.c.conversations
        tenant = await service.repo.get_tenant(self.session.tenant_id)
        version = await service.versions.get(
            self.session.tenant_id, self.session.profile_version_id
        )
        offer = next(
            (o for o in version.content.offers if o.key == self.session.run_context.offer_key),
            None,
        )
        return voice_opening(history, tenant.name, offer.title if offer else "", telephone=True)

    async def connect(self) -> None:
        self.bridge, self.task, self.lease = None, None, ""
        self.disconnected = False
        self.stream_id = ""
        self.marks: set[str] = set()
        self.serial = 0
        self.sequence = 0
        self.started = monotonic()
        self.codec = TelephoneCodec()
        self.c = get_container()
        self.handshake_timer: asyncio.Task[None] | None = None
        stage = "configuration"
        try:
            tenant = UUID(self.scope["url_route"]["kwargs"]["tenant_id"])
            self.attempt_id = UUID(self.scope["url_route"]["kwargs"]["attempt_id"])
            self.config = await sync_to_async(configuration)(tenant)
            if self.scope.get("query_string"):
                raise NotAllowed("Query no admitida")
            signature = dict(self.scope.get("headers", [])).get(b"x-twilio-signature", b"").decode()
            url = self.config.callback(self.attempt_id, "media")
            stage = "signature"
            # Twilio documents a trailing-slash normalization in WSS signatures.
            if not any(
                valid_twilio_signature(u, {}, signature, self.config.auth_token.get_secret_value())
                for u in (url, url + "/")
            ):
                raise NotAllowed("Firma de stream no válida")
            stage = "bound_call"
            self.attempt = await self.load_attempt(tenant)
            self.session = await self.c.conversations.repo.get_session(
                tenant, self.attempt.session_id
            )
            self.digest = self.attempt.link_digest
            stage = "policy"
            if not await self.authorized() or not await enabled(self.session, telephone=True):
                raise NotAllowed("Llamada no autorizada")
            self.runtime = VoiceRuntime(self.session, transport="telephone")
        except Exception as exc:
            # Diagnostic categories only; never emit headers, signatures or contact data.
            logger.warning("Telephone handshake rejected: %s (%s)", stage, type(exc).__name__)
            await self.close(code=4403)
            return
        await self.accept()
        self.handshake_timer = asyncio.create_task(self.start_deadline())

    @sync_to_async
    def load_attempt(self, tenant: UUID) -> TelephoneAttempt:
        with tenant_context(tenant):
            row = TelephoneAttempt.objects.select_related("session__application").get(
                pk=self.attempt_id
            )
            if (
                not row.provider_id
                or row.state != "in-progress"
                or row.stream_used_at
                or not live_session(row.session, row.link_digest)
                or row.expires_at <= timezone.now()
                or row.session.profile_version_id != row.profile_version_id
            ):
                raise NotAllowed("Intento de llamada no válido")
            return row

    async def start_deadline(self) -> None:
        await asyncio.sleep(10)
        if not self.stream_id:
            await self.close(code=4408)

    @sync_to_async
    def consume_stream(self, start: dict[str, Any]) -> None:
        with tenant_context(self.session.tenant_id):
            row = TelephoneAttempt.objects.select_for_update().get(pk=self.attempt_id)
            stream = start.get("streamSid", "")
            if (
                row.stream_used_at
                or row.state in FINAL
                or start.get("callSid") != row.provider_id
                or start.get("accountSid") != self.config.account_sid
                or not re.fullmatch(r"MZ[0-9a-fA-F]{32}", stream)
                or start.get("tracks") != ["inbound"]
                or start.get("mediaFormat")
                != {"encoding": "audio/x-mulaw", "sampleRate": 8000, "channels": 1}
            ):
                raise NotAllowed("Stream no válido o ya utilizado")
            row.stream_used_at, row.stream_id = timezone.now(), stream
            row.save(update_fields=["stream_used_at", "stream_id", "updated_at"])
            self.stream_id = stream

    async def receive(
        self, text_data: str | None = None, bytes_data: bytes | None = None, **kwargs: Any
    ) -> None:
        try:
            if bytes_data is not None or not text_data or len(text_data) > 24000:
                raise ValueError
            event = json.loads(text_data)
            kind = event.get("event")
            if kind == "connected" and not self.stream_id:
                if event.get("protocol") != "Call" or event.get("version") != "1.0.0":
                    raise ValueError
                return
            sequence = int(event.get("sequenceNumber", "0"))
            if sequence <= self.sequence:
                raise ValueError
            self.sequence = sequence
            if kind == "start" and not self.stream_id:
                await self.consume_stream(event["start"])
                if event.get("streamSid") != self.stream_id:
                    raise ValueError
                self.task = asyncio.create_task(self.run_voice())
                return
            if not self.stream_id or event.get("streamSid") != self.stream_id:
                raise ValueError
            if kind == "media":
                if event["media"].get("track") != "inbound":
                    raise ValueError
                pcm = self.codec.to_agent(event["media"]["payload"])
                if self.bridge:
                    await self.bridge.audio(pcm)
            elif kind == "mark":
                self.marks.discard(event["mark"]["name"])
            elif kind == "stop":
                if self.bridge:
                    self.bridge.stop()
            elif kind != "dtmf":
                raise ValueError
        except (ValueError, KeyError, TypeError, NotAllowed, asyncio.QueueFull):
            if self.bridge:
                self.bridge.stop()
            await self.close(code=4400)

    async def emit(self, event: dict[str, Any]) -> None:
        if self.disconnected or not self.stream_id:
            return
        if event.get("type") == "audio":
            self.serial += 1
            mark = str(self.serial)
            if len(self.marks) > 1000:
                raise ValueError("Playback backpressure")
            self.marks.add(mark)
            await self.send_json(
                {
                    "event": "media",
                    "streamSid": self.stream_id,
                    "media": {"payload": self.codec.to_phone(event["delta"])},
                }
            )
            await self.send_json(
                {"event": "mark", "streamSid": self.stream_id, "mark": {"name": mark}}
            )
        elif event.get("type") == "user_speaking":
            self.marks.clear()
            await self.send_json({"event": "clear", "streamSid": self.stream_id})

    async def authorized(self) -> bool:
        return (
            await self.transport_authorized()
            and await super().authorized()
            and not await human_owns(self.session)
        )

    async def transport_authorized(self) -> bool:
        try:
            current = await sync_to_async(configuration)(self.session.tenant_id)
        except NotAllowed:
            return False
        return current.public_base_url == self.config.public_base_url and bool(
            SPANISH_MOBILE.fullmatch(self.attempt.contact)
        )

    async def watch(self) -> None:
        terminal: float | None = None
        while self.bridge and not self.bridge.finished.is_set():
            if (
                not await self.transport_authorized()
                or monotonic() - self.started >= self.config.max_seconds
                or await human_owns(self.session)
                or not await valid_socket_link(
                    self.digest, self.session.tenant_id, self.session.application_id
                )
            ):
                await self.send_json({"event": "clear", "streamSid": self.stream_id})
                self.bridge.stop()
                return
            if getattr(self.runtime, "pause_requested", False) or not await self.authorized():
                terminal = terminal or monotonic()
                if (
                    not self.marks and self.bridge.final_response_finished(terminal, monotonic())
                ) or monotonic() - terminal > 30:
                    self.bridge.stop()
                    return
            await asyncio.sleep(0.5)

    async def release(self) -> None:
        if self.handshake_timer:
            self.handshake_timer.cancel()
        if getattr(self, "stream_id", ""):
            # Closing the stream also reaches TwiML Hangup. Explicit stop is a second fence.
            try:
                stopped = await TwilioVoiceClient(self.config).stop(self.attempt.provider_id)
            except Exception:
                stopped = False
            await self.c.conversations.repo.add_event(
                self.session.tenant_id,
                self.session.id,
                "telephone_stream_ended",
                {"attempt_id": str(self.attempt_id), "stop_confirmed": stopped},
            )
            self.stream_id = ""
        if hasattr(self, "session"):
            key = "live-call:" + str(self.session.id)
            if await sync_to_async(cache.get)(key) == str(self.attempt_id):
                await sync_to_async(cache.delete)(key)
