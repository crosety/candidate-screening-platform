"""Interview agenda: candidate self-service (token in the body) and the team's agenda."""

from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from asgiref.sync import sync_to_async
from channels.layers import get_channel_layer
from django.http import HttpRequest, HttpResponse
from django.utils import timezone
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from screening.domain.errors import NotAllowed
from screening.domain.interviews import local_text, zone_label
from screening.platform.container import get_container
from web.conversations.models import InterviewBooking
from web.core.security import rate_limit
from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent
from web.tenancy.permissions import require_role
from web.tenancy.scope import applications

router = Router()

InterviewStatus = Literal["booked", "rescheduled", "cancelled"]


class CandidateInterviewIn(Schema):
    token: str = Field(min_length=1, max_length=200)
    session_id: UUID


class CandidateInterviewOut(Schema):
    id: UUID
    status: InterviewStatus
    when: str
    timezone: str
    timezone_label: str
    starts_at: datetime
    site_name: str
    address: str | None
    duration_minutes: int
    can_change: bool


class CandidateInterviewState(Schema):
    interview: CandidateInterviewOut | None


class ManageOut(Schema):
    status: str


class InterviewOut(Schema):
    id: UUID
    session_id: UUID
    candidate_name: str
    site_ref: str
    site_name: str
    site_address: str
    site_timezone: str
    starts_at: datetime
    starts_at_local: str
    candidate_timezone: str
    candidate_time: str
    duration_minutes: int
    status: InterviewStatus
    sequence: int
    cancelled_by: str
    reminder_at: datetime | None
    reminder_status: str


class SessionInterviewOut(Schema):
    interview: InterviewOut | None


class InterviewSlotOut(Schema):
    slot_id: str
    starts_at_local: str
    site_name: str


class InterviewActionIn(Schema):
    action: Literal["cancel", "reschedule"]
    slot_id: str = Field(default="", max_length=60)


def _calendar(text: str) -> HttpResponse:
    response = HttpResponse(text, content_type="text/calendar; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="entrevista.ics"'
    response["Cache-Control"] = "no-store"
    return response


async def _candidate(request: HttpRequest, payload: CandidateInterviewIn) -> tuple[UUID, UUID]:
    await sync_to_async(rate_limit)(request, "chat_interview", 60)
    repo = get_container().conversations.repo
    link = await repo.resolve_link(payload.token)
    session = await repo.get_session(link.tenant_id, payload.session_id)
    if session.application_id != link.application_id:
        raise NotAllowed("Enlace no válido para esta conversación.")
    return link.tenant_id, session.application_id


@router.post("/chat/interview", auth=None, response=CandidateInterviewState)
async def candidate_interview(
    request: HttpRequest, payload: CandidateInterviewIn
) -> dict[str, Any]:
    tenant_id, application_id = await _candidate(request, payload)
    return {"interview": await get_container().interviews.candidate_view(tenant_id, application_id)}


@router.post("/chat/interview/manage", auth=None, response=ManageOut)
async def candidate_manage(request: HttpRequest, payload: CandidateInterviewIn) -> dict[str, str]:
    tenant_id, _ = await _candidate(request, payload)
    c = get_container()
    async with c.conversations.turn_lock.hold(tenant_id, payload.session_id):
        status = await c.interviews.reopen(tenant_id, payload.session_id)
    layer = get_channel_layer()
    if layer is not None:
        await layer.group_send(
            f"chat_{payload.session_id}",
            {"type": "chat.event", "payload": {"type": "status", "status": status}},
        )
    return {"status": status}


@router.post("/chat/interview/ics", auth=None)
async def candidate_ics(request: HttpRequest, payload: CandidateInterviewIn) -> HttpResponse:
    tenant_id, application_id = await _candidate(request, payload)
    c = get_container()
    booking = await c.interviews.store.latest_for(tenant_id, application_id)
    if booking is None:
        raise HttpError(404, "No hay ninguna entrevista reservada.")
    return _calendar(await c.interviews.ics(tenant_id, booking.id))


# --- Recruiter agenda ---------------------------------------------------------------------


def _out(row: InterviewBooking) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "candidate_name": row.application.candidate_name,
        "site_ref": row.site_ref,
        "site_name": row.site_name,
        "site_address": row.site_address,
        "site_timezone": row.site_timezone,
        "starts_at": row.starts_at,
        "starts_at_local": f"{local_text(row.starts_at, row.site_timezone)} "
        f"({zone_label(row.site_timezone)})",
        "candidate_timezone": row.candidate_timezone,
        "candidate_time": f"{local_text(row.starts_at, row.candidate_timezone)} "
        f"({zone_label(row.candidate_timezone)})",
        "duration_minutes": row.duration_minutes,
        "status": row.status,
        "sequence": row.sequence,
        "cancelled_by": row.cancelled_by,
        "reminder_at": row.reminder_at,
        "reminder_status": row.reminder_status,
    }


def _scoped(request: HttpRequest, tenant_id: UUID) -> Any:
    member = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    return member, InterviewBooking.objects.filter(
        tenant_id=tenant_id,
        application_id__in=applications(member).values("id"),
        application__erased_at__isnull=True,
    ).select_related("application")


def _authorize_booking(request: HttpRequest, tenant_id: UUID, booking_id: UUID) -> UUID:
    with tenant_context(tenant_id):
        member, rows = _scoped(request, tenant_id)
        if not rows.filter(pk=booking_id).exists():
            raise HttpError(404, "Entrevista no encontrada")
        return member.user_id


@router.get("/tenants/{tenant_id}/interviews", response=list[InterviewOut])
def agenda(
    request: HttpRequest,
    tenant_id: UUID,
    status: Literal["", "booked", "rescheduled", "cancelled"] = "",
    site: str = "",
    include_past: bool = False,
) -> list[dict[str, Any]]:
    with tenant_context(tenant_id):
        _, rows = _scoped(request, tenant_id)
        if status:
            rows = rows.filter(status=status)
        if site:
            rows = rows.filter(site_ref=site)
        if not include_past:
            rows = rows.filter(starts_at__gte=timezone.now() - timedelta(hours=1))
        return [_out(row) for row in rows.order_by("site_name", "starts_at")[:500]]


@router.get("/tenants/{tenant_id}/sessions/{session_id}/interview", response=SessionInterviewOut)
def session_interview(request: HttpRequest, tenant_id: UUID, session_id: UUID) -> dict[str, Any]:
    with tenant_context(tenant_id):
        _, rows = _scoped(request, tenant_id)
        row = rows.filter(session_id=session_id).order_by("-updated_at").first()
        return {"interview": _out(row) if row else None}


@router.get("/tenants/{tenant_id}/interviews/{booking_id}/slots", response=list[InterviewSlotOut])
async def interview_slots(
    request: HttpRequest, tenant_id: UUID, booking_id: UUID
) -> list[dict[str, Any]]:
    await sync_to_async(_authorize_booking)(request, tenant_id, booking_id)
    return await get_container().interviews.recruiter_slots(tenant_id, booking_id)


@router.post("/tenants/{tenant_id}/interviews/{booking_id}", response=InterviewOut)
async def interview_action(
    request: HttpRequest, tenant_id: UUID, booking_id: UUID, payload: InterviewActionIn
) -> dict[str, Any]:
    actor = await sync_to_async(_authorize_booking)(request, tenant_id, booking_id)
    if payload.action == "reschedule" and not payload.slot_id:
        raise HttpError(400, "Elige el nuevo hueco de la entrevista.")
    c = get_container()
    booking = await c.interviews.store.get(tenant_id, booking_id)
    async with c.conversations.turn_lock.hold(tenant_id, booking.session_id):
        result = await c.interviews.recruiter_change(
            tenant_id, booking_id, payload.action, payload.slot_id
        )
    if result["status"] not in ("cancelled", "rescheduled"):
        messages = {
            "taken": "Ese hueco ya está ocupado. Elige otro.",
            "expired": "Ese hueco ya ha pasado. Elige otro.",
            "same_slot": "La entrevista ya está en ese hueco.",
            "not_found": "No se ha encontrado ese hueco.",
            "not_owner": "La agenda no muestra esta reserva; revisa la agenda del local.",
        }
        raise HttpError(409, messages.get(result["status"], "No se ha podido cambiar la agenda."))

    @sync_to_async
    def audited() -> dict[str, Any]:
        with tenant_context(tenant_id):
            row = InterviewBooking.objects.select_related("application").get(pk=booking_id)
            AuditEvent.objects.create(
                tenant_id=tenant_id,
                actor_id=actor,
                action=f"interview.{payload.action}",
                payload={
                    "booking_id": str(booking_id),
                    "session_id": str(row.session_id),
                    "sequence": row.sequence,
                    "slot_id": payload.slot_id,
                },
            )
            return _out(row)

    return await audited()


@router.get("/tenants/{tenant_id}/interviews/{booking_id}/ics")
async def interview_ics(request: HttpRequest, tenant_id: UUID, booking_id: UUID) -> HttpResponse:
    await sync_to_async(_authorize_booking)(request, tenant_id, booking_id)
    return _calendar(await get_container().interviews.ics(tenant_id, booking_id))
