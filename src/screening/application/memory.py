import secrets
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from screening.application.interviews import BookingRecord
from screening.application.ports import (
    ApplicationRecord,
    Clock,
    DecisionRecord,
    DraftRecord,
    LinkTarget,
    MessageRecord,
    NewApplication,
    OutboxRecord,
    ResultRecord,
    SerializedMessage,
    SessionRecord,
    TenantRecord,
    VersionRecord,
)
from screening.domain.errors import Conflict, NotFound
from screening.domain.interviews import SlotBooking
from screening.domain.run_context import RunContext
from screening.domain.signals import TurnSignal


class InMemoryConversationRepository:
    def __init__(self) -> None:
        self.tenants: dict[UUID, TenantRecord] = {}
        self.applications: dict[tuple[UUID, UUID], ApplicationRecord] = {}
        self.links: dict[str, LinkTarget] = {}
        self.sessions: dict[tuple[UUID, UUID], SessionRecord] = {}
        self.messages: dict[tuple[UUID, UUID], list[MessageRecord]] = {}
        self.client_ids: set[tuple[UUID, UUID, str]] = set()
        self.provider_ids: set[tuple[UUID, str, str]] = set()
        self.history: dict[tuple[UUID, UUID], dict[int, SerializedMessage]] = {}
        self.evidence: dict[tuple[UUID, UUID], list[dict[str, Any]]] = {}
        self.events: list[tuple[UUID, UUID | None, str, dict[str, Any]]] = []

    def add_tenant(self, tenant: TenantRecord) -> None:
        self.tenants[tenant.id] = tenant

    async def get_tenant(self, tenant_id: UUID) -> TenantRecord:
        try:
            return self.tenants[tenant_id]
        except KeyError:
            raise NotFound("Cliente no encontrado") from None

    async def get_tenant_by_slug(self, slug: str) -> TenantRecord:
        for tenant in self.tenants.values():
            if tenant.slug == slug:
                return tenant
        raise NotFound("Cliente no encontrado")

    async def upsert_application(self, tenant_id: UUID, data: NewApplication) -> ApplicationRecord:
        await self.get_tenant(tenant_id)
        existing = next(
            (
                a
                for a in self.applications.values()
                if a.tenant_id == tenant_id and a.external_id == data.external_id
            ),
            None,
        )
        app = ApplicationRecord(
            id=existing.id if existing else uuid4(), tenant_id=tenant_id, **asdict(data)
        )
        self.applications[(tenant_id, app.id)] = app
        return app

    async def get_application(self, tenant_id: UUID, application_id: UUID) -> ApplicationRecord:
        try:
            return self.applications[(tenant_id, application_id)]
        except KeyError:
            raise NotFound("Candidatura no encontrada") from None

    async def get_or_create_link(self, tenant_id: UUID, application_id: UUID) -> str:
        await self.get_application(tenant_id, application_id)
        target = LinkTarget(tenant_id, application_id)
        for token, item in self.links.items():
            if item == target:
                return token
        token = secrets.token_urlsafe(32)
        self.links[token] = target
        return token

    async def resolve_link(self, token: str) -> LinkTarget:
        try:
            return self.links[token]
        except KeyError:
            raise NotFound("Enlace no válido") from None

    async def latest_session(self, tenant_id: UUID, application_id: UUID) -> SessionRecord | None:
        rows = [
            s
            for s in self.sessions.values()
            if s.tenant_id == tenant_id and s.application_id == application_id
        ]
        return deepcopy(max(rows, key=lambda s: s.created_at)) if rows else None

    async def create_session(
        self,
        tenant_id: UUID,
        session_id: UUID,
        application_id: UUID,
        profile_version_id: UUID,
        channel: str,
        run_context: RunContext,
        system_prompt: str,
    ) -> SessionRecord:
        await self.get_application(tenant_id, application_id)
        if (tenant_id, session_id) in self.sessions:
            raise Conflict("La conversación ya existe.")
        existing = await self.latest_session(tenant_id, application_id)
        if existing is not None:
            return existing
        row = SessionRecord(
            session_id,
            tenant_id,
            application_id,
            profile_version_id,
            channel,
            "active",
            deepcopy(run_context),
            system_prompt,
            datetime.now(UTC),
        )
        self.sessions[(tenant_id, session_id)] = row
        return deepcopy(row)

    async def get_session(self, tenant_id: UUID, session_id: UUID) -> SessionRecord:
        try:
            return deepcopy(self.sessions[(tenant_id, session_id)])
        except KeyError:
            raise NotFound("Conversación no encontrada") from None

    async def set_session_status(self, tenant_id: UUID, session_id: UUID, status: str) -> None:
        row = await self.get_session(tenant_id, session_id)
        self.sessions[(tenant_id, session_id)] = replace(row, status=status)

    async def add_message(
        self,
        tenant_id: UUID,
        session_id: UUID,
        direction: str,
        text: str,
        client_message_id: str = "",
        *,
        channel: str = "web",
        provider_message_id: str = "",
        simulated: bool = False,
    ) -> MessageRecord | None:
        await self.get_session(tenant_id, session_id)
        key = (tenant_id, session_id, client_message_id)
        if client_message_id and key in self.client_ids:
            return None
        provider_key = (tenant_id, channel, provider_message_id)
        if provider_message_id and provider_key in self.provider_ids:
            return None
        if client_message_id:
            self.client_ids.add(key)
        if provider_message_id:
            self.provider_ids.add(provider_key)
        row = MessageRecord(
            uuid4(),
            direction,
            text,
            datetime.now(UTC),
            channel,
            provider_message_id,
            "",
            simulated,
            client_message_id,
        )
        self.messages.setdefault((tenant_id, session_id), []).append(row)
        return row

    async def list_messages(self, tenant_id: UUID, session_id: UUID) -> list[MessageRecord]:
        await self.get_session(tenant_id, session_id)
        return list(self.messages.get((tenant_id, session_id), []))

    async def record_signal(
        self, tenant_id: UUID, session_id: UUID, message_id: UUID, signal: TurnSignal
    ) -> None:
        session = await self.get_session(tenant_id, session_id)
        rows = self.messages.get((tenant_id, session_id), [])
        self.messages[(tenant_id, session_id)] = [
            replace(m, language=signal.language, signal=signal.stored())
            if m.id == message_id
            else m
            for m in rows
        ]
        if signal.language:
            self.sessions[(tenant_id, session_id)] = replace(session, language=signal.language)

    async def set_language_choice(
        self, tenant_id: UUID, session_id: UUID, preferred: str, fallback: str
    ) -> None:
        session = await self.get_session(tenant_id, session_id)
        self.sessions[(tenant_id, session_id)] = replace(
            session, preferred_language=preferred, fallback_language=fallback
        )

    async def set_abandoned_stage(self, tenant_id: UUID, session_id: UUID, stage: str) -> None:
        session = await self.get_session(tenant_id, session_id)
        self.sessions[(tenant_id, session_id)] = replace(session, abandoned_stage=stage)

    async def count_incoming(self, tenant_id: UUID, session_id: UUID) -> int:
        return sum(m.direction == "in" for m in await self.list_messages(tenant_id, session_id))

    async def tool_evidence(self, tenant_id: UUID, session_id: UUID) -> list[dict[str, Any]]:
        await self.get_session(tenant_id, session_id)
        return deepcopy(self.evidence.get((tenant_id, session_id), []))

    async def load_history(self, tenant_id: UUID, session_id: UUID) -> list[SerializedMessage]:
        await self.get_session(tenant_id, session_id)
        rows = self.history.get((tenant_id, session_id), {})
        return deepcopy([rows[index] for index in sorted(rows)])

    async def append_history(
        self, tenant_id: UUID, session_id: UUID, start_seq: int, messages: list[SerializedMessage]
    ) -> None:
        await self.get_session(tenant_id, session_id)
        rows = self.history.setdefault((tenant_id, session_id), {})
        if any(start_seq + index in rows for index in range(len(messages))):
            raise Conflict("El historial ha cambiado.")
        rows.update({start_seq + index: deepcopy(item) for index, item in enumerate(messages)})

    async def add_event(
        self, tenant_id: UUID, session_id: UUID | None, type: str, payload: dict[str, Any]
    ) -> None:
        if session_id is not None:
            await self.get_session(tenant_id, session_id)
        self.events.append((tenant_id, session_id, type, deepcopy(payload)))


class InMemoryDecisionRepository:
    def __init__(self) -> None:
        self.results: dict[tuple[UUID, UUID], ResultRecord] = {}
        self.decisions: dict[tuple[UUID, UUID], DecisionRecord] = {}
        self.outbox: dict[tuple[UUID, UUID], OutboxRecord] = {}
        self.outbox_keys: dict[tuple[UUID, str], UUID] = {}
        self.errors: dict[UUID, str] = {}

    async def get_result(self, tenant_id: UUID, session_id: UUID) -> ResultRecord | None:
        return deepcopy(self.results.get((tenant_id, session_id)))

    async def save_result(self, tenant_id: UUID, result: ResultRecord) -> None:
        key = (tenant_id, result.session_id)
        if key in self.results:
            raise Conflict("La conversación ya tiene un resultado.")
        self.results[key] = deepcopy(result)

    async def create_decision(
        self,
        tenant_id: UUID,
        session_id: UUID,
        decision_type: str,
        status: str,
        autonomy_level: int,
        decided_by: str,
        user_id: UUID | None,
        reason: str,
        applied_at: datetime | None,
    ) -> DecisionRecord:
        row = DecisionRecord(
            uuid4(),
            tenant_id,
            session_id,
            decision_type,
            status,
            autonomy_level,
            decided_by,
            user_id,
            reason,
            datetime.now(UTC),
            applied_at,
        )
        self.decisions[(tenant_id, row.id)] = row
        return row

    async def get_decision(self, tenant_id: UUID, decision_id: UUID) -> DecisionRecord:
        try:
            return self.decisions[(tenant_id, decision_id)]
        except KeyError:
            raise NotFound("Decisión no encontrada") from None

    async def set_decision_status(
        self,
        tenant_id: UUID,
        decision_id: UUID,
        status: str,
        user_id: UUID | None,
        applied_at: datetime | None,
    ) -> DecisionRecord:
        row = await self.get_decision(tenant_id, decision_id)
        if row.status != "proposed":
            raise Conflict("Esta decisión ya no está pendiente.")
        row = replace(
            row,
            status=status,
            user_id=user_id,
            applied_at=applied_at,
            decided_by="user" if user_id else row.decided_by,
        )
        self.decisions[(tenant_id, row.id)] = row
        return row

    async def list_proposed(self, tenant_id: UUID) -> list[DecisionRecord]:
        return sorted(
            [
                row
                for (tenant, _), row in self.decisions.items()
                if tenant == tenant_id and row.status == "proposed"
            ],
            key=lambda row: (row.created_at, row.id),
        )

    async def has_applied_qualify(self, tenant_id: UUID, session_id: UUID) -> bool:
        return any(
            row.session_id == session_id
            and row.decision_type == "qualify"
            and row.status == "applied"
            for (tenant, _), row in self.decisions.items()
            if tenant == tenant_id
        )

    async def create_outbox(
        self,
        tenant_id: UUID,
        session_id: UUID,
        kind: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> OutboxRecord:
        key = tenant_id, idempotency_key
        if key in self.outbox_keys:
            return deepcopy(self.outbox[(tenant_id, self.outbox_keys[key])])
        row = OutboxRecord(uuid4(), tenant_id, session_id, kind, deepcopy(payload), "pending", 0)
        self.outbox_keys[key] = row.id
        self.outbox[(tenant_id, row.id)] = row
        return deepcopy(row)

    async def get_outbox(self, tenant_id: UUID, item_id: UUID) -> OutboxRecord:
        try:
            return deepcopy(self.outbox[(tenant_id, item_id)])
        except KeyError:
            raise NotFound("Efecto no encontrado") from None

    async def mark_outbox(
        self, tenant_id: UUID, item_id: UUID, status: str, error: str = ""
    ) -> None:
        row = await self.get_outbox(tenant_id, item_id)
        self.outbox[(tenant_id, item_id)] = replace(row, status=status, attempts=row.attempts + 1)
        self.errors[item_id] = error


class InMemoryJobQueue:
    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        tenant_id: UUID | None,
        idempotency_key: str | None = None,
        max_attempts: int = 5,
    ) -> None:
        if idempotency_key and any(job["idempotency_key"] == idempotency_key for job in self.jobs):
            return
        self.jobs.append(
            {
                "kind": kind,
                "payload": deepcopy(payload),
                "tenant_id": tenant_id,
                "idempotency_key": idempotency_key,
                "max_attempts": max_attempts,
            }
        )


class FixedProfileVersions:
    def __init__(self, version: VersionRecord, draft: DraftRecord) -> None:
        self.version, self.current_draft = version, draft

    async def for_new_session(
        self, tenant_id: UUID, session_id: UUID, segment: str = ""
    ) -> VersionRecord:
        return self.version

    async def get(self, tenant_id: UUID, version_id: UUID) -> VersionRecord:
        return self.version

    async def active(self, tenant_id: UUID) -> VersionRecord | None:
        return self.version

    async def draft(self, tenant_id: UUID) -> DraftRecord:
        return self.current_draft


@dataclass
class InMemoryInterviewStore:
    clock: Clock
    rows: dict[UUID, BookingRecord] = field(default_factory=dict)
    reminders: list[tuple[UUID, int, datetime]] = field(default_factory=list)
    cancelled_reminders: list[tuple[UUID, int]] = field(default_factory=list)
    finished: list[tuple[UUID, int, str]] = field(default_factory=list)

    async def active_for(self, tenant_id: UUID, application_id: UUID) -> BookingRecord | None:
        return next(
            (
                r
                for r in self.rows.values()
                if r.tenant_id == tenant_id and r.application_id == application_id and r.active
            ),
            None,
        )

    async def latest_for(self, tenant_id: UUID, application_id: UUID) -> BookingRecord | None:
        rows = [
            r
            for r in self.rows.values()
            if r.tenant_id == tenant_id and r.application_id == application_id
        ]
        return max(rows, key=lambda r: r.updated_at, default=None)

    async def get(self, tenant_id: UUID, booking_id: UUID) -> BookingRecord:
        row = self.rows.get(booking_id)
        if row is None or row.tenant_id != tenant_id:
            raise NotFound("Entrevista no encontrada")
        return row

    def _schedule(self, row: BookingRecord) -> None:
        if row.reminder_at is not None:
            self.reminders.append((row.id, row.sequence, row.reminder_at))

    async def create(
        self,
        tenant_id: UUID,
        session_id: UUID,
        application_id: UUID,
        connection_id: str,
        slot: SlotBooking,
        candidate_timezone: str,
        reminder_at: datetime | None,
    ) -> BookingRecord:
        current = await self.active_for(tenant_id, application_id)
        if current is not None:
            if current.slot.slot_ref == slot.slot_ref:
                return current
            raise Conflict("La candidatura ya tiene una entrevista.")
        now = self.clock.now()
        row = BookingRecord(
            uuid4(),
            tenant_id,
            session_id,
            application_id,
            connection_id,
            slot,
            candidate_timezone,
            "booked",
            0,
            "",
            None,
            reminder_at,
            "scheduled" if reminder_at else "none",
            now,
            now,
        )
        self.rows[row.id] = row
        self._schedule(row)
        return row

    async def move(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        expected_sequence: int,
        slot: SlotBooking,
        reminder_at: datetime | None,
    ) -> BookingRecord:
        row = await self.get(tenant_id, booking_id)
        if row.sequence != expected_sequence:
            return row
        self.cancelled_reminders.append((row.id, row.sequence))
        row = replace(
            row,
            slot=slot,
            status="rescheduled",
            sequence=row.sequence + 1,
            reminder_at=reminder_at,
            reminder_status="scheduled" if reminder_at else "none",
            updated_at=self.clock.now() + timedelta(microseconds=row.sequence + 1),
        )
        self.rows[row.id] = row
        self._schedule(row)
        return row

    async def cancel(
        self, tenant_id: UUID, booking_id: UUID, expected_sequence: int, by: str, at: datetime
    ) -> BookingRecord:
        row = await self.get(tenant_id, booking_id)
        if row.sequence != expected_sequence or not row.active:
            return row
        self.cancelled_reminders.append((row.id, row.sequence))
        row = replace(
            row,
            status="cancelled",
            sequence=row.sequence + 1,
            cancelled_by=by,
            cancelled_at=at,
            reminder_at=None,
            reminder_status="cancelled",
            updated_at=at,
        )
        self.rows[row.id] = row
        return row

    async def finish_reminder(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        sequence: int,
        status: str,
        retry_at: datetime | None = None,
    ) -> None:
        row = await self.get(tenant_id, booking_id)
        if row.sequence != sequence:
            return
        self.finished.append((booking_id, sequence, status))
        self.rows[booking_id] = replace(
            row, reminder_status=status, reminder_at=retry_at or row.reminder_at
        )
        if retry_at is not None:
            self.reminders.append((booking_id, sequence, retry_at))
