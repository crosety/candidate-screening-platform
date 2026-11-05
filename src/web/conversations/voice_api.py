import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID, uuid4

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.core import signing
from django.http import HttpRequest, JsonResponse, StreamingHttpResponse
from django.http.response import HttpResponseBase
from django.views.decorators.csrf import csrf_exempt
from ninja import Router, Schema
from pydantic import Field

from adapters.channels.eleven import ElevenAdapter, VoiceAgent
from screening.domain.errors import DomainError, NotFound
from screening.domain.voice import sentences
from screening.platform.container import get_container
from web.core.security import rate_limit
from web.tenancy.permissions import require_role

router = Router()
SALT = "voice-session-v1"
logger = logging.getLogger(__name__)
KEEPALIVE_SECONDS = 2.0
# Fixed transport notice, not model output: no candidate facts, promises or decisions.
# ElevenLabs requires spoken content; empty deltas do not stop its first-token timeout.
WAITING_NOTICE = "Estoy revisando tu respuesta... "
# Keep disconnected turns alive until their bounded runner persists its outcome.
# Concurrent provider retries in this process share the same work, not new inference.
_inflight: dict[tuple[UUID, UUID, str], asyncio.Task[tuple[bytes, ...]]] = {}


async def configuration(tenant_id: UUID, version_id: UUID) -> VoiceAgent:
    c = get_container()
    version = await c.conversations.versions.get(tenant_id, version_id)
    tenant = await c.conversations.repo.get_tenant(tenant_id)
    voice = version.content.channel_settings.voice
    ref = voice.agent_ref if voice else ""
    if "voice" not in version.content.channels or not ref.startswith(f"secret://{tenant.slug}/"):
        raise NotFound("Voz no configurada para esta versión.")
    return VoiceAgent.model_validate_json(c.secrets.resolve(ref))


def session_token(tenant_id: UUID, session_id: UUID, version_id: UUID) -> str:
    return signing.dumps([str(tenant_id), str(session_id), str(version_id)], salt=SALT)


async def events(reply: str) -> AsyncIterator[str]:
    stream_id = f"chatcmpl-{uuid4()}"
    for part in [*sentences(reply), None]:
        chunk = {
            "id": stream_id,
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "screening-guarded",
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": part + " "} if part else {},
                    "finish_reason": None if part else "stop",
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


def validate_turn(
    session_id: UUID, messages: list[dict[str, Any]], turn_scope: str = ""
) -> tuple[str, str]:
    if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
        raise ValueError("Historial de voz no válido.")
    if any(not isinstance(message, dict) for message in messages):
        raise ValueError("Historial de voz no válido.")
    # External assistant/system/tool content is neither authority nor persisted history.
    last = next((m for m in reversed(messages) if m.get("role") == "user"), None)
    text = last.get("content", "") if last else ""
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError("Intervención de voz no válida.")
    # Provider retries can append assistant buffer messages. Those must not turn
    # the same candidate utterance into a new persisted/inferred turn.
    user_turn = sum(message.get("role") == "user" for message in messages)
    scope = f"{turn_scope}:" if turn_scope else ""
    key = hashlib.sha256(f"voice-v2:{session_id}:{scope}{user_turn}:{text}".encode()).hexdigest()[
        :60
    ]
    return key, text


async def complete(
    tenant_id: UUID, session_id: UUID, messages: list[dict[str, Any]]
) -> HttpResponseBase:
    reply = await guarded_reply(tenant_id, session_id, messages)
    response = StreamingHttpResponse(events(reply), content_type="text/event-stream")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    return response


async def guarded_reply(
    tenant_id: UUID, session_id: UUID, messages: list[dict[str, Any]], turn_scope: str = ""
) -> str:
    key, text = validate_turn(session_id, messages, turn_scope)
    service = get_container().conversations
    stored = await service.repo.list_messages(tenant_id, session_id)
    previous = next((i for i, m in enumerate(stored) if m.client_message_id == key), None)
    if previous is not None:
        replay = next((m for m in stored[previous + 1 :] if m.direction == "out"), None)
        if replay:
            return replay.text
    started = time.monotonic()
    result = await service.handle_message(tenant_id, session_id, key, text, channel="voice")
    await service.repo.add_event(
        tenant_id,
        session_id,
        "voice_turn_latency",
        {"ready_ms": round((time.monotonic() - started) * 1000), "streaming_generation": False},
    )
    if result.message is not None:
        layer = get_channel_layer()
        if layer is not None:
            persisted = await service.repo.list_messages(tenant_id, session_id)
            for message in [m for m in persisted if m.channel == "voice"][-2:]:
                await layer.group_send(
                    f"chat_{session_id}",
                    {
                        "type": "chat.event",
                        "payload": {
                            "type": "message",
                            "message": {
                                "id": str(message.id),
                                "direction": message.direction,
                                "text": message.text,
                                "created_at": message.created_at.isoformat(),
                            },
                        },
                    },
                )
            await layer.group_send(
                f"chat_{session_id}",
                {"type": "chat.event", "payload": {"type": "status", "status": result.status}},
            )
    if result.message is None:
        history = await service.repo.list_messages(tenant_id, session_id)
        incoming = next((i for i, m in enumerate(history) if m.client_message_id == key), None)
        replay = (
            next((m for m in history[incoming + 1 :] if m.direction == "out"), None)
            if incoming is not None
            else None
        )
        reply = replay.text if replay else "Tu respuesta ya está registrada. Un momento, por favor."
    else:
        reply = result.message.text
    return reply


def stream_complete(
    tenant_id: UUID, session_id: UUID, messages: list[dict[str, Any]]
) -> StreamingHttpResponse:
    key, _ = validate_turn(session_id, messages)
    identity = (tenant_id, session_id, key)

    async def prepare() -> tuple[bytes, ...]:
        response = await complete(tenant_id, session_id, messages)
        if not isinstance(response, StreamingHttpResponse):
            raise ValueError("Expected guarded voice stream")
        # Immutable chunks can be replayed to concurrent consumers independently.
        return tuple([chunk async for chunk in response])

    async def stream() -> AsyncIterator[bytes | str]:
        task = _inflight.get(identity)
        if task is None:
            task = asyncio.create_task(prepare())
            _inflight[identity] = task

            def finished(done: asyncio.Task[tuple[bytes, ...]]) -> None:
                _inflight.pop(identity, None)
                if not done.cancelled() and (error := done.exception()) is not None:
                    # Exception messages may contain candidate content or provider URLs.
                    logger.warning("Voice turn failed: %s", type(error).__name__)

            task.add_done_callback(finished)
        try:
            # Only this fixed platform notice precedes the fully guarded response.
            initial = {
                "id": f"chatcmpl-{uuid4()}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "screening-guarded",
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant", "content": WAITING_NOTICE},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(initial, ensure_ascii=False)}\n\n"
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=KEEPALIVE_SECONDS)
                if not done:
                    # The provider measures content gaps, not HTTP liveness. A space
                    # keeps that timer alive without adding speech or model content.
                    initial["choices"] = [
                        {"index": 0, "delta": {"content": " "}, "finish_reason": None}
                    ]
                    yield f"data: {json.dumps(initial, ensure_ascii=False)}\n\n"
            for chunk in task.result():
                yield chunk
        except asyncio.CancelledError:
            # Do not cancel a persisted incoming turn: a retry must recover its reply.
            await get_container().conversations.repo.add_event(
                tenant_id, session_id, "voice_stream_disconnected", {"turn_continues": True}
            )
            raise
        except Exception as error:
            logger.warning("Voice stream failed: %s", type(error).__name__)
            yield (
                'data: {"error":{"type":"voice_turn_failed",'
                '"message":"No se ha podido completar la respuesta."}}\n\n'
            )
            yield "data: [DONE]\n\n"

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-store"
    response["X-Accel-Buffering"] = "no"
    return response


@csrf_exempt
async def voice_callback(request: HttpRequest, tenant_id: UUID) -> HttpResponseBase:
    if request.method != "POST":
        return JsonResponse({"detail": "Método no permitido"}, status=405)
    if not request.headers.get("Authorization", "").startswith("Bearer "):
        return JsonResponse({"detail": "Acceso no autorizado"}, status=401)
    try:
        # Verify the signed envelope first, then authenticate against its frozen version.
        # A later publication must not change the configuration of an ongoing call.
        c = get_container()
        if len(request.body) > 256_000:
            raise ValueError("Petición demasiado grande.")
        body = json.loads(request.body)
        if not isinstance(body, dict) or not isinstance(body.get("elevenlabs_extra_body"), dict):
            raise ValueError("Petición no válida")
        token = body.get("elevenlabs_extra_body", {}).get("session_token", "")
        tenant, session_id, version = signing.loads(token, salt=SALT, max_age=900)
        if tenant != str(tenant_id):
            raise ValueError("Cliente no válido.")
        session = await c.conversations.repo.get_session(tenant_id, UUID(session_id))
        if str(session.profile_version_id) != version:
            raise ValueError("Versión no válida.")
        config = await configuration(tenant_id, session.profile_version_id)
        if not hmac.compare_digest(
            request.headers.get("Authorization", ""),
            f"Bearer {config.callback_secret.get_secret_value()}",
        ):
            return JsonResponse({"detail": "Acceso no autorizado"}, status=401)
        return stream_complete(tenant_id, session.id, body.get("messages", []))
    except (ValueError, TypeError, KeyError, signing.BadSignature, DomainError):
        return JsonResponse({"detail": "No se puede validar esta conversación de voz."}, status=400)


class ConnectInput(Schema):
    token: str
    consent: bool = False
    provider: str = "ElevenLabs"


class ConnectOutput(Schema):
    signed_url: str
    session_token: str
    transport: str = "speech_engine"


class VoiceInfo(Schema):
    provider: str
    transport: str


@router.post("/chat/sessions/{session_id}/voice/config", auth=None, response=VoiceInfo)
async def voice_config(request: HttpRequest, session_id: UUID, payload: ConnectInput) -> VoiceInfo:
    from web.conversations.eleven_direct import enabled as direct_enabled
    from web.conversations.gpt_live import enabled

    await sync_to_async(rate_limit)(request, "voice_config", 30)
    session = await get_container().conversations.authorize(payload.token, session_id)
    if await direct_enabled(session):
        return VoiceInfo(provider="ElevenLabs", transport="eleven_direct")
    live = await enabled(session)
    return VoiceInfo(
        provider="OpenAI" if live else "ElevenLabs",
        transport="gpt_live" if live else "speech_engine",
    )


@router.post("/chat/sessions/{session_id}/voice", auth=None, response=ConnectOutput)
async def connect(request: HttpRequest, session_id: UUID, payload: ConnectInput) -> ConnectOutput:
    from ninja.errors import HttpError

    from web.conversations.eleven_direct import daily_limit_message, daily_limit_reached
    from web.conversations.eleven_direct import enabled as direct_enabled
    from web.conversations.gpt_live import enabled, issue_ticket

    await sync_to_async(rate_limit)(request, "voice_connect", 8)
    c = get_container()
    session = await c.conversations.authorize(payload.token, session_id)
    if not payload.consent:
        raise HttpError(422, "Confirma el uso opcional de voz antes de activar el micrófono.")
    if session.status != "active":
        raise NotFound("Conversación cerrada")
    direct = await direct_enabled(session)
    live = not direct and await enabled(session)
    provider = "OpenAI" if live else "ElevenLabs"
    if payload.provider != provider:
        raise HttpError(409, "Ha cambiado el proveedor de voz. Revisa el consentimiento.")
    if direct and await sync_to_async(daily_limit_reached)():
        # Reject before issuing a ticket or asking the browser for its microphone.
        raise HttpError(429, daily_limit_message())
    if live or direct:
        ticket = await sync_to_async(issue_ticket)(
            session, payload.token, "eleven_direct" if direct else "gpt_live"
        )
        await c.conversations.repo.add_event(
            session.tenant_id,
            session.id,
            "voice_consent",
            {"notice_version": "2026-10-27", "provider": provider, "optional": True},
        )
        return ConnectOutput(
            signed_url=f"/ws/voice/{'direct' if direct else 'live'}/{session_id}/?ticket={ticket}",
            session_token="",
            transport="eleven_direct" if direct else "gpt_live",
        )
    config = await configuration(session.tenant_id, session.profile_version_id)
    version = await c.conversations.versions.get(session.tenant_id, session.profile_version_id)
    voice = version.content.channel_settings.voice
    try:
        url = await ElevenAdapter(
            config, expected_voice_id=voice.voice_id if voice else ""
        ).signed_url()
    except ValueError:
        raise HttpError(503, "La voz no está disponible. Puedes continuar por escrito.") from None
    await c.conversations.repo.add_event(
        session.tenant_id,
        session.id,
        "voice_consent",
        {"notice_version": "2026-10-23", "provider": "ElevenLabs", "optional": True},
    )
    return ConnectOutput(
        signed_url=url,
        session_token=session_token(session.tenant_id, session.id, session.profile_version_id),
        transport="speech_engine" if config.agent_id.startswith("seng_") else "custom_llm",
    )


class TestInput(Schema):
    text: str = Field(min_length=1, max_length=2000)


@router.post("/tenants/{tenant_id}/voice-test/{session_id}", response=None)
async def text_test(
    request: HttpRequest, tenant_id: UUID, session_id: UUID, payload: TestInput
) -> HttpResponseBase:
    await sync_to_async(require_role)(request, tenant_id, {"admin", "fde"})
    session = await get_container().conversations.repo.get_session(tenant_id, session_id)
    app = await get_container().conversations.repo.get_application(
        tenant_id, session.application_id
    )
    if not app.is_test:
        raise NotFound("La simulación solo admite candidaturas de prueba.")
    return await complete(tenant_id, session_id, [{"role": "user", "content": payload.text}])
