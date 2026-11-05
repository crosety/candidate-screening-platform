from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from screening.application.assessment import enqueue_write_result, writable_ats
from screening.application.ports import (
    Clock,
    ConversationRepository,
    DecisionRecord,
    DecisionRepository,
    JobQueue,
    ProfileVersions,
)
from screening.domain.errors import NotAllowed, NotFound
from screening.domain.profile import profile_for_offer


@dataclass(frozen=True)
class InboxItem:
    decision_id: UUID
    session_id: UUID
    candidate_name: str
    decision_type: str
    reason: str
    summary: str
    fields: dict[str, Any]
    flags: list[str]
    created_at: datetime


class InboxService:
    def __init__(
        self,
        decisions: DecisionRepository,
        conversations: ConversationRepository,
        versions: ProfileVersions,
        queue: JobQueue,
        clock: Clock,
    ) -> None:
        self.decisions, self.conversations, self.versions = decisions, conversations, versions
        self.queue, self.clock = queue, clock

    async def list_pending(
        self, tenant_id: UUID, allowed_session_ids: frozenset[UUID] | None = None
    ) -> list[InboxItem]:
        items = []
        for decision in await self.decisions.list_proposed(tenant_id):
            if allowed_session_ids is not None and decision.session_id not in allowed_session_ids:
                continue
            session = await self.conversations.get_session(tenant_id, decision.session_id)
            application = await self.conversations.get_application(
                tenant_id, session.application_id
            )
            result = await self.decisions.get_result(tenant_id, session.id)
            if result is None:
                raise NotFound("Resultado no encontrado")
            items.append(
                InboxItem(
                    decision.id,
                    session.id,
                    application.candidate_name,
                    decision.decision_type,
                    decision.reason,
                    result.summary,
                    result.fields,
                    result.flags,
                    decision.created_at,
                )
            )
        return items

    async def act(
        self,
        tenant_id: UUID,
        decision_id: UUID,
        action: str,
        reason: str,
        user_id: UUID,
    ) -> DecisionRecord:
        decision = await self.decisions.get_decision(tenant_id, decision_id)
        if decision.status != "proposed":
            raise NotAllowed("Esta decisión ya no está pendiente.")
        if action not in ("confirm", "qualify", "reject"):
            raise NotAllowed("Acción no válida.")
        same = action == "confirm" or action == decision.decision_type
        if same and decision.decision_type not in ("qualify", "reject"):
            raise NotAllowed("Elige si es apta o no.")
        if not same and not reason.strip():
            raise NotAllowed("Explica el motivo del cambio.")
        session = await self.conversations.get_session(tenant_id, decision.session_id)
        result = await self.decisions.get_result(tenant_id, session.id)
        if result is None:
            raise NotFound("Resultado no encontrado")
        profile = profile_for_offer(
            (await self.versions.get(tenant_id, session.profile_version_id)).content,
            session.run_context.offer_key,
        )
        writable_ats(profile)
        now = self.clock.now()
        if same:
            applied = await self.decisions.set_decision_status(
                tenant_id, decision_id, "applied", user_id, now
            )
            verdict = result.verdict
            event, payload = "decision_confirmed", {"decision_id": str(decision_id)}
        else:
            await self.decisions.set_decision_status(
                tenant_id, decision_id, "overridden", user_id, None
            )
            applied = await self.decisions.create_decision(
                tenant_id, session.id, action, "applied", 0, "user", user_id, reason.strip(), now
            )
            verdict = "qualified" if action == "qualify" else "not_qualified"
            event, payload = (
                "decision_overridden",
                {
                    "from": decision.decision_type,
                    "to": action,
                    "reason": reason.strip(),
                },
            )
        await enqueue_write_result(
            self.decisions,
            self.conversations,
            self.queue,
            tenant_id,
            session.id,
            applied,
            verdict,
            result,
            profile,
        )
        if verdict == "qualified" and session.status == "completed":
            await self.conversations.set_session_status(tenant_id, session.id, "active")
            await self.conversations.add_event(
                tenant_id,
                session.id,
                "candidate_followup_required",
                {"reason": "human_qualification", "owner": "recruiter"},
            )
        await self.conversations.add_event(tenant_id, session.id, event, payload)
        return applied
