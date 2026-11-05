"""Audited HR takeover. No AI calls while a human owns the conversation."""

from typing import Any, Literal
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from langchain_core.messages import AIMessage, HumanMessage, message_to_dict
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from adapters.persistence.followups import cancel_pending
from screening.platform.container import get_container
from web.conversations.models import Event, Message, ModelMessage, Session
from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent
from web.tenancy.permissions import require_role
from web.tenancy.scope import require_session

router = Router()


class HumanAction(Schema):
    action: Literal["claim", "message", "resume_ai", "close"]
    text: str = Field(default="", max_length=2000)
    message_id: UUID | None = None


class HumanState(Schema):
    status: str
    owner_id: str | None
    owned_by_me: bool
    message: dict[str, Any] | None = None


def authorize(request: HttpRequest, tenant: UUID, session: UUID) -> UUID:
    member = require_role(request, tenant, {"admin", "fde", "recruiter"})
    with tenant_context(tenant):
        require_session(member, session)
    return member.user_id


@router.get("/tenants/{tenant_id}/sessions/{session_id}/human", response=HumanState)
def state(request: HttpRequest, tenant_id: UUID, session_id: UUID) -> dict[str, Any]:
    actor = authorize(request, tenant_id, session_id)
    with tenant_context(tenant_id):
        session = Session.objects.get(pk=session_id)
        return {
            "status": session.status,
            "owner_id": str(session.human_owner_id) if session.human_owner_id else None,
            "owned_by_me": session.human_owner_id == actor,
        }


@sync_to_async
def mutate(tenant: UUID, sid: UUID, actor: UUID, payload: HumanAction) -> dict[str, Any]:
    with tenant_context(tenant), transaction.atomic():
        session = Session.objects.select_for_update().get(pk=sid)
        if session.status not in ("active", "handed_off"):
            raise HttpError(409, "La conversación está cerrada")
        if payload.action == "claim":
            if session.human_owner_id not in (None, actor):
                raise HttpError(409, "Otro miembro del equipo está atendiendo la conversación")
            session.human_owner_id = actor
            session.status = "handed_off"
            session.ended_at = None
            cancel_pending(tenant, sid)
        elif session.human_owner_id != actor:
            raise HttpError(409, "Toma la conversación antes de responder o cerrarla")
        message = None
        if payload.action == "message":
            if not payload.text.strip() or payload.message_id is None:
                raise HttpError(422, "Escribe un mensaje con identificador")
            if session.channel == "whatsapp" and len(payload.text.strip()) > 1600:
                raise HttpError(422, "WhatsApp admite hasta 1600 caracteres por mensaje")
            row, created = Message.objects.get_or_create(
                tenant_id=tenant,
                session_id=sid,
                client_message_id="hr:" + str(payload.message_id),
                defaults={"direction": "out", "channel": "human", "text": payload.text.strip()},
            )
            if not created and row.text != payload.text.strip():
                raise HttpError(409, "Identificador de mensaje reutilizado con otro contenido")
            if created:
                from web.conversations.twilio_runtime import queue_human

                queue_human(session, row)
            message = {
                "id": str(row.id),
                "direction": "out",
                "text": row.text,
                "channel": "human",
                "client_message_id": row.client_message_id,
                "created_at": row.created_at.isoformat(),
            }
        elif payload.action == "resume_ai":
            # Transfer this human episode atomically before reopening the AI.
            ModelMessage.objects.bulk_create(human_history(tenant, sid))
            session.human_owner_id = None
            session.status = "active"
            session.ended_at = None
        elif payload.action == "close":
            cancel_pending(tenant, sid)
            session.status = "completed"
            session.ended_at = timezone.now()
        session.save(update_fields=["human_owner", "status", "ended_at"])
        Event.objects.create(
            tenant_id=tenant,
            session_id=sid,
            type="human_" + payload.action,
            payload={
                "actor_id": str(actor),
                "message_id": str(payload.message_id) if payload.message_id else None,
            },
        )
        AuditEvent.objects.create(
            tenant_id=tenant,
            actor_id=actor,
            action="conversation.human_" + payload.action,
            payload={"session_id": str(sid)},
        )
        return {
            "status": session.status,
            "owner_id": str(session.human_owner_id) if session.human_owner_id else None,
            "owned_by_me": session.human_owner_id == actor,
            "message": message,
        }


def human_history(tenant: UUID, sid: UUID) -> list[ModelMessage]:
    with tenant_context(tenant):
        last_resume = (
            Event.objects.filter(session_id=sid, type="human_resume_ai")
            .order_by("-created_at")
            .first()
        )
        starts = Event.objects.filter(session_id=sid, type__in=["human_claim", "handoff_requested"])
        if last_resume:
            starts = starts.filter(created_at__gt=last_resume.created_at)
        start = starts.order_by("created_at").first()
        if start is None:
            return []
        rows = Message.objects.filter(session_id=sid, created_at__gte=start.created_at).order_by(
            "created_at", "id"
        )
        offset = ModelMessage.objects.filter(session_id=sid).count()
        result: list[ModelMessage] = []
        for row in rows:
            if row.channel == "voice" or (row.direction == "out" and row.channel != "human"):
                continue
            result.append(
                ModelMessage(
                    tenant_id=tenant,
                    session_id=sid,
                    seq=offset + len(result),
                    role="user" if row.direction == "in" else "assistant",
                    content=message_to_dict(
                        HumanMessage(row.text)
                        if row.direction == "in"
                        else AIMessage("[Equipo humano] " + row.text)
                    ),
                )
            )
        return result


@router.post("/tenants/{tenant_id}/sessions/{session_id}/human", response=HumanState)
async def action(
    request: HttpRequest, tenant_id: UUID, session_id: UUID, payload: HumanAction
) -> dict[str, Any]:
    actor = await sync_to_async(authorize)(request, tenant_id, session_id)
    service = get_container().conversations
    async with service.turn_lock.hold(tenant_id, session_id):
        result = await mutate(tenant_id, session_id, actor, payload)
    layer = get_channel_layer()
    if result["message"]:
        await layer.group_send(
            f"chat_{session_id}",
            {"type": "chat.event", "payload": {"type": "message", "message": result["message"]}},
        )
    await layer.group_send(
        f"chat_{session_id}",
        {"type": "chat.event", "payload": {"type": "status", "status": result["status"]}},
    )
    return result
