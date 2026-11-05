from dataclasses import asdict
from uuid import UUID

from asgiref.sync import async_to_sync
from django.http import HttpRequest
from ninja import Router
from ninja.errors import HttpError

from screening.platform.container import get_container
from web.decisions.models import Decision
from web.decisions.schemas import DecisionIn, DecisionOut, InboxOut
from web.tenancy.context import tenant_context
from web.tenancy.permissions import require_role
from web.tenancy.scope import require_session, sessions

router = Router()


@router.get("/tenants/{tenant_id}/inbox", response=list[InboxOut])
def inbox(request: HttpRequest, tenant_id: UUID) -> list[InboxOut]:
    membership = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    with tenant_context(tenant_id):
        allowed = set(sessions(membership).values_list("id", flat=True))
    return [
        InboxOut.model_validate(asdict(item))
        for item in async_to_sync(get_container().inbox.list_pending)(tenant_id, frozenset(allowed))
        if item.session_id in allowed
    ]


@router.post("/tenants/{tenant_id}/decisions/{decision_id}", response=DecisionOut)
def act(
    request: HttpRequest, tenant_id: UUID, decision_id: UUID, payload: DecisionIn
) -> DecisionOut:
    membership = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    with tenant_context(tenant_id):
        row = Decision.objects.filter(pk=decision_id, tenant_id=tenant_id).first()
        if row is None:
            raise HttpError(404, "Decisión no encontrada")
        require_session(membership, row.session_id)
    return DecisionOut.model_validate(
        asdict(
            async_to_sync(get_container().inbox.act)(
                tenant_id, decision_id, payload.action, payload.reason, membership.user_id
            )
        )
    )
