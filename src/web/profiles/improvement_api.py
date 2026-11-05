from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from asgiref.sync import async_to_sync, sync_to_async
from django.db.models import Count
from django.http import HttpRequest
from ninja import Router, Schema
from pydantic import Field

from screening.domain.errors import Conflict, NotAllowed
from screening.domain.profile import ProfileContent
from screening.platform.container import get_container
from web.conversations.models import Session
from web.jobs.models import Job
from web.profiles.improvement import accept, get_proposal, passed, propose
from web.profiles.models import (
    BudgetReservation,
    ImprovementProposal,
    MaintenancePolicy,
    SegmentPilot,
)
from web.profiles.operation_schemas import (
    ImprovementOut,
    MaintenanceOut,
    PilotsOut,
    ProposalActionOut,
    ProposalIdsOut,
)
from web.tenancy.context import tenant_context
from web.tenancy.permissions import require_role

router = Router()


@router.get("/tenants/{tenant_id}/improvements", response=list[ImprovementOut])
def proposals(request: HttpRequest, tenant_id: UUID) -> list[dict[str, Any]]:
    require_role(request, tenant_id, {"admin", "fde"})
    with tenant_context(tenant_id):
        result = []
        for row in ImprovementProposal.objects.filter(tenant_id=tenant_id).order_by("-created_at")[
            :100
        ]:
            ready = passed(row)
            result.append(
                {
                    "id": row.id,
                    "base_rev": row.base_rev,
                    "state": "ready" if ready and row.state == "evaluating" else row.state,
                    "rationale": row.rationale,
                    "diff": row.diff,
                    "evidence": row.evidence,
                    "eval_run_id": row.eval_run_id,
                    "created_at": row.created_at,
                    "accepted_rev": row.accepted_rev,
                }
            )
        return result


@router.post("/tenants/{tenant_id}/improvements", response=ProposalIdsOut)
async def create(request: HttpRequest, tenant_id: UUID) -> dict[str, Any]:
    member = await sync_to_async(require_role)(request, tenant_id, {"admin", "fde"})
    return {"ids": await propose(tenant_id, member.user_id)}


class ProposalAction(Schema):
    action: str = Field(pattern="^(evaluate|accept|reject|cancel)$")
    max_cost_usd: float = Field(default=0.1, ge=0.1, le=1)


@router.post("/tenants/{tenant_id}/improvements/{proposal_id}", response=ProposalActionOut)
def act(
    request: HttpRequest, tenant_id: UUID, proposal_id: UUID, payload: ProposalAction
) -> dict[str, Any]:
    member = require_role(request, tenant_id, {"admin", "fde"})
    if payload.action == "accept":
        return {"rev": accept(tenant_id, proposal_id, member.user_id)}
    with tenant_context(tenant_id):
        row = get_proposal(tenant_id, proposal_id)
        if row.state not in {"proposed", "evaluating", "ready"}:
            raise NotAllowed("Esta propuesta ya está cerrada.")
        if payload.action in {"reject", "cancel"}:
            row.state = "rejected"
            if row.eval_run_id:
                queued = Job.objects.filter(
                    tenant_id=tenant_id,
                    kind="eval_run",
                    status="queued",
                    payload__run_id=str(row.eval_run_id),
                )
                queued.update(status="cancelled")
        else:
            if row.eval_run_id:
                raise NotAllowed("Esta propuesta ya tiene una evaluación.")
            draft = get_container().profiles.get_draft(tenant_id)
            if draft.rev != row.base_rev:
                raise Conflict("El borrador ha cambiado.")
            run = async_to_sync(get_container().evals.start)(
                tenant_id,
                None,
                True,
                payload.max_cost_usd,
                member.user_id,
                alternative=ProfileContent.model_validate(row.content),
            )
            row.eval_run_id, row.state = run.id, "evaluating"
        row.save(update_fields=["state", "eval_run"])
        return {"state": row.state, "eval_run_id": row.eval_run_id}


class PolicyInput(Schema):
    nightly_enabled: bool = False
    improvement_enabled: bool = False
    timezone: str = Field(default="Europe/Madrid", max_length=80)
    nightly_hour: int = Field(default=3, ge=0, le=23)
    judge_percent: int = Field(default=0, ge=0, le=100)
    daily_budget_micros: int = Field(default=300000, ge=100000, le=1000000)
    run_budget_micros: int = Field(default=100000, ge=100000, le=500000)


@router.get("/tenants/{tenant_id}/maintenance", response=MaintenanceOut)
def policy(request: HttpRequest, tenant_id: UUID) -> dict[str, Any]:
    member = require_role(request, tenant_id, {"admin", "fde"})
    with tenant_context(tenant_id):
        row, _ = MaintenancePolicy.objects.get_or_create(
            tenant_id=tenant_id, defaults={"updated_by_id": member.user_id}
        )
        return {
            "policy": {key: getattr(row, key) for key in PolicyInput.model_fields},
            "reservations": list(
                BudgetReservation.objects.filter(tenant_id=tenant_id)
                .order_by("-created_at")
                .values("id", "kind", "reserved_micros", "actual_micros", "state", "day")[:100]
            ),
        }


@router.put("/tenants/{tenant_id}/maintenance", response=MaintenanceOut)
def save_policy(request: HttpRequest, tenant_id: UUID, payload: PolicyInput) -> dict[str, Any]:
    member = require_role(request, tenant_id, {"admin", "fde"})
    try:
        ZoneInfo(payload.timezone)
    except ZoneInfoNotFoundError:
        raise NotAllowed("Zona horaria no válida.") from None
    if payload.run_budget_micros > payload.daily_budget_micros:
        raise NotAllowed("El presupuesto por ejecución supera el diario.")
    with tenant_context(tenant_id):
        MaintenancePolicy.objects.update_or_create(
            tenant_id=tenant_id, defaults={**payload.model_dump(), "updated_by_id": member.user_id}
        )
    return policy(request, tenant_id)


@router.post("/tenants/{tenant_id}/maintenance/{reservation_id}/cancel", response=dict[str, str])
def cancel_reservation(
    request: HttpRequest, tenant_id: UUID, reservation_id: UUID
) -> dict[str, str]:
    from web.profiles.maintenance import cancel

    require_role(request, tenant_id, {"admin", "fde"})
    cancel(tenant_id, reservation_id)
    return {"status": "cancelled_if_queued"}


class PilotInput(Schema):
    segment: str = Field(min_length=1, max_length=60)
    version_id: UUID
    baseline_id: UUID
    percent: int = Field(default=10, ge=1, le=100)
    enabled: bool = True


@router.get("/tenants/{tenant_id}/pilots", response=PilotsOut)
def pilots(request: HttpRequest, tenant_id: UUID) -> dict[str, Any]:
    require_role(request, tenant_id, {"admin", "fde"})
    with tenant_context(tenant_id):
        return {
            "pilots": list(
                SegmentPilot.objects.filter(tenant_id=tenant_id).values(
                    "id", "segment", "version_id", "baseline_id", "percent", "enabled"
                )
            ),
            "metrics": list(
                Session.objects.filter(tenant_id=tenant_id)
                .values("application__segment", "profile_version__version", "status")
                .annotate(count=Count("id"))
            ),
            "note": "Comparación descriptiva; no demuestra causalidad ni significación.",
        }


@router.put("/tenants/{tenant_id}/pilots", response=PilotsOut)
def save_pilot(request: HttpRequest, tenant_id: UUID, payload: PilotInput) -> dict[str, Any]:
    require_role(request, tenant_id, {"admin", "fde"})
    if payload.enabled:
        raise NotAllowed("Crea el piloto desde Publicar: requiere evals, diff y aprobaciones.")
    with tenant_context(tenant_id):
        SegmentPilot.objects.filter(
            tenant_id=tenant_id,
            segment=payload.segment,
            version_id=payload.version_id,
            baseline_id=payload.baseline_id,
        ).update(enabled=False)
    return pilots(request, tenant_id)
