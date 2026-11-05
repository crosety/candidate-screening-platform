from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from screening.application.ports import EvalCaseRecord, EvalRunRecord, MessageRecord, ResultRecord
from screening.domain.errors import NotFound
from screening.domain.evals import JudgeCheck, JudgeItem, JudgeOutput, Persona
from screening.domain.profile import ProfileContent
from screening.domain.run_context import RunContext


class ScriptedSimulator:
    def __init__(self, messages: list[str | None]) -> None:
        self.messages = list(messages)

    async def next_message(
        self, ctx: RunContext, persona: Persona, transcript: list[MessageRecord]
    ) -> str | None:
        return self.messages.pop(0) if self.messages else None


class ScriptedJudge:
    def __init__(self, outputs: list[JudgeOutput]) -> None:
        self.outputs = list(outputs)

    async def judge(
        self,
        ctx: RunContext,
        profile: ProfileContent,
        persona: Persona,
        transcript: list[MessageRecord],
        result: ResultRecord | None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> JudgeOutput:
        return (self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]).model_copy(
            deep=True
        )


class PerfectJudge:
    async def judge(
        self,
        ctx: RunContext,
        profile: ProfileContent,
        persona: Persona,
        transcript: list[MessageRecord],
        result: ResultRecord | None,
        evidence: list[dict[str, Any]] | None = None,
    ) -> JudgeOutput:
        return JudgeOutput(
            items=[
                JudgeItem(key=item.key, score=100, comment="Modo de prueba")
                for item in profile.rubric
            ],
            checks=[
                JudgeCheck(text=prefix + text, passed=True, evidence=None)
                for prefix, texts in (("Debe: ", persona.must), ("No debe: ", persona.must_not))
                for text in texts
            ],
            comment="Modo de prueba",
        )


class StaticPersonaSource:
    def __init__(self, personas: list[Persona]) -> None:
        self.personas = personas

    def list_personas(self, tenant_slug: str, offer_key: str = "") -> list[Persona]:
        return deepcopy(self.personas)


class InMemoryEvalRepository:
    def __init__(self) -> None:
        self.runs: dict[tuple[UUID, UUID], EvalRunRecord] = {}
        self.cases: dict[tuple[UUID, UUID], EvalCaseRecord] = {}
        self.costs: dict[UUID, int] = {}
        self.errors: dict[UUID, str] = {}

    async def create_run(
        self,
        tenant_id: UUID,
        draft_rev: int,
        published_version_id: UUID | None,
        persona_keys: list[str],
        compare_published: bool,
        max_cost_usd_micros: int,
        user_id: UUID,
    ) -> EvalRunRecord:
        row = EvalRunRecord(
            uuid4(),
            tenant_id,
            draft_rev,
            published_version_id,
            "queued",
            list(persona_keys),
            compare_published,
            max_cost_usd_micros,
            None,
            None,
            {},
            datetime.now(UTC),
            None,
        )
        self.runs[(tenant_id, row.id)] = row
        return deepcopy(row)

    async def get_run(self, tenant_id: UUID, run_id: UUID) -> EvalRunRecord:
        try:
            return deepcopy(self.runs[(tenant_id, run_id)])
        except KeyError:
            raise NotFound("Ejecución no encontrada") from None

    async def list_runs(self, tenant_id: UUID) -> list[EvalRunRecord]:
        return deepcopy(
            sorted(
                [r for (tenant, _), r in self.runs.items() if tenant == tenant_id],
                key=lambda r: r.created_at,
                reverse=True,
            )
        )

    async def set_run(self, tenant_id: UUID, run_id: UUID, **changes: Any) -> None:
        if changes.keys() - {
            "status",
            "score_draft",
            "score_published",
            "summary",
            "finished_at",
            "last_error",
            "alternative_content",
        }:
            raise ValueError("Campo de ejecución no permitido")
        row = await self.get_run(tenant_id, run_id)
        self.errors[run_id] = changes.pop("last_error", self.errors.get(run_id, ""))
        self.runs[(tenant_id, run_id)] = replace(row, **deepcopy(changes))

    async def create_case(
        self, tenant_id: UUID, run_id: UUID, persona_key: str, target: str
    ) -> EvalCaseRecord:
        await self.get_run(tenant_id, run_id)
        row = EvalCaseRecord(uuid4(), run_id, persona_key, target, [], {}, "", None, 0, "pending")
        self.cases[(tenant_id, row.id)] = row
        return deepcopy(row)

    async def list_cases(self, tenant_id: UUID, run_id: UUID) -> list[EvalCaseRecord]:
        return deepcopy(
            [
                r
                for (tenant, _), r in self.cases.items()
                if tenant == tenant_id and r.run_id == run_id
            ]
        )

    async def finish_case(
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
        key = (tenant_id, case_id)
        if key not in self.cases:
            raise NotFound("Caso no encontrado")
        self.cases[key] = replace(
            self.cases[key],
            transcript=deepcopy(transcript),
            judge={**deepcopy(judge), "verdict": verdict},
            verdict=verdict,
            score=score,
            status=status,
            cost_usd_micros=cost_usd_micros,
        )

    async def claim_run(self, tenant_id: UUID, run_id: UUID) -> bool:
        row = await self.get_run(tenant_id, run_id)
        if row.status != "queued":
            return False
        self.runs[(tenant_id, run_id)] = replace(row, status="running")
        return True

    async def case_cost(self, tenant_id: UUID, case_id: UUID) -> int:
        return self.costs.get(case_id, 0) if (tenant_id, case_id) in self.cases else 0
