from dataclasses import asdict
from uuid import UUID

from asgiref.sync import async_to_sync
from django.conf import settings
from django.http import HttpRequest
from ninja import Router

from adapters.persistence.evals_gate import passing_eval_run
from screening.application.ports import EvalRunRecord
from screening.domain.evals import Persona
from screening.domain.languages import language_variants
from screening.platform.container import get_container
from web.evals.schemas import EvalCaseOut, EvalIn, EvalRunDetailOut, EvalRunOut, GateOut
from web.tenancy.permissions import require_role

router = Router()


def _run(tenant_id: UUID, run: EvalRunRecord) -> EvalRunOut:
    cases = async_to_sync(get_container().evals.evals.list_cases)(tenant_id, run.id)
    return EvalRunOut.model_validate(
        {**asdict(run), "cost_usd_micros": sum(c.cost_usd_micros for c in cases)}
    )


@router.get("/tenants/{tenant_id}/personas", response=list[Persona])
def personas(
    request: HttpRequest, tenant_id: UUID, languages: bool = False, offer_key: str = ""
) -> list[Persona]:
    require_role(request, tenant_id, {"admin", "fde"})
    service = get_container().evals
    tenant = async_to_sync(service.tenants.get_tenant)(tenant_id)
    if offer_key and offer_key not in {
        o.key for o in get_container().profiles.get_draft(tenant_id).content.offers
    }:
        from ninja.errors import HttpError

        raise HttpError(400, "Oferta no válida")
    base = service.personas.list_personas(tenant.slug, offer_key)
    return [*base, *language_variants(base)] if languages else base


@router.post("/tenants/{tenant_id}/evals", response=EvalRunOut)
def start(request: HttpRequest, tenant_id: UUID, payload: EvalIn) -> EvalRunOut:
    member = require_role(request, tenant_id, {"admin", "fde"})
    run = async_to_sync(get_container().evals.start)(
        tenant_id,
        payload.persona_keys,
        payload.compare_published,
        payload.max_cost_usd,
        member.user_id,
        offer_key=payload.offer_key,
    )
    return _run(tenant_id, run)


@router.get("/tenants/{tenant_id}/evals", response=list[EvalRunOut])
def runs(request: HttpRequest, tenant_id: UUID) -> list[EvalRunOut]:
    require_role(request, tenant_id, {"admin", "fde"})
    return [
        _run(tenant_id, run)
        for run in async_to_sync(get_container().evals.evals.list_runs)(tenant_id)
    ]


@router.get("/tenants/{tenant_id}/evals/{run_id}", response=EvalRunDetailOut)
def detail(request: HttpRequest, tenant_id: UUID, run_id: UUID) -> EvalRunDetailOut:
    require_role(request, tenant_id, {"admin", "fde"})
    service = get_container().evals.evals
    run = async_to_sync(service.get_run)(tenant_id, run_id)
    return EvalRunDetailOut(
        **_run(tenant_id, run).model_dump(),
        cases=[
            EvalCaseOut.model_validate(asdict(c))
            for c in async_to_sync(service.list_cases)(tenant_id, run_id)
        ],
    )


@router.get("/tenants/{tenant_id}/draft/gate", response=GateOut)
def gate(request: HttpRequest, tenant_id: UUID) -> GateOut:
    require_role(request, tenant_id, {"admin", "fde"})
    draft = get_container().profiles.get_draft(tenant_id)
    run = passing_eval_run(tenant_id, draft.rev)
    return GateOut(
        required=settings.PUBLISH_REQUIRES_EVALS,
        passed=run is not None,
        run_id=str(run.id) if run else None,
        score=run.score_draft if run else None,
        threshold=draft.content.eval_threshold,
    )
