import json
from dataclasses import asdict
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db.models import Count

from adapters.privacy import pseudonymize
from screening.application.copilot import allowed_brief_fields
from screening.application.ports import CopilotMaterial
from screening.application.prompts import build_copilot_material
from screening.domain.diff import diff_profiles
from screening.domain.errors import Conflict, NotAllowed, NotFound
from screening.domain.profile import ProfileContent
from screening.domain.run_context import RunContext
from screening.platform.container import get_container
from web.conversations.models import Message, Session
from web.decisions.models import Decision
from web.evals.models import EvalCase, EvalRun
from web.profiles.models import ImprovementProposal, ProfileDraft
from web.tenancy.context import tenant_context


@sync_to_async
def material(tenant_id: UUID) -> dict[str, Any]:
    with tenant_context(tenant_id):
        rows = list(
            Session.objects.filter(tenant_id=tenant_id)
            .select_related("application")
            .order_by("-created_at")[:10]
        )
        evidence = []
        for row in rows:
            for message in Message.objects.filter(tenant_id=tenant_id, session=row).order_by(
                "-created_at"
            )[:6]:
                evidence.append(
                    {
                        "id": str(message.id),
                        "session_id": str(row.id),
                        "direction": message.direction,
                        "text": pseudonymize(
                            message.text, row.application.candidate_name, row.application.phone
                        )[:1200],
                        "url": f"/t/{tenant_id}/conversaciones/{row.id}",
                    }
                )
        return {
            "evidence": evidence,
            "counts": list(
                Session.objects.filter(tenant_id=tenant_id)
                .values("status")
                .annotate(count=Count("id"))
            ),
            "privacy": "Fragmentos seudonimizados: no equivalen a anonimización completa.",
            "corrections": list(
                Decision.objects.filter(tenant_id=tenant_id, status="overridden")
                .values("decision_type")
                .annotate(count=Count("id"))
            ),
            "eval_observations": [
                {
                    "case_id": str(case.id),
                    "score": case.score,
                    "rubric": [
                        {"key": item.get("key"), "score": item.get("score")}
                        for item in case.judge.get("items", [])
                    ],
                }
                for case in EvalCase.objects.filter(
                    tenant_id=tenant_id, status="done", score__lt=85
                ).order_by("-created_at")[:20]
            ],
        }


async def propose(
    tenant_id: UUID, user_id: UUID, max_cost_usd: float = 0.1, execute: bool = False
) -> list[str]:
    c = get_container()
    draft = await c.conversations.versions.draft(tenant_id)
    tenant = await c.conversations.repo.get_tenant(tenant_id)
    observations = await material(tenant_id)
    if not observations["evidence"]:
        return []
    ctx = RunContext(
        "copiloto", tenant_id, tenant.slug, "tenant", "fde", None, None, None, frozenset(), {}
    )
    suggestions = await c.copilot.copilot.suggest(
        ctx,
        "brief",
        json.dumps(
            {
                **json.loads(build_copilot_material(draft, CopilotMaterial([], [], []))),
                "observations": observations,
                "instruction": (
                    "Mejora la claridad operativa sin alterar requisitos ni hechos. "
                    "Para cada sugerencia incluye evidence_ids con IDs de los fragmentos que "
                    "justifican específicamente ese cambio. "
                    "Si no hay evidencia, no propongas cambios."
                ),
            },
            ensure_ascii=False,
        ),
    )
    result = []
    for suggestion in suggestions[:1]:
        if (
            suggestion.field not in allowed_brief_fields(draft.content)
            or not suggestion.append.strip()
        ):
            continue
        selected = [e for e in observations["evidence"] if e["id"] in suggestion.evidence_ids]
        if not selected or set(suggestion.evidence_ids) != {e["id"] for e in selected}:
            continue
        content = draft.content.model_copy(deep=True)
        old = getattr(content.brief, suggestion.field)
        setattr(content.brief, suggestion.field, old + "\n" + suggestion.append.strip())
        pk = await store(tenant_id, user_id, draft.rev, content, suggestion.why, selected)
        run = await c.evals.start(
            tenant_id, None, True, max_cost_usd, user_id, enqueue=not execute, alternative=content
        )
        await attach_evaluation(tenant_id, pk, run.id)
        if execute:
            await c.evals.execute(tenant_id, run.id)
        result.append(str(pk))
    return result


@sync_to_async
def attach_evaluation(tenant_id: UUID, proposal_id: UUID, run_id: UUID) -> None:
    with tenant_context(tenant_id):
        ImprovementProposal.objects.filter(pk=proposal_id, tenant_id=tenant_id).update(
            eval_run_id=run_id, state="evaluating"
        )


@sync_to_async
def store(
    tenant_id: UUID,
    user_id: UUID,
    rev: int,
    content: ProfileContent,
    rationale: str,
    evidence: list[dict[str, str]],
) -> UUID:
    with tenant_context(tenant_id):
        draft = ProfileDraft.objects.select_for_update().get(tenant_id=tenant_id)
        if draft.rev != rev:
            raise Conflict("El borrador ha cambiado durante la propuesta.")
        row = ImprovementProposal.objects.create(
            tenant_id=tenant_id,
            base_rev=rev,
            content=content.model_dump(mode="json"),
            rationale=rationale,
            evidence=evidence,
            diff=asdict(diff_profiles(ProfileContent.model_validate(draft.content), content, True)),
            created_by_id=user_id,
        )
        return row.id


def get_proposal(tenant_id: UUID, proposal_id: UUID) -> ImprovementProposal:
    row = (
        ImprovementProposal.objects.select_for_update()
        .filter(tenant_id=tenant_id, pk=proposal_id)
        .first()
    )
    if row is None:
        raise NotFound("Propuesta no encontrada")
    return row


def passed(row: ImprovementProposal) -> bool:
    if row.eval_run_id is None:
        return False
    run = EvalRun.objects.get(tenant_id=row.tenant_id, pk=row.eval_run_id)
    from adapters.persistence.evals_gate import run_passes

    return (
        run.alternative_content == row.content
        and run_passes(run, ProfileContent.model_validate(row.content))
        and run.score_published is not None
        and run.score_draft is not None
        and run.score_draft >= run.score_published
    )


def accept(tenant_id: UUID, proposal_id: UUID, user_id: UUID) -> int:
    with tenant_context(tenant_id):
        row = get_proposal(tenant_id, proposal_id)
        if row.state not in {"proposed", "evaluating", "ready"} or not passed(row):
            raise NotAllowed("La propuesta necesita evals completas y aprobadas.")
        draft = ProfileDraft.objects.select_for_update().get(tenant_id=tenant_id)
        if draft.rev != row.base_rev:
            raise Conflict("El borrador ha cambiado; genera y evalúa una nueva propuesta.")
        updated = get_container().profiles.save_draft(
            tenant_id, draft.rev, ProfileContent.model_validate(row.content), user_id
        )
        row.state, row.accepted_rev = "accepted", updated.rev
        row.save(update_fields=["state", "accepted_rev"])
        return updated.rev
