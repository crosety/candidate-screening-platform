from dataclasses import replace
from datetime import date
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from ninja import Router, Schema
from ninja.errors import HttpError
from pydantic import Field

from adapters.persistence.recruiter import DjangoRecruiterReader
from screening.application.recruiter import AggregateQuery, CandidateQuery, RecruiterScope
from screening.domain.run_context import RunContext
from screening.platform.container import get_container
from web.conversations.models import Event, Followup, Message
from web.conversations.recruiter_schemas import (
    AgreementOut,
    CandidateDetailOut,
    CandidatesOut,
    CountsOut,
    MetricsOut,
    QueryHistoryOut,
)
from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent
from web.tenancy.permissions import require_role
from web.tenancy.scope import require_session, segments

router = Router()


def scope_for(request: HttpRequest, tenant_id: UUID) -> RecruiterScope:
    member = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    allowed = ("*",) if member.role in ("fde", "admin") else segments(member)
    # People may open every conversation in their scope (see require_session), so
    # literal candidate quotes in aggregates stay within what they can already read.
    return RecruiterScope(tenant_id, member.user_id, allowed, conversations=True)


@router.get("/tenants/{tenant_id}/candidates", response=CandidatesOut)
async def candidates(
    request: HttpRequest,
    tenant_id: UUID,
    status: str = "",
    segment: str = "",
    offset: int = 0,
    verdict: str = "",
    location: str = "",
    shift: str = "",
    channel: str = "",
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    scope = await sync_to_async(scope_for)(request, tenant_id)
    try:
        query = CandidateQuery.model_validate(
            {
                "status": status,
                "segment": segment,
                "offset": offset,
                "verdict": verdict,
                "location": location,
                "shift": shift,
                "channel": channel,
                "since": since,
                "until": until,
            }
        )
    except ValueError:
        raise HttpError(422, "Filtros no válidos.") from None
    return await DjangoRecruiterReader().query(scope, query)


@router.get("/tenants/{tenant_id}/candidates/{session_id}", response=CandidateDetailOut)
def candidate(request: HttpRequest, tenant_id: UUID, session_id: UUID) -> dict[str, Any]:
    membership = require_role(request, tenant_id, {"admin", "fde", "recruiter"})
    with tenant_context(tenant_id):
        require_session(membership, session_id)
        from web.conversations.models import Session
        from web.decisions.models import Decision, Result

        row = Session.objects.select_related("application", "profile_version").get(pk=session_id)
        result = Result.objects.filter(session=row).first()
        decisions = list(
            Decision.objects.filter(session=row).values("decision_type", "status", "reason")
        )
        return {
            "id": str(row.id),
            "name": row.application.candidate_name,
            "segment": row.application.segment,
            "status": row.status,
            "version": row.profile_version.version,
            "messages": [
                {**message, "signal": message["signal"] or None}
                for message in Message.objects.filter(session=row)
                .order_by("created_at", "id")
                .values("id", "direction", "text", "channel", "created_at", "language", "signal")
            ],
            "result": {
                "verdict": result.verdict,
                "summary": result.summary,
                "fields": result.fields,
                "flags": result.flags,
                "sentiment": result.sentiment,
            }
            if result
            else None,
            "decisions": decisions,
            "timeline": list(
                Event.objects.filter(tenant_id=tenant_id, session=row)
                .order_by("created_at", "id")
                .values("id", "type", "created_at")
            ),
            "followups": list(
                Followup.objects.filter(tenant_id=tenant_id, session=row)
                .order_by("created_at", "id")
                .values("id", "status", "run_at")
            ),
            "next_step": "Revisar decisión pendiente"
            if any(d["status"] == "proposed" for d in decisions)
            else "Revisar derivación"
            if row.status == "handed_off"
            else "Esperar respuesta del candidato"
            if row.status == "active"
            else "Cribado cerrado; revisar resultado",
        }


@router.get("/tenants/{tenant_id}/analytics", response=CountsOut | AgreementOut | MetricsOut)
async def analytics(
    request: HttpRequest,
    tenant_id: UUID,
    group: str = "status",
    segment: str = "",
    channel: str = "",
    offer: str = "",
    language: str = "",
    environment: str = "",
    since: date | None = None,
    until: date | None = None,
) -> dict[str, Any]:
    scope = await sync_to_async(scope_for)(request, tenant_id)
    try:
        query = AggregateQuery.model_validate(
            {
                "group": group,
                "segment": segment,
                "channel": channel,
                "offer": offer,
                "language": language,
                "environment": environment,
                "since": since,
                "until": until,
            }
        )
    except ValueError:
        raise HttpError(422, "Agrupación no válida.") from None
    return await DjangoRecruiterReader().aggregates(scope, query)


class AskInput(Schema):
    question: str = Field(min_length=1, max_length=2000)


@router.get("/tenants/{tenant_id}/recruiter/history", response=list[QueryHistoryOut])
def history(request: HttpRequest, tenant_id: UUID) -> list[dict[str, Any]]:
    scope = scope_for(request, tenant_id)
    with tenant_context(tenant_id):
        return [
            dict(row)
            for row in AuditEvent.objects.filter(
                tenant_id=tenant_id,
                actor_id=scope.user_id,
                action="recruiter.query",
                payload__segments=list(scope.segments),
            )
            .order_by("-created_at")
            .values("id", "created_at", "payload")[:20]
        ]


@sync_to_async
def audit(scope: RecruiterScope, question: str, reply: str) -> None:
    with tenant_context(scope.tenant_id):
        AuditEvent.objects.create(
            tenant_id=scope.tenant_id,
            actor_id=scope.user_id,
            action="recruiter.query",
            payload={"question": question, "reply": reply, "segments": list(scope.segments)},
        )


@router.post("/tenants/{tenant_id}/recruiter/ask", response=dict[str, str])
async def ask(request: HttpRequest, tenant_id: UUID, payload: AskInput) -> dict[str, str]:
    # The assistant answers from aggregates only; it never receives candidate quotes.
    scope = replace(await sync_to_async(scope_for)(request, tenant_id), conversations=False)
    c = get_container()
    tenant = await c.conversations.repo.get_tenant(tenant_id)
    active = await c.conversations.versions.active(tenant_id)
    grants = frozenset(active.content.grants.recruiter) if active else frozenset()
    ctx = RunContext(
        "recruiter",
        tenant_id,
        tenant.slug,
        "tenant",
        "recruiter",
        None,
        None,
        active.id if active else None,
        grants,
        {},
        allowed_segments=scope.segments,
    )
    reply = await c.recruiter.answer(ctx, scope, payload.question, DjangoRecruiterReader())
    await audit(scope, payload.question, reply)
    return {"reply": reply}
