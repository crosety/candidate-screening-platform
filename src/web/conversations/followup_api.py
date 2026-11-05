from dataclasses import asdict
from datetime import datetime
from typing import Literal
from uuid import UUID

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from ninja import Router, Schema
from pydantic import Field

from screening.platform.container import get_container
from web.conversations.models import Followup
from web.tenancy.context import tenant_context
from web.tenancy.permissions import require_role
from web.tenancy.scope import require_session, sessions

router = Router()


class FollowupOut(Schema):
    id: UUID
    session_id: UUID
    candidate_name: str
    run_at: datetime
    reason: str
    status: str
    simulated: bool


class ScheduleFollowupIn(Schema):
    session_id: UUID
    at_local: str
    reason: str = Field(min_length=1, max_length=300)


class FollowupActionIn(Schema):
    action: Literal["approve", "cancel"]


@router.get("/tenants/{tenant_id}/followups", response=list[FollowupOut])
def followup_list(request: HttpRequest, tenant_id: UUID) -> list[FollowupOut]:
    member = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    with tenant_context(tenant_id):
        return [
            FollowupOut(
                id=row.id,
                session_id=row.session_id,
                candidate_name=row.session.application.candidate_name,
                run_at=row.run_at,
                reason=row.reason,
                status=row.status,
                simulated=row.simulated,
            )
            for row in Followup.objects.filter(
                tenant_id=tenant_id, session_id__in=sessions(member).values("id")
            )
            .select_related("session__application")
            .order_by("-created_at")[:500]
        ]


@router.post("/tenants/{tenant_id}/followups", response=dict[str, str])
async def schedule_followup(
    request: HttpRequest, tenant_id: UUID, payload: ScheduleFollowupIn
) -> dict[str, str]:
    await sync_to_async(authorize_followup_session)(request, tenant_id, payload.session_id)
    container = get_container()
    session = await container.conversations.repo.get_session(tenant_id, payload.session_id)
    return await container.followups.schedule(
        session.run_context, payload.at_local, payload.reason, author="fde"
    )


@router.post("/tenants/{tenant_id}/followups/{followup_id}", response=dict[str, str])
async def followup_action(
    request: HttpRequest, tenant_id: UUID, followup_id: UUID, payload: FollowupActionIn
) -> dict[str, str]:
    await sync_to_async(authorize_followup)(request, tenant_id, followup_id)
    store = get_container().followups.store
    await store.get(tenant_id, followup_id)
    if payload.action == "approve":
        row = await store.approve(tenant_id, followup_id)
        return {key: str(value) for key, value in asdict(row).items() if key in ("id", "status")}
    await store.finish(tenant_id, followup_id, "cancelled")
    return {"id": str(followup_id), "status": "cancelled"}


def authorize_followup_session(request: HttpRequest, tenant_id: UUID, session_id: UUID) -> None:
    member = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    with tenant_context(tenant_id):
        require_session(member, session_id)


def authorize_followup(request: HttpRequest, tenant_id: UUID, followup_id: UUID) -> None:
    from ninja.errors import HttpError

    member = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    with tenant_context(tenant_id):
        row = Followup.objects.filter(
            tenant_id=tenant_id, pk=followup_id, session_id__in=sessions(member).values("id")
        ).first()
        if row is None:
            raise HttpError(404, "Recordatorio no encontrado")
