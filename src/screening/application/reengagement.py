"""Platform-detected silence: the FDE policy decides when, the agent writes each reminder."""

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol
from uuid import UUID

from screening.application.conversation import ConversationService
from screening.application.ports import Clock
from screening.domain.profile import ProfileContent, profile_for_offer
from screening.domain.reengagement import abandon_due, reminder_due
from screening.domain.run_context import RunContext


@dataclass(frozen=True)
class SilentSession:
    tenant_id: UUID
    session_id: UUID
    version_id: UUID
    run_context: RunContext
    timezone: str
    last_out_at: datetime
    last_in_at: datetime | None
    sent: int  # reminders delivered since the candidate's last message
    last_reminder_at: datetime | None
    contact: str  # linked WhatsApp contact, "" for web-only candidates
    simulated: bool


class SilenceStore(Protocol):
    async def silent_sessions(self) -> list[SilentSession]: ...  # active, AI-owned, awaiting
    async def schedule(
        self, item: SilentSession, run_at: datetime, reason: str, status: str, key: str
    ) -> bool: ...  # False when an identical reminder already exists
    async def last_question(self, tenant_id: UUID, session_id: UUID) -> str: ...
    async def abandon(
        self, tenant_id: UUID, session_id: UUID, last_in_at: datetime | None, stage: str
    ) -> bool: ...  # False if the candidate answered or the session closed meanwhile


class ReengagementService:
    def __init__(
        self, store: SilenceStore, conversations: ConversationService, clock: Clock
    ) -> None:
        self.store, self.conversations, self.clock = store, conversations, clock

    async def tick(self) -> dict[str, int]:
        now = self.clock.now()
        counts = {"scheduled": 0, "proposed": 0, "abandoned": 0}
        profiles: dict[UUID, ProfileContent] = {}
        for item in await self.store.silent_sessions():
            if item.version_id not in profiles:
                version = await self.conversations.versions.get(item.tenant_id, item.version_id)
                profiles[item.version_id] = version.content
            profile = profile_for_offer(profiles[item.version_id], item.run_context.offer_key)
            policy = profile.reengagement
            if not policy.enabled:
                continue
            level = int(item.run_context.autonomy.get("followup", 0))
            if item.sent >= policy.max_attempts or level == 0:
                last = item.last_reminder_at or item.last_out_at
                if now >= abandon_due(policy, last) and await self._abandon(item):
                    counts["abandoned"] += 1
                continue
            reference = item.last_reminder_at if item.sent else item.last_out_at
            assert reference is not None
            due = reminder_due(policy, item.sent + 1, reference, item.timezone)
            if now < due:
                continue
            status = "scheduled" if level >= 2 else "proposed"
            key = f"silence:{item.session_id}:{item.sent + 1}:{item.last_in_at}"
            reason = f"recordatorio {item.sent + 1} de {policy.max_attempts} por silencio"
            if await self.store.schedule(item, due, reason, status, key):
                counts[status] += 1
        return counts

    async def stage_for(self, tenant_id: UUID, session_id: UUID) -> str:
        """The schema field (or summary/interview) the agent was waiting for; never a verdict."""
        signals = self.conversations.signals
        if signals is None:
            return "unknown"
        session = await self.conversations.repo.get_session(tenant_id, session_id)
        version = await self.conversations.versions.get(tenant_id, session.profile_version_id)
        profile = profile_for_offer(version.content, session.run_context.offer_key)
        question = await self.store.last_question(tenant_id, session_id)
        try:
            stage = await signals.pending_stage(
                replace(session.run_context, agent="senales"), question, profile.result_schema
            )
        except Exception:
            stage = ""
        return stage or "unknown"

    async def _abandon(self, item: SilentSession) -> bool:
        stage = await self.stage_for(item.tenant_id, item.session_id)
        async with self.conversations.turn_lock.hold(item.tenant_id, item.session_id):
            return await self.store.abandon(item.tenant_id, item.session_id, item.last_in_at, stage)
