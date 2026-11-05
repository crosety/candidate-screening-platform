import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from screening.application.channels import ChannelIdentityRecord, ChannelService
from screening.application.ports import MessageRecord
from screening.domain.channels import ChannelTemplate, template_values
from screening.domain.errors import NotAllowed
from screening.domain.run_context import RunContext


@dataclass(frozen=True)
class FollowupRecord:
    id: UUID
    tenant_id: UUID
    session_id: UUID
    contact: str
    run_at: datetime
    reason: str
    status: str
    simulated: bool
    last_inbound_at: datetime | None
    channel: str = "whatsapp"


class FollowupStore(Protocol):
    async def create(
        self,
        ctx: RunContext,
        identity: ChannelIdentityRecord,
        run_at: datetime,
        reason: str,
        key: str,
        status: str,
        author: str,
    ) -> FollowupRecord: ...
    async def get(self, tenant_id: UUID, followup_id: UUID) -> FollowupRecord: ...
    async def finish(self, tenant_id: UUID, followup_id: UUID, status: str) -> None: ...
    async def approve(self, tenant_id: UUID, followup_id: UUID) -> FollowupRecord: ...
    async def postpone(self, tenant_id: UUID, followup_id: UUID, until: datetime) -> None: ...
    async def due(self, tenant_id: UUID, contact: str, now: datetime) -> list[FollowupRecord]: ...
    async def contact(self, ctx: RunContext) -> ChannelIdentityRecord | None: ...


def allowed_time(at: datetime, timezone: str) -> datetime:
    local = at.astimezone(ZoneInfo(timezone))
    # The agent can request stricter hours from the client's brief, never weaker ones.
    if local.hour >= 22 or local.hour < 8:
        day = local.date() + timedelta(days=1) if local.hour >= 22 else local.date()
        return datetime.combine(day, time(8), tzinfo=local.tzinfo).astimezone(UTC)
    return at.astimezone(UTC)


WebPush = Callable[[UUID, UUID, MessageRecord], Awaitable[None]]


class FollowupService:
    def __init__(
        self, channels: ChannelService, store: FollowupStore, web_push: WebPush | None = None
    ) -> None:
        self.channels, self.store = channels, store
        # Delivers a reminder to an open chat tab; the message is stored either way.
        self.web_push = web_push

    async def schedule(
        self, ctx: RunContext, at_local: str, reason: str, *, author: str = "conversador"
    ) -> dict[str, str]:
        if ctx.session_id is None or ctx.application_id is None:
            raise NotAllowed("El reenganche necesita una candidatura y sesión.")
        level = ctx.autonomy.get("followup", 0)
        if level == 0 or "followup.schedule" not in ctx.granted:
            return {"status": "not_allowed"}
        identity = await self.store.contact(ctx)
        if identity is None:
            return {"status": "not_available_in_channel"}
        if identity.opted_out:
            return {"status": "opted_out"}
        try:
            at = datetime.fromisoformat(at_local)
        except ValueError:
            raise NotAllowed("Indica una fecha ISO 8601 válida con su zona u offset.") from None
        if at.tzinfo is None or at.utcoffset() is None:
            raise NotAllowed("La hora del recordatorio debe incluir su zona u offset.")
        now = self.channels.now(identity)
        if not now < at <= now + timedelta(days=7):
            raise NotAllowed("Programa el recordatorio en el futuro y dentro de siete días.")
        app = await self.channels.conversations.repo.get_application(
            ctx.tenant_id, ctx.application_id
        )
        at = allowed_time(at, app.timezone)
        if at > now + timedelta(days=7):
            raise NotAllowed("El aplazamiento por silencio superaría siete días.")
        if not 1 <= len(reason.strip()) <= 300:
            raise NotAllowed("Indica un motivo de entre 1 y 300 caracteres.")
        key = hashlib.sha256(f"{ctx.session_id}:{at.isoformat()}:{reason}".encode()).hexdigest()
        row = await self.store.create(
            ctx, identity, at, reason, key, "proposed" if level == 1 else "scheduled", author
        )
        return {"status": row.status, "followup_id": str(row.id), "at": row.run_at.isoformat()}

    async def dispatch(self, tenant_id: UUID, followup_id: UUID) -> None:
        row = await self.store.get(tenant_id, followup_id)
        if row.status != "scheduled":
            return
        conversations = self.channels.conversations
        async with conversations.turn_lock.hold(tenant_id, row.session_id):
            row = await self.store.get(tenant_id, followup_id)
            if row.status != "scheduled":
                return
            if row.channel == "web":
                await self._dispatch_web(row)
                return
            identity = await self.channels.store.identity(tenant_id, row.contact, row.simulated)
            now = self.channels.now(identity)
            if now < row.run_at:
                return
            if now - row.run_at > timedelta(days=1):
                await self.store.finish(tenant_id, row.id, "expired")
                return
            session = await conversations.repo.get_session(tenant_id, row.session_id)
            if (
                session.status != "active"
                or identity.opted_out
                or identity.last_inbound_at != row.last_inbound_at
            ):
                await self.store.finish(tenant_id, row.id, "cancelled")
                return
            app = await conversations.repo.get_application(tenant_id, session.application_id)
            permitted = allowed_time(now, app.timezone)
            if permitted > now:
                await self.store.postpone(tenant_id, row.id, permitted)
                return
            version = await conversations.versions.get(tenant_id, session.profile_version_id)
            template = None
            if row.simulated:
                template = ChannelTemplate("simulated_followup", "es", "followup", approved=True)
            elif version.content.channel_settings.whatsapp:
                item = next(
                    (
                        t
                        for t in version.content.channel_settings.whatsapp.templates
                        if t.purpose == "followup"
                    ),
                    None,
                )
                if item:
                    tenant = await conversations.repo.get_tenant(tenant_id)
                    # The real adapter verifies approval with Meta immediately before send.
                    template = ChannelTemplate(
                        item.name,
                        item.language,
                        "followup",
                        template_values(
                            item.variables,
                            name=app.candidate_name,
                            role=app.job_ref,
                            company=tenant.name,
                        ),
                        approved=True,
                    )
            try:
                text = await conversations.compose_followup(session, row.reason)
                result = await self.channels.proactive(
                    tenant_id,
                    row.contact,
                    text,
                    template,
                    simulated=row.simulated,
                )
            except Exception:
                await self.store.finish(tenant_id, row.id, "failed")
                raise
            await self.store.finish(
                tenant_id, row.id, "sent" if result.delivery is not None else "blocked"
            )

    async def _dispatch_web(self, row: FollowupRecord) -> None:
        """Reminder inside the candidate's web conversation; called under the session lock."""
        conversations = self.channels.conversations
        now = conversations.clock.now()
        if now < row.run_at:
            return
        if now - row.run_at > timedelta(days=1):
            await self.store.finish(row.tenant_id, row.id, "expired")
            return
        session = await conversations.repo.get_session(row.tenant_id, row.session_id)
        inbound = [
            m.created_at
            for m in await conversations.repo.list_messages(row.tenant_id, row.session_id)
            if m.direction == "in"
        ]
        if session.status != "active" or (max(inbound) if inbound else None) != row.last_inbound_at:
            await self.store.finish(row.tenant_id, row.id, "cancelled")
            return
        app = await conversations.repo.get_application(row.tenant_id, session.application_id)
        permitted = allowed_time(now, app.timezone)
        if permitted > now:
            await self.store.postpone(row.tenant_id, row.id, permitted)
            return
        try:
            message = await conversations.remind(session, row.reason, "web")
        except Exception:
            await self.store.finish(row.tenant_id, row.id, "failed")
            raise
        await self.store.finish(row.tenant_id, row.id, "sent")
        if self.web_push is not None:
            await self.web_push(row.tenant_id, row.session_id, message)

    async def dispatch_due(self, tenant_id: UUID, contact: str) -> None:
        identity = await self.channels.store.identity(tenant_id, contact, True)
        for row in await self.store.due(tenant_id, contact, self.channels.now(identity)):
            await self.dispatch(tenant_id, row.id)
