import asyncio
from dataclasses import replace
from typing import Any, ClassVar
from uuid import UUID
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo

from screening.application.ports import (
    Clock,
    ConversationRepository,
    DecisionRecord,
    DecisionRepository,
    EvaluationRequest,
    Evaluator,
    JobQueue,
    ProfileVersions,
    ResultRecord,
)
from screening.domain.autonomy import DECISION_FOR_VERDICT, decide_outcome, next_step
from screening.domain.errors import NotAllowed
from screening.domain.profile import Connection, ProfileContent, profile_for_offer
from screening.domain.result import FieldValue, invalid_field, observed_sentiment, quoted_in
from screening.domain.run_context import RunContext


def writable_ats(profile: ProfileContent) -> Connection:
    connection = next(
        (c for c in profile.connections if c.kind == "ats" and c.permission == "write"), None
    )
    if connection is None:
        raise NotAllowed("No hay un ATS con permiso de escritura; requiere revisión humana.")
    return connection


async def enqueue_write_result(
    decisions: DecisionRepository,
    conversations: ConversationRepository,
    queue: JobQueue,
    tenant_id: UUID,
    session_id: UUID,
    decision: DecisionRecord,
    verdict: str,
    result: ResultRecord,
    profile: ProfileContent,
) -> None:
    session = await conversations.get_session(tenant_id, session_id)
    application = await conversations.get_application(tenant_id, session.application_id)
    ats = writable_ats(profile)
    fields = {}
    for field in profile.result_schema:
        value = result.fields.get(field.key, {}).get("value")
        if value is not None:
            fields[field.ats_field] = value
    item = await decisions.create_outbox(
        tenant_id,
        session_id,
        "ats.write_result",
        {
            "application_external_id": application.external_id,
            "verdict": verdict,
            "fields": fields,
            "summary": result.summary,
            "stage": ats.settings.get(f"stage_{verdict}", ""),
        },
        f"write_result:{decision.id}",
    )
    await queue.enqueue(
        "outbox_dispatch", {"outbox_item_id": str(item.id)}, tenant_id, f"outbox:{item.id}"
    )


class AssessmentService:
    _locks: ClassVar[WeakValueDictionary[tuple[UUID, UUID], asyncio.Lock]] = WeakValueDictionary()

    def __init__(
        self,
        decisions: DecisionRepository,
        conversations: ConversationRepository,
        versions: ProfileVersions,
        evaluator: Evaluator,
        queue: JobQueue,
        clock: Clock,
        evaluator_model: str,
    ) -> None:
        self.decisions, self.conversations, self.versions = decisions, conversations, versions
        self.evaluator, self.queue, self.clock = evaluator, queue, clock
        self.evaluator_model = evaluator_model

    async def complete(self, ctx: RunContext) -> dict[str, str]:
        if ctx.session_id is None or ctx.profile_version_id is None:
            raise NotAllowed("La evaluación necesita una sesión y una versión.")
        async with self._locks.setdefault((ctx.tenant_id, ctx.session_id), asyncio.Lock()):
            return await self._complete(ctx, ctx.session_id, ctx.profile_version_id)

    async def _complete(
        self, ctx: RunContext, session_id: UUID, version_id: UUID
    ) -> dict[str, str]:
        session = await self.conversations.get_session(ctx.tenant_id, session_id)
        if session.status in ("handed_off", "abandoned"):
            return {
                "verdict": "review",
                "next_step": "explain_review",
                "reason": "La sesión requiere atención humana.",
            }
        existing = await self.decisions.get_result(ctx.tenant_id, session_id)
        if existing is not None:
            applied = await self.decisions.has_applied_qualify(ctx.tenant_id, session_id)
            return {
                "verdict": existing.verdict,
                "next_step": next_step(existing.verdict, applied),
                "reason": existing.reason,
            }
        profile = profile_for_offer(
            (await self.versions.get(ctx.tenant_id, version_id)).content, ctx.offer_key
        )
        transcript = await self.conversations.list_messages(ctx.tenant_id, session_id)
        fields: dict[str, Any]
        flags: list[str]
        unconfirmed_fields: dict[str, Any] = {}
        sentiment: list[dict[str, str | None]] = []
        try:
            output = await self.evaluator.evaluate(
                EvaluationRequest(
                    replace(ctx, agent="evaluador", talks_to="nobody", granted=frozenset()),
                    profile,
                    transcript,
                    await self.conversations.tool_evidence(ctx.tenant_id, session_id),
                )
            )
            verdict, reason, summary, flags = (
                output.verdict,
                output.reason,
                output.summary,
                list(output.flags),
            )
            fields = {
                field.key: output.fields.get(
                    field.key, FieldValue(value=None, evidence=None, confidence="low")
                ).model_dump()
                for field in profile.result_schema
            }
            application = await self.conversations.get_application(
                ctx.tenant_id, session.application_id
            )
            today = self.clock.now().astimezone(ZoneInfo(application.timezone)).date()
            failures = [
                field.key
                for field in profile.result_schema
                # A missing free-text answer only blocks a hire; pool/no-fit may lack a site.
                if (
                    field.value_type != "text"
                    or verdict == "qualified"
                    or fields[field.key]["value"] not in (None, "")
                )
                and invalid_field(
                    field,
                    output.fields.get(
                        field.key, FieldValue(value=None, evidence=None, confidence="low")
                    ),
                    today,
                )
            ]
            for field in profile.result_schema:
                item = fields[field.key]
                if (
                    item["value"] is not None
                    and item["value"] != ""
                    and field.key not in failures
                    and not quoted_in(
                        item["evidence"],
                        [message.text for message in transcript if message.direction == "in"],
                    )
                ):
                    failures.append(field.key)
            excluded = [
                field.key
                for field in profile.result_schema
                if field.exclude_if_false
                and fields[field.key]["value"] is False
                and field.key not in failures
            ]
            if excluded:
                verdict = "not_qualified"
                reason = "Requisito excluyente no cumplido: " + ", ".join(excluded)
            elif failures and verdict in ("qualified", "pool", "not_qualified"):
                # No decision or ATS mutation until the candidate can correct the data.
                return {
                    "verdict": "review",
                    "next_step": "clarify_fields",
                    "reason": "Aclara los datos con formato inválido o pendientes: "
                    + ", ".join(failures),
                }
            if failures:
                unconfirmed_fields = {key: fields[key] for key in failures}
                for key in failures:
                    fields[key] = FieldValue(
                        value=None, evidence=None, confidence="low"
                    ).model_dump()
                if "unclear_requirement" not in flags:
                    flags.append("unclear_requirement")
            sentiment = observed_sentiment(
                output.sentiment, [m.text for m in transcript if m.direction == "in"]
            )
        except Exception:
            verdict = "review"
            reason = summary = "No se ha podido evaluar automáticamente."
            flags = ["evaluation_failed"]
            fields = {
                field.key: {"value": None, "evidence": None, "confidence": "low"}
                for field in profile.result_schema
            }
        result = ResultRecord(
            session_id, verdict, fields, flags, summary, reason, self.evaluator_model, sentiment
        )
        kind = DECISION_FOR_VERDICT[verdict]
        level = ctx.autonomy.get(kind, 0)
        applied = decide_outcome(kind, level, flags) == "apply"
        if applied and not any(
            c.kind == "ats" and c.permission == "write" for c in profile.connections
        ):
            applied = False
            flags.append("ats_not_writable")
            result = replace(result, flags=flags)
        await self.decisions.save_result(ctx.tenant_id, result)
        decision = await self.decisions.create_decision(
            ctx.tenant_id,
            session_id,
            kind,
            "applied" if applied else "proposed",
            level,
            "agent",
            None,
            reason,
            self.clock.now() if applied else None,
        )
        if applied:
            await enqueue_write_result(
                self.decisions,
                self.conversations,
                self.queue,
                ctx.tenant_id,
                session_id,
                decision,
                verdict,
                result,
                profile,
            )
        if not (verdict == "qualified" and applied):
            await self.conversations.set_session_status(ctx.tenant_id, session_id, "completed")
        await self.conversations.add_event(
            ctx.tenant_id,
            session_id,
            "assessment_completed",
            {
                "verdict": verdict,
                "decision_status": decision.status,
                "flags": flags,
                **({"unconfirmed_fields": unconfirmed_fields} if unconfirmed_fields else {}),
            },
        )
        return {"verdict": verdict, "next_step": next_step(verdict, applied), "reason": reason}


class DecisionBookingPolicy:
    def __init__(
        self, decisions: DecisionRepository, conversations: ConversationRepository, queue: JobQueue
    ) -> None:
        self.decisions, self.conversations, self.queue = decisions, conversations, queue

    async def can_book(self, ctx: RunContext) -> bool:
        if ctx.session_id is None:
            return False
        session = await self.conversations.get_session(ctx.tenant_id, ctx.session_id)
        return session.status == "active" and await self.decisions.has_applied_qualify(
            ctx.tenant_id, ctx.session_id
        )

    async def booked(
        self, ctx: RunContext, profile: ProfileContent, booking: dict[str, Any]
    ) -> None:
        if ctx.session_id is None:
            raise NotAllowed("La reserva necesita una sesión.")
        session = await self.conversations.get_session(ctx.tenant_id, ctx.session_id)
        application = await self.conversations.get_application(
            ctx.tenant_id, session.application_id
        )
        ats = writable_ats(profile)
        item = await self.decisions.create_outbox(
            ctx.tenant_id,
            ctx.session_id,
            "ats.set_stage",
            {
                "application_external_id": application.external_id,
                "stage": ats.settings.get("stage_interview", ""),
                "note": f"Entrevista: {booking['starts_at_local']} en {booking['site_name']}",
            },
            f"set_stage:{ctx.session_id}:interview",
        )
        await self.queue.enqueue(
            "outbox_dispatch", {"outbox_item_id": str(item.id)}, ctx.tenant_id, f"outbox:{item.id}"
        )
        await self.conversations.set_session_status(ctx.tenant_id, ctx.session_id, "completed")
