"""Private server-to-server voice. The browser can send audio, never tool results."""

import asyncio
import base64
import hashlib
import json
import logging
import re
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import httpx
from asgiref.sync import sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.conf import settings
from django.core.cache import cache

from adapters.channels.eleven_direct import ElevenDirectBridge
from screening.application.ports import MessageRecord, SessionRecord
from screening.domain.errors import NotAllowed
from screening.platform.container import get_container
from screening.platform.voice_runtime import (
    DIRECT_MODEL,
    VoiceRuntime,
    agent_registry_key,
    policy_hash,
    tool_specs,
    voice_prompt,
)
from web.conversations.consumers import message_payload, valid_socket_link
from web.conversations.gpt_live import consume_ticket
from web.conversations.models import Session
from web.core.security import limited
from web.tenancy.context import tenant_context


def daily_limit_reached() -> bool:
    # Read the existing fixed-window counter without spending another attempt.
    # The atomic admission check below still enforces simultaneous requests.
    key = "limit:eleven_direct_daily:" + hashlib.sha256(b"global").hexdigest()
    return int(cache.get(key) or 0) >= settings.ELEVEN_DIRECT_DAILY_LIMIT


def daily_limit_message() -> str:
    return (
        f"Se ha alcanzado el límite local de {settings.ELEVEN_DIRECT_DAILY_LIMIT} "
        "llamadas de prueba en 24 horas. No es el saldo de tu plan de ElevenLabs. "
        "Reintentar ahora no lo resuelve; puedes continuar por escrito."
    )


def voice_opening(
    history: list[MessageRecord],
    company: str = "el equipo de selección",
    offer: str = "",
    *,
    telephone: bool = False,
) -> str:
    identity = f"Hola, soy el asistente de inteligencia artificial de {company}. "
    purpose = f"tu candidatura a {offer}" if offer else "tu candidatura"
    if telephone:
        resume = " Tengo lo que ya hemos hablado en la conversación. " if history else " "
        return (
            identity + f"Te llamo por {purpose}." + resume + "¿Te viene bien hablar unos minutos?"
        )
    intro = identity + f"Ahora hablamos por voz sobre {purpose}. "
    if not history:
        return intro + "Para empezar, ¿me dices tu nombre y apellidos?"
    # Resume the pending question, never replay a legacy greeting or its invitations.
    if history[-1].direction == "out":
        question = re.search(r"¿[^?]{1,500}\?", history[-1].text)
        if question:
            return intro + "Retomamos donde lo dejamos. " + question.group()
    return intro + "Tengo tu última respuesta y lo que ya hemos hablado. Continuamos desde ahí."


@sync_to_async
def human_owns(session: SessionRecord) -> bool:
    with tenant_context(session.tenant_id):
        return Session.objects.filter(pk=session.id, human_owner__isnull=False).exists()


async def enabled(session: SessionRecord, *, telephone: bool = False) -> bool:
    if not settings.ELEVEN_DIRECT_AGENTS_FILE:
        return False
    try:
        registry = json.loads(
            await asyncio.to_thread(Path(settings.ELEVEN_DIRECT_AGENTS_FILE).read_text)
        )
        if agent_registry_key(session) not in registry:
            return False
    except (OSError, ValueError):
        return False
    c = get_container()
    app = await c.conversations.repo.get_application(session.tenant_id, session.application_id)
    version = await c.conversations.versions.get(session.tenant_id, session.profile_version_id)
    # Telephone admission has its own consent, destination and durable budget policy.
    # Do not widen browser-voice admission as a side effect of that policy.
    return (telephone or app.is_test) and "voice" in version.content.channels


async def signed_url(session: SessionRecord) -> tuple[str, dict[str, Any]]:
    registry = json.loads(
        await asyncio.to_thread(Path(settings.ELEVEN_DIRECT_AGENTS_FILE).read_text)
    )
    record = registry[agent_registry_key(session)]
    if record["hash"] != policy_hash(session):
        raise NotAllowed("Reaprovisiona la política de voz")
    api_key = (await asyncio.to_thread(Path(settings.ELEVENLABS_API_KEY_FILE).read_text)).strip()
    async with httpx.AsyncClient(
        base_url="https://api.elevenlabs.io",
        headers={"xi-api-key": api_key},
        timeout=20,
    ) as client:
        check = await client.get(f"/v1/convai/agents/{record['agent_id']}")
        check.raise_for_status()
        data = check.json()
        app = await get_container().conversations.repo.get_application(
            session.tenant_id, session.application_id
        )
        if not app.is_test and (
            data.get("platform_settings", {}).get("privacy", {}).get("record_voice") is not False
        ):
            raise NotAllowed("La privacidad del agente de voz no está preparada")
        prompt = data["conversation_config"]["agent"]["prompt"]
        expected = {item["name"]: item for item in tool_specs(session)}
        actual = prompt.get("tools", [])
        if data["conversation_config"].get("realtime_model") != DIRECT_MODEL:
            raise NotAllowed("El modelo de voz ha cambiado")
        if {t["name"] for t in actual} != set(expected) or any(
            t.get("type") != "client" for t in actual
        ):
            raise NotAllowed("Las herramientas de voz han cambiado")
        if (
            prompt.get("mcp_server_ids")
            or prompt.get("native_mcp_server_ids")
            or any(prompt.get("built_in_tools", {}).values())
        ):
            raise NotAllowed("No se permiten herramientas que eludan al guardián")
        for tool in actual:
            if tool.get("expects_response") is not True:
                raise NotAllowed("La voz debe esperar el resultado de las herramientas")
            schema = expected[tool["name"]]["parameters"]
            params = tool.get("parameters", {})
            if set(params.get("properties", {})) != set(schema.get("properties", {})) or set(
                params.get("required", [])
            ) != set(schema.get("required", [])):
                raise NotAllowed("El esquema de las herramientas ha cambiado")
            if tool.get("execution_mode") != expected[tool["name"]]["execution_mode"]:
                raise NotAllowed("El modo de herramientas ha cambiado")
        if data["platform_settings"]["auth"]["enable_auth"] is not True or data[
            "conversation_config"
        ]["agent"]["prompt"]["prompt"] != voice_prompt(session):
            raise NotAllowed("Configuración del agente modificada")
        response = await client.get(
            "/v1/convai/conversation/get-signed-url", params={"agent_id": record["agent_id"]}
        )
        response.raise_for_status()
        url = response.json()["signed_url"]
        parsed = urlparse(url)
        if parsed.scheme != "wss" or parsed.hostname != "api.elevenlabs.io":
            raise NotAllowed("Origen de voz no válido")
        return str(url), record


class ElevenDirectConsumer(AsyncJsonWebsocketConsumer):
    async def opening(self, history: list[MessageRecord]) -> str:
        tenant = await self.c.conversations.repo.get_tenant(self.session.tenant_id)
        version = await self.c.conversations.versions.get(
            self.session.tenant_id, self.session.profile_version_id
        )
        offer = next(
            (o for o in version.content.offers if o.key == self.session.run_context.offer_key), None
        )
        return voice_opening(history, tenant.name, offer.title if offer else "")

    async def connect(self) -> None:
        self.bridge: ElevenDirectBridge | None = None
        self.task: asyncio.Task[None] | None = None
        self.lease = ""
        self.disconnected = False
        self.c = get_container()
        try:
            sid = UUID(self.scope["url_route"]["kwargs"]["session_id"])
            ticket = parse_qs(self.scope["query_string"].decode()).get("ticket", [""])[0]
            payload = await sync_to_async(consume_ticket)(ticket)
            if payload.get("transport") != "eleven_direct":
                raise NotAllowed("Transporte no válido")
            self.session = await self.c.conversations.repo.get_session(UUID(payload["tenant"]), sid)
            self.digest = payload["digest"]
            if (
                payload["session"] != str(sid)
                or payload["version"] != str(self.session.profile_version_id)
                or not await self.authorized()
                or not await enabled(self.session)
            ):
                raise NotAllowed("Ámbito de voz no válido")
            lease = "live-call:" + str(sid)
            if not await sync_to_async(cache.add)(lease, True, 330):
                await self.limit("Ya hay una llamada de voz activa para esta conversación.")
                return
            self.lease = lease
            if await sync_to_async(daily_limit_reached)() or await sync_to_async(limited)(
                "eleven_direct_daily", "global", settings.ELEVEN_DIRECT_DAILY_LIMIT, 86400
            ):
                await self.limit(daily_limit_message())
                await self.release()
                return
            self.runtime = VoiceRuntime(self.session)
        except Exception:
            await self.release()
            await self.close(code=4403)
            return
        await self.accept()
        self.task = asyncio.create_task(self.run_voice())

    async def limit(self, message: str) -> None:
        # Only authorized candidates reach this branch. Complete the handshake
        # so the browser receives the reason instead of an opaque network error.
        await self.accept()
        await self.send_json({"type": "error", "message": message})
        await self.close(code=4429)

    async def authorized(self) -> bool:
        current = await self.c.conversations.repo.get_session(
            self.session.tenant_id, self.session.id
        )
        return current.status == "active" and await valid_socket_link(
            self.digest, current.tenant_id, current.application_id
        )

    async def emit(self, event: dict[str, Any]) -> None:
        if not self.disconnected:
            await self.send_json(event)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        await self.channel_layer.group_send(
            f"chat_{self.session.id}", {"type": "chat.event", "payload": payload}
        )

    async def transcript(self, direction: str, text: str, identity: str) -> None:
        assert self.bridge
        if not await valid_socket_link(
            self.digest, self.session.tenant_id, self.session.application_id
        ):
            self.bridge.stop()
            return
        # The provider reuses an event ID for pre-tool speech and its continuation.
        # Deduplicate identical fragments, not distinct replies within the same turn.
        fragment = hashlib.sha256(text.encode()).hexdigest()[:20]
        provider_id = f"eleven:{self.bridge.conversation_id}:{direction}:{identity}:{fragment}"
        row = await self.runtime.transcript(direction, text, provider_id)
        if row:
            await self.broadcast({"type": "message", "message": message_payload(row)})

    async def tool(self, identity: str, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if not await self.authorized():
            return {"error": "session_not_active"}
        assert self.bridge
        result = await self.runtime.tool(f"{self.bridge.conversation_id}:{identity}", name, args)
        current = await self.c.conversations.repo.get_session(
            self.session.tenant_id, self.session.id
        )
        await self.broadcast({"type": "status", "status": current.status})
        return result

    async def watch(self) -> None:
        terminal_since: float | None = None
        while self.bridge and not self.bridge.finished.is_set():
            if not await valid_socket_link(
                self.digest, self.session.tenant_id, self.session.application_id
            ) or await human_owns(self.session):
                self.bridge.stop()
                return
            if getattr(self.runtime, "pause_requested", False) or not await self.authorized():
                terminal_since = terminal_since or monotonic()
                # Wait for the final response AND trailing audio, not an arbitrary short cut.
                settled = self.bridge.final_response_finished(terminal_since, monotonic())
                if settled or monotonic() - terminal_since > 30:
                    self.bridge.stop()
                    return
            await asyncio.sleep(0.5)

    async def run_voice(self) -> None:
        watcher: asyncio.Task[None] | None = None
        try:
            url, record = await signed_url(self.session)
            self.bridge = ElevenDirectBridge(self.emit, self.tool, self.transcript)
            history = await self.c.conversations.repo.list_messages(
                self.session.tenant_id, self.session.id
            )
            opening = await self.opening(history)
            watcher = asyncio.create_task(self.watch())
            # Fail closed if the authorization monitor itself fails.
            watcher.add_done_callback(lambda _: self.bridge.stop() if self.bridge else None)
            await self.c.conversations.repo.add_event(
                self.session.tenant_id,
                self.session.id,
                "eleven_direct_started",
                {"mode": record["mode"]},
            )
            await self.bridge.run(
                url,
                await self.runtime.context(),
                opening,
                channel_context=self.runtime.channel_context(),
            )
            if watcher.done() and not watcher.cancelled():
                watcher.result()
            await self.c.conversations.repo.add_event(
                self.session.tenant_id,
                self.session.id,
                "eleven_direct_ended",
                {"conversation_id": self.bridge.conversation_id},
            )
        except Exception as exc:
            await self.c.conversations.repo.add_event(
                self.session.tenant_id,
                self.session.id,
                "eleven_direct_failed",
                {"error_type": type(exc).__name__},
            )
            await self.emit(
                {
                    "type": "error",
                    "message": "No se pudo mantener la voz. Tu historial está guardado; "
                    "puedes seguir por escrito.",
                }
            )
        finally:
            if self.bridge and self.bridge.conversation_id:
                from web.tracing.langfuse import enqueue_voice_metrics

                try:
                    await sync_to_async(enqueue_voice_metrics)(
                        self.session.tenant_id, self.session.id, self.bridge.conversation_id
                    )
                except Exception:
                    logging.getLogger(__name__).error("No se pudieron encolar métricas de voz")
            if watcher:
                watcher.cancel()
                await asyncio.gather(watcher, return_exceptions=True)
            await self.release()
            if not self.disconnected:
                await self.close(code=1000)

    async def receive(
        self, text_data: str | None = None, bytes_data: bytes | None = None, **kwargs: Any
    ) -> None:
        try:
            if bytes_data is not None or not text_data or len(text_data) > 25000:
                raise ValueError
            content = json.loads(text_data)
            if not isinstance(content, dict) or await sync_to_async(limited)(
                "direct_audio", str(self.session.id), 800
            ):
                raise ValueError
            if content.get("type") == "audio" and self.bridge:
                pcm = content["audio"]
                decoded = base64.b64decode(pcm, validate=True)
                if not 0 < len(decoded) <= 16000 or len(decoded) % 2:
                    raise ValueError
                await self.bridge.audio(pcm)
            elif content.get("type") == "stop" and self.bridge:
                self.bridge.stop()
            elif content.get("type") != "ping":
                raise ValueError
        except (ValueError, KeyError, TypeError, asyncio.QueueFull):
            if self.bridge:
                self.bridge.stop()
            await self.close(code=4400)

    async def release(self) -> None:
        if self.lease:
            await sync_to_async(cache.delete)(self.lease)
            self.lease = ""

    async def disconnect(self, code: int) -> None:
        self.disconnected = True
        if self.bridge:
            self.bridge.stop()
        if self.task:
            # Don't cancel a guarded write midway. It is bounded by guard/tool deadlines.
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 45)
            except TimeoutError:
                self.task.cancel()
        await self.release()
