from dataclasses import asdict
from datetime import datetime
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from ninja import Router, Schema
from pydantic import Field

from screening.domain.channels import ChannelTemplate, NormalizedInbound
from screening.domain.errors import NotFound
from screening.platform.container import get_container
from web.conversations.models import Application, Event, Message, Session
from web.tenancy.context import tenant_context
from web.tenancy.permissions import require_role

router = Router()


class PhoneCandidateOut(Schema):
    id: UUID
    candidate_name: str
    external_id: str


class PhoneMessageOut(Schema):
    id: UUID
    direction: str
    text: str
    channel: str
    delivery_status: str
    simulated: bool
    created_at: datetime


class PhoneStateOut(Schema):
    application_id: UUID
    candidate_name: str
    now: datetime
    simulated: bool = True
    opted_out: bool
    session_id: UUID | None
    status: str
    messages: list[PhoneMessageOut]
    events: list[dict[str, Any]]


class PhoneInput(Schema):
    text: str = Field(min_length=1, max_length=2000)
    client_message_id: UUID


class AdvanceInput(Schema):
    hours: int = Field(ge=1, le=168)


class ProactiveInput(Schema):
    text: str = Field(
        default="¿Quieres continuar con tu candidatura?", min_length=1, max_length=2000
    )
    use_template: bool = True


class ChannelOutcomeOut(Schema):
    status: str
    session_id: UUID | None = None
    reply: str | None = None
    delivery: dict[str, Any] | None = None


class ChannelStatusOut(Schema):
    whatsapp_configured: bool
    external_sends_enabled: bool
    detail: str
    webhook_path: str
    failed_jobs: int


@router.get("/tenants/{tenant_id}/channels/status", response=ChannelStatusOut)
def channel_status(request: HttpRequest, tenant_id: UUID) -> ChannelStatusOut:
    from django.conf import settings

    from screening.domain.errors import DomainError
    from web.conversations.meta_webhook import sender_config
    from web.jobs.models import Job

    require_role(request, tenant_id, {"admin", "fde"})
    try:
        sender_config(tenant_id)
    except (DomainError, ValueError):
        configured, detail = False, "Falta configurar un emisor Meta válido en el perfil publicado."
    else:
        configured, detail = (
            True,
            "Configuración presente; conexión y teléfono real aún por validar.",
        )
    return ChannelStatusOut(
        whatsapp_configured=configured,
        external_sends_enabled=settings.META_SEND_ENABLED,
        detail=detail,
        webhook_path=f"/api/v1/webhooks/whatsapp/{tenant_id}",
        failed_jobs=Job.objects.filter(
            tenant_id=tenant_id, kind="channel_inbound", status__in=["dead", "failed"]
        ).count(),
    )


@sync_to_async
def _candidates(tenant_id: UUID) -> list[PhoneCandidateOut]:
    with tenant_context(tenant_id):
        return [
            PhoneCandidateOut.model_validate(row)
            for row in Application.objects.filter(tenant_id=tenant_id, is_test=True)
            .order_by("candidate_name")
            .values("id", "candidate_name", "external_id")[:500]
        ]


async def _identity(request: HttpRequest, tenant_id: UUID, application_id: UUID) -> str:
    await sync_to_async(require_role)(request, tenant_id, {"admin", "fde"})
    app = await get_container().conversations.repo.get_application(tenant_id, application_id)
    if not app.is_test:
        raise NotFound("La simulación solo admite candidaturas de prueba.")
    contact = f"sim:{application_id}"
    await get_container().channels.store.bind(tenant_id, contact, application_id, True)
    return contact


@sync_to_async
def _state_data(tenant_id: UUID, application_id: UUID) -> dict[str, Any]:
    with tenant_context(tenant_id):
        app = Application.objects.filter(pk=application_id, tenant_id=tenant_id).first()
        if app is None:
            raise NotFound("Candidatura no encontrada")
        session = (
            Session.objects.filter(tenant_id=tenant_id, application_id=application_id)
            .order_by("-created_at")
            .first()
        )
        return {
            "application_id": app.id,
            "candidate_name": app.candidate_name,
            "session_id": session.id if session else None,
            "status": session.status if session else "not_started",
            "messages": list(
                Message.objects.filter(tenant_id=tenant_id, session=session)
                .order_by("created_at", "id")
                .values(
                    "id",
                    "direction",
                    "text",
                    "channel",
                    "delivery_status",
                    "simulated",
                    "created_at",
                )
            )
            if session
            else [],
            "events": list(
                Event.objects.filter(tenant_id=tenant_id, session=session, type="channel_delivery")
                .order_by("created_at")
                .values("type", "payload", "created_at")
            )
            if session
            else [],
        }


@router.get("/tenants/{tenant_id}/phone", response=list[PhoneCandidateOut])
async def phone_candidates(request: HttpRequest, tenant_id: UUID) -> list[PhoneCandidateOut]:
    await sync_to_async(require_role)(request, tenant_id, {"admin", "fde"})
    return await _candidates(tenant_id)


@router.get("/tenants/{tenant_id}/phone/{application_id}", response=PhoneStateOut)
async def phone_state(request: HttpRequest, tenant_id: UUID, application_id: UUID) -> PhoneStateOut:
    contact = await _identity(request, tenant_id, application_id)
    service = get_container().channels
    identity = await service.store.identity(tenant_id, contact, True)
    return PhoneStateOut.model_validate(
        {
            **await _state_data(tenant_id, application_id),
            "now": service.now(identity),
            "opted_out": identity.opted_out,
        }
    )


@router.post("/tenants/{tenant_id}/phone/{application_id}/messages", response=ChannelOutcomeOut)
async def phone_message(
    request: HttpRequest, tenant_id: UUID, application_id: UUID, payload: PhoneInput
) -> ChannelOutcomeOut:
    contact = await _identity(request, tenant_id, application_id)
    service = get_container().channels
    identity = await service.store.identity(tenant_id, contact, True)
    result = await service.receive(
        NormalizedInbound(
            tenant_id,
            "whatsapp",
            f"sim-in:{payload.client_message_id}",
            contact,
            payload.text,
            service.now(identity),
        ),
        simulated=True,
    )
    return ChannelOutcomeOut.model_validate(asdict(result))


@router.post("/tenants/{tenant_id}/phone/{application_id}/advance", response=PhoneStateOut)
async def phone_advance(
    request: HttpRequest, tenant_id: UUID, application_id: UUID, payload: AdvanceInput
) -> PhoneStateOut:
    contact = await _identity(request, tenant_id, application_id)
    await get_container().channels.store.advance(tenant_id, contact, payload.hours * 3600)
    await get_container().followups.dispatch_due(tenant_id, contact)
    return await phone_state(request, tenant_id, application_id)


@router.post("/tenants/{tenant_id}/phone/{application_id}/outbound", response=ChannelOutcomeOut)
async def phone_outbound(
    request: HttpRequest, tenant_id: UUID, application_id: UUID, payload: ProactiveInput
) -> ChannelOutcomeOut:
    contact = await _identity(request, tenant_id, application_id)
    template = (
        ChannelTemplate("simulated_followup", "es", "followup", approved=True)
        if (payload.use_template)
        else None
    )
    result = await get_container().channels.proactive(
        tenant_id, contact, payload.text, template, simulated=True
    )
    return ChannelOutcomeOut.model_validate(asdict(result))
