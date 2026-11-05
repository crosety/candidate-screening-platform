from dataclasses import fields
from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db.models import Sum

from screening.application.ports import EvalCaseRecord, EvalRunRecord
from screening.domain.errors import NotFound
from web.evals.models import EvalCase, EvalRun
from web.tenancy.context import tenant_context
from web.tracing.models import LlmCall

RUN_CHANGES = {
    "status",
    "score_draft",
    "score_published",
    "summary",
    "finished_at",
    "last_error",
    "alternative_content",
}


def _run(row: EvalRun) -> EvalRunRecord:
    return EvalRunRecord(
        **{field.name: getattr(row, field.name) for field in fields(EvalRunRecord)}
    )


def _case(row: EvalCase) -> EvalCaseRecord:
    return EvalCaseRecord(
        **{
            field.name: row.judge.get("verdict", "")
            if field.name == "verdict"
            else getattr(row, field.name)
            for field in fields(EvalCaseRecord)
        }
    )


class DjangoEvalRepository:
    @sync_to_async
    def create_run(
        self,
        tenant_id: UUID,
        draft_rev: int,
        published_version_id: UUID | None,
        persona_keys: list[str],
        compare_published: bool,
        max_cost_usd_micros: int,
        user_id: UUID,
    ) -> EvalRunRecord:
        with tenant_context(tenant_id):
            return _run(
                EvalRun.objects.create(
                    tenant_id=tenant_id,
                    draft_rev=draft_rev,
                    published_version_id=published_version_id,
                    persona_keys=persona_keys,
                    compare_published=compare_published,
                    max_cost_usd_micros=max_cost_usd_micros,
                    created_by_id=user_id,
                )
            )

    @sync_to_async
    def get_run(self, tenant_id: UUID, run_id: UUID) -> EvalRunRecord:
        with tenant_context(tenant_id):
            try:
                return _run(EvalRun.objects.get(tenant_id=tenant_id, pk=run_id))
            except EvalRun.DoesNotExist:
                raise NotFound("Ejecución no encontrada") from None

    @sync_to_async
    def list_runs(self, tenant_id: UUID) -> list[EvalRunRecord]:
        with tenant_context(tenant_id):
            return [
                _run(row)
                for row in EvalRun.objects.filter(tenant_id=tenant_id).order_by("-created_at")
            ]

    @sync_to_async
    def set_run(self, tenant_id: UUID, run_id: UUID, **changes: Any) -> None:
        if changes.keys() - RUN_CHANGES:
            raise ValueError("Campo de ejecución no permitido")
        with tenant_context(tenant_id):
            if not EvalRun.objects.filter(tenant_id=tenant_id, pk=run_id).update(**changes):
                raise NotFound("Ejecución no encontrada")

    @sync_to_async
    def create_case(
        self, tenant_id: UUID, run_id: UUID, persona_key: str, target: str
    ) -> EvalCaseRecord:
        with tenant_context(tenant_id):
            if not EvalRun.objects.filter(tenant_id=tenant_id, pk=run_id).exists():
                raise NotFound("Ejecución no encontrada")
            return _case(
                EvalCase.objects.create(
                    tenant_id=tenant_id, run_id=run_id, persona_key=persona_key, target=target
                )
            )

    @sync_to_async
    def list_cases(self, tenant_id: UUID, run_id: UUID) -> list[EvalCaseRecord]:
        with tenant_context(tenant_id):
            return [
                _case(row)
                for row in EvalCase.objects.filter(tenant_id=tenant_id, run_id=run_id).order_by(
                    "created_at", "id"
                )
            ]

    @sync_to_async
    def finish_case(
        self,
        tenant_id: UUID,
        case_id: UUID,
        transcript: list[dict[str, str]],
        judge: dict[str, Any],
        verdict: str,
        score: float | None,
        status: str,
        cost_usd_micros: int,
    ) -> None:
        with tenant_context(tenant_id):
            if not EvalCase.objects.filter(tenant_id=tenant_id, pk=case_id).update(
                transcript=transcript,
                judge={**judge, "verdict": verdict},
                score=score,
                status=status,
                cost_usd_micros=cost_usd_micros,
            ):
                raise NotFound("Caso no encontrado")

    @sync_to_async
    def claim_run(self, tenant_id: UUID, run_id: UUID) -> bool:
        with tenant_context(tenant_id):
            return bool(
                EvalRun.objects.filter(tenant_id=tenant_id, pk=run_id, status="queued").update(
                    status="running"
                )
            )

    @sync_to_async
    def case_cost(self, tenant_id: UUID, case_id: UUID) -> int:
        with tenant_context(tenant_id):
            return int(
                LlmCall.objects.filter(tenant_id=tenant_id, eval_case_id=case_id).aggregate(
                    total=Sum("cost_usd_micros")
                )["total"]
                or 0
            )
