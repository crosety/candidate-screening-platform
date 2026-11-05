"""Read-only, evidence-backed view of an unfinished interview. Never makes a decision."""

from dataclasses import replace
from zoneinfo import ZoneInfo

from screening.application.ports import (
    Clock,
    ConversationRepository,
    EvaluationRequest,
    Evaluator,
    ProfileVersions,
)
from screening.domain.errors import NotAllowed
from screening.domain.profile import profile_for_offer
from screening.domain.result import FieldValue, invalid_field
from screening.domain.run_context import RunContext


class SnapshotService:
    def __init__(
        self,
        conversations: ConversationRepository,
        versions: ProfileVersions,
        evaluator: Evaluator,
        clock: Clock,
    ) -> None:
        self.conversations, self.versions = conversations, versions
        self.evaluator, self.clock = evaluator, clock

    async def capture(self, ctx: RunContext) -> None:
        if ctx.session_id is None or ctx.profile_version_id is None:
            raise NotAllowed("El resumen necesita una sesión.")
        session = await self.conversations.get_session(ctx.tenant_id, ctx.session_id)
        app = await self.conversations.get_application(ctx.tenant_id, session.application_id)
        profile = profile_for_offer(
            (await self.versions.get(ctx.tenant_id, ctx.profile_version_id)).content, ctx.offer_key
        )
        transcript = await self.conversations.list_messages(ctx.tenant_id, ctx.session_id)
        if not any(m.direction == "in" for m in transcript):
            return
        output = await self.evaluator.evaluate(
            EvaluationRequest(
                replace(
                    ctx, agent="evaluador", talks_to="nobody", granted=frozenset(), autonomy={}
                ),
                profile,
                transcript,
                await self.conversations.tool_evidence(ctx.tenant_id, ctx.session_id),
            )
        )
        today = self.clock.now().astimezone(ZoneInfo(app.timezone)).date()
        fields = {}
        for field in profile.result_schema:
            item = output.fields.get(
                field.key, FieldValue(value=None, evidence=None, confidence="low")
            )
            verified = bool(
                item.evidence
                and any(item.evidence in m.text for m in transcript if m.direction == "in")
            )
            # Capture an unmet requirement as a fact; eligibility is evaluated separately.
            typed_field = field.model_copy(update={"minimum": None, "future_date": False})
            if not verified or invalid_field(typed_field, item, today):
                item = FieldValue(value=None, evidence=None, confidence="low")
            fields[field.key] = item.model_dump()
        await self.conversations.add_event(
            ctx.tenant_id,
            ctx.session_id,
            "candidate_snapshot",
            {
                "fields": fields,
                "summary": output.summary,
                "message_count": len(transcript),
                "through": transcript[-1].created_at.isoformat(),
                "provisional": True,
            },
        )
