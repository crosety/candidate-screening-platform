"""Interview agenda: one platform record per interview across book, reschedule and cancel.

The calendar connector owns the slots; this service owns the interview as the candidate
and the team see it (iCalendar UID and SEQUENCE, reminder, ATS stage and audit events).
Every calendar write carries an idempotency key derived from the session and the booking,
so a retry after an ambiguous timeout replays the first result instead of acting twice.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol
from uuid import UUID

from screening.application.assessment import writable_ats
from screening.application.ports import (
    Clock,
    ConnectorRegistry,
    ConversationRepository,
    DecisionRepository,
    JobQueue,
    ProfileVersions,
    SessionRecord,
    TenantRecord,
)
from screening.domain.errors import Conflict, NotAllowed, NotFound
from screening.domain.interviews import (
    ACTIVE_STATUSES,
    HOW_TO_CHANGE,
    IcsEvent,
    SlotBooking,
    build_ics,
    confirmation,
    in_quiet_hours,
    local_text,
    operation_key,
    recruiter_change_text,
    reminder_text,
    reminder_time,
    zone_label,
)
from screening.domain.profile import Connection, ProfileContent, profile_for_offer
from screening.domain.run_context import RunContext

CHANGE_TOOLS = ("interview_reschedule", "interview_cancel")


@dataclass(frozen=True)
class BookingRecord:
    id: UUID
    tenant_id: UUID
    session_id: UUID
    application_id: UUID
    connection_id: str
    slot: SlotBooking
    candidate_timezone: str
    status: str
    sequence: int
    cancelled_by: str
    cancelled_at: datetime | None
    reminder_at: datetime | None
    reminder_status: str
    created_at: datetime
    updated_at: datetime

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES


class InterviewStore(Protocol):
    async def active_for(self, tenant_id: UUID, application_id: UUID) -> BookingRecord | None: ...
    async def latest_for(self, tenant_id: UUID, application_id: UUID) -> BookingRecord | None: ...
    async def get(self, tenant_id: UUID, booking_id: UUID) -> BookingRecord: ...  # NotFound
    async def create(
        self,
        tenant_id: UUID,
        session_id: UUID,
        application_id: UUID,
        connection_id: str,
        slot: SlotBooking,
        candidate_timezone: str,
        reminder_at: datetime | None,
    ) -> BookingRecord: ...  # same active slot: returns it; another active slot: Conflict
    async def move(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        expected_sequence: int,
        slot: SlotBooking,
        reminder_at: datetime | None,
    ) -> BookingRecord: ...  # sequence already advanced: returns the current row unchanged
    async def cancel(
        self, tenant_id: UUID, booking_id: UUID, expected_sequence: int, by: str, at: datetime
    ) -> BookingRecord: ...
    async def finish_reminder(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        sequence: int,
        status: str,
        retry_at: datetime | None = None,
    ) -> None: ...


class InterviewNotifier(Protocol):
    """Posts a platform message into the candidate's own conversation (idempotent by key)."""

    async def notify(self, session: SessionRecord, text: str, key: str) -> bool: ...


class InterviewService:
    def __init__(
        self,
        store: InterviewStore,
        conversations: ConversationRepository,
        versions: ProfileVersions,
        registry: ConnectorRegistry,
        decisions: DecisionRepository,
        queue: JobQueue,
        clock: Clock,
        notifier: InterviewNotifier | None = None,
        *,
        timeout_s: float = 10.0,
        organizer_email: str = "no-reply@orbio.invalid",
    ) -> None:
        self.store, self.conversations, self.versions = store, conversations, versions
        self.registry, self.decisions, self.queue = registry, decisions, queue
        self.clock, self.notifier = clock, notifier
        self.timeout_s, self.organizer_email = timeout_s, organizer_email

    # --- Candidate tools (called by CapabilityGateway after its guards) -----------------------

    async def can_change(self, ctx: RunContext) -> bool:
        if ctx.session_id is None or ctx.application_id is None:
            return False
        session = await self.conversations.get_session(ctx.tenant_id, ctx.session_id)
        if session.status != "active":
            return False
        return await self.store.active_for(ctx.tenant_id, ctx.application_id) is not None

    async def handle(
        self,
        ctx: RunContext,
        profile: ProfileContent,
        connection: Connection,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        if ctx.session_id is None or ctx.application_id is None:
            raise NotAllowed("La agenda necesita una sesión y una candidatura.")
        if tool_name == "interview_book":
            return await self._book(ctx, profile, connection, str(args["slot_id"]))
        booking = await self.store.active_for(ctx.tenant_id, ctx.application_id)
        if booking is None:
            return {"status": "no_booking"}
        if tool_name == "interview_reschedule":
            return await self._reschedule(
                ctx.tenant_id,
                ctx.tenant_slug,
                booking,
                profile,
                connection,
                str(args["slot_id"]),
                "candidate",
            )
        return await self._cancel(
            ctx.tenant_id, ctx.tenant_slug, booking, profile, connection, "candidate"
        )

    async def _book(
        self, ctx: RunContext, profile: ProfileContent, connection: Connection, slot_id: str
    ) -> dict[str, Any]:
        assert ctx.session_id is not None and ctx.application_id is not None
        app = await self.conversations.get_application(ctx.tenant_id, ctx.application_id)
        result = await self._calendar(
            ctx.tenant_slug,
            connection,
            "book_slot",
            {
                "slot_id": slot_id,
                "application_external_id": app.external_id,
                # No booking exists yet: the session and the chosen slot identify the write.
                "idempotency_key": operation_key(ctx.session_id, "new", "book", slot_id),
            },
        )
        if result.get("status") != "booked":
            current = await self.store.active_for(ctx.tenant_id, ctx.application_id)
            if result.get("status") == "already_booked" and current is not None:
                return {
                    "status": "already_booked",
                    "booking_id": str(current.id),
                    "confirmation": confirmation(current.slot, current.candidate_timezone),
                }
            return {"status": result.get("status", "unavailable")}
        slot = SlotBooking.from_calendar(result)
        booking = await self.store.create(
            ctx.tenant_id,
            ctx.session_id,
            ctx.application_id,
            connection.id,
            slot,
            app.timezone,
            reminder_time(slot.starts_at, app.timezone, self.clock.now()),
        )
        await self._after_change(booking, profile, "booked", "candidate")
        await self.conversations.set_session_status(ctx.tenant_id, ctx.session_id, "completed")
        return self._result("booked", booking, legacy=result)

    async def _reschedule(
        self,
        tenant_id: UUID,
        tenant_slug: str,
        booking: BookingRecord,
        profile: ProfileContent,
        connection: Connection,
        new_slot_id: str,
        by: str,
    ) -> dict[str, Any]:
        app = await self.conversations.get_application(tenant_id, booking.application_id)
        result = await self._calendar(
            tenant_slug,
            connection,
            "reschedule_booking",
            {
                "slot_id": booking.slot.slot_ref,
                "new_slot_id": new_slot_id,
                "application_external_id": app.external_id,
                "idempotency_key": operation_key(
                    booking.session_id, booking.id, "reschedule", booking.sequence, new_slot_id
                ),
            },
        )
        if result.get("status") != "rescheduled":
            return {"status": result.get("status", "unavailable")}
        slot = SlotBooking.from_calendar(result)
        moved = await self.store.move(
            tenant_id,
            booking.id,
            booking.sequence,
            slot,
            reminder_time(slot.starts_at, booking.candidate_timezone, self.clock.now()),
        )
        await self._after_change(moved, profile, "rescheduled", by)
        if by == "candidate":
            await self.conversations.set_session_status(tenant_id, booking.session_id, "completed")
        return self._result("rescheduled", moved, legacy=result)

    async def _cancel(
        self,
        tenant_id: UUID,
        tenant_slug: str,
        booking: BookingRecord,
        profile: ProfileContent,
        connection: Connection,
        by: str,
    ) -> dict[str, Any]:
        app = await self.conversations.get_application(tenant_id, booking.application_id)
        result = await self._calendar(
            tenant_slug,
            connection,
            "cancel_booking",
            {
                "slot_id": booking.slot.slot_ref,
                "application_external_id": app.external_id,
                "idempotency_key": operation_key(booking.session_id, booking.id, "cancel"),
            },
        )
        if result.get("status") != "cancelled":
            return {"status": result.get("status", "unavailable")}
        cancelled = await self.store.cancel(
            tenant_id, booking.id, booking.sequence, by, self.clock.now()
        )
        await self._after_change(cancelled, profile, "cancelled", by)
        return {
            "status": "cancelled",
            "booking_id": str(cancelled.id),
            "cancelled": {
                "when": local_text(cancelled.slot.starts_at, cancelled.candidate_timezone),
                "timezone_label": zone_label(cancelled.candidate_timezone),
                "site_name": cancelled.slot.site_name,
            },
            "must_confirm": "Confirma que esa entrevista queda cancelada. Si quiere otra fecha, "
            "ofrece huecos con interview_slots; no reserves sin que elija uno.",
        }

    async def _calendar(
        self, tenant_slug: str, connection: Connection, operation: str, args: dict[str, Any]
    ) -> dict[str, Any]:
        # A timeout here is ambiguous; the caller retries with the same key and gets a replay.
        return await asyncio.wait_for(
            self.registry.get(tenant_slug, connection).call(operation, args),
            timeout=self.timeout_s,
        )

    def _result(
        self, status: str, booking: BookingRecord, *, legacy: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "status": status,
            "booking_id": str(booking.id),
            # Kept for compatibility: the site's local time as the calendar shows it.
            "starts_at_local": legacy.get("starts_at_local", ""),
            "site_name": booking.slot.site_name,
            "confirmation": confirmation(
                booking.slot, booking.candidate_timezone, rescheduled=status == "rescheduled"
            ),
            "reminder": (
                {
                    "scheduled": True,
                    "at": local_text(booking.reminder_at, booking.candidate_timezone),
                }
                if booking.reminder_at
                else {"scheduled": False}
            ),
        }

    async def _after_change(
        self, booking: BookingRecord, profile: ProfileContent, change: str, by: str
    ) -> None:
        when = local_text(booking.slot.starts_at, booking.slot.site_timezone)
        await self.conversations.add_event(
            booking.tenant_id,
            booking.session_id,
            f"interview_{change}",
            {
                "booking_id": str(booking.id),
                "sequence": booking.sequence,
                "by": by,
                "site_name": booking.slot.site_name,
                "starts_at": booking.slot.starts_at.isoformat(),
                "reminder_at": booking.reminder_at.isoformat() if booking.reminder_at else None,
            },
        )
        try:
            ats = writable_ats(profile)
        except NotAllowed:
            await self.conversations.add_event(
                booking.tenant_id, booking.session_id, "interview_ats_not_writable", {}
            )
            return
        app = await self.conversations.get_application(booking.tenant_id, booking.application_id)
        stage, note = (
            (ats.settings.get("stage_qualified", ""), f"Entrevista cancelada ({by}): {when}")
            if change == "cancelled"
            else (
                ats.settings.get("stage_interview", ""),
                f"Entrevista{' cambiada' if change == 'rescheduled' else ''}: "
                f"{when} en {booking.slot.site_name}",
            )
        )
        item = await self.decisions.create_outbox(
            booking.tenant_id,
            booking.session_id,
            "ats.set_stage",
            {"application_external_id": app.external_id, "stage": stage, "note": note},
            f"set_stage:{booking.session_id}:interview:{booking.id}:{change}:{booking.sequence}",
        )
        await self.queue.enqueue(
            "outbox_dispatch",
            {"outbox_item_id": str(item.id)},
            booking.tenant_id,
            f"outbox:{item.id}",
        )

    # --- Recruiter actions ----------------------------------------------------------------

    async def recruiter_change(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        action: Literal["cancel", "reschedule"],
        new_slot_id: str = "",
    ) -> dict[str, Any]:
        booking = await self.store.get(tenant_id, booking_id)
        if not booking.active:
            raise Conflict("La entrevista ya está cancelada.")
        tenant, profile, connection = await self._setup(booking)
        if action == "cancel":
            result = await self._cancel(
                tenant_id, tenant.slug, booking, profile, connection, "recruiter"
            )
        else:
            result = await self._reschedule(
                tenant_id, tenant.slug, booking, profile, connection, new_slot_id, "recruiter"
            )
        if result["status"] not in ("cancelled", "rescheduled"):
            return result
        current = await self.store.get(tenant_id, booking_id)
        await self._notify(
            current,
            lambda language: recruiter_change_text(
                action, current.slot, current.candidate_timezone, tenant.name, language
            ),
            f"agenda:c:{current.id}:{current.sequence}",
        )
        return result

    async def recruiter_slots(self, tenant_id: UUID, booking_id: UUID) -> list[dict[str, Any]]:
        booking = await self.store.get(tenant_id, booking_id)
        if not booking.active or not booking.slot.site_ref:
            return []
        tenant, _, connection = await self._setup(booking)
        result = await self._calendar(
            tenant.slug, connection, "list_slots", {"site_id": booking.slot.site_ref, "limit": 10}
        )
        return list(result.get("slots", []))

    async def _setup(
        self, booking: BookingRecord
    ) -> tuple[TenantRecord, ProfileContent, Connection]:
        tenant = await self.conversations.get_tenant(booking.tenant_id)
        session = await self.conversations.get_session(booking.tenant_id, booking.session_id)
        version = await self.versions.get(booking.tenant_id, session.profile_version_id)
        profile = profile_for_offer(version.content, session.run_context.offer_key)
        connection = next(
            (
                c
                for c in profile.connections
                if c.id == booking.connection_id
                and c.kind == "calendar"
                and c.permission == "write"
                and "interview.book" in c.backs
            ),
            None,
        )
        if connection is None:
            raise NotAllowed("La agenda de esta entrevista no está conectada con escritura.")
        return tenant, profile, connection

    async def _notify(self, booking: BookingRecord, text: Callable[[str], str], key: str) -> bool:
        """Post a notice written in the language the candidate is using in the conversation."""
        if self.notifier is None:
            return False
        session = await self.conversations.get_session(booking.tenant_id, booking.session_id)
        return await self.notifier.notify(session, text(session.response_language), key)

    # --- Candidate self-service in the chat --------------------------------------------------

    async def reopen(self, tenant_id: UUID, session_id: UUID) -> str:
        """Let the candidate talk to the agent again to change a booked interview."""
        session = await self.conversations.get_session(tenant_id, session_id)
        booking = await self.store.active_for(tenant_id, session.application_id)
        if booking is None:
            raise NotAllowed("No hay una entrevista que cambiar.")
        if session.status == "completed":
            await self.conversations.set_session_status(tenant_id, session_id, "active")
            await self.conversations.add_event(
                tenant_id, session_id, "interview_change_requested", {"booking_id": str(booking.id)}
            )
            return "active"
        return session.status

    async def candidate_view(self, tenant_id: UUID, application_id: UUID) -> dict[str, Any] | None:
        booking = await self.store.latest_for(tenant_id, application_id)
        if booking is None:
            return None
        return {
            "id": str(booking.id),
            "status": booking.status,
            "when": local_text(booking.slot.starts_at, booking.candidate_timezone),
            "timezone": booking.candidate_timezone,
            "timezone_label": zone_label(booking.candidate_timezone),
            "starts_at": booking.slot.starts_at.isoformat(),
            "site_name": booking.slot.site_name,
            "address": booking.slot.site_address or None,
            "duration_minutes": booking.slot.duration_minutes,
            "can_change": booking.active and booking.slot.starts_at > self.clock.now(),
        }

    async def ics(self, tenant_id: UUID, booking_id: UUID) -> str:
        booking = await self.store.get(tenant_id, booking_id)
        tenant = await self.conversations.get_tenant(tenant_id)
        cancelled = not booking.active
        summary = f"Entrevista con {tenant.name} · {booking.slot.site_name}"
        description = (
            "Entrevista cancelada."
            if cancelled
            else f"Entrevista de {booking.slot.duration_minutes} minutos con la persona "
            f"encargada de {booking.slot.site_name}. {HOW_TO_CHANGE}"
        )
        return build_ics(
            IcsEvent(
                uid=f"{booking.id}@agenda.orbio",
                sequence=booking.sequence,
                method="CANCEL" if cancelled else "PUBLISH",
                slot=booking.slot,
                summary=("Cancelada: " if cancelled else "") + summary,
                description=description,
                organizer_name=tenant.name,
                organizer_email=self.organizer_email,
                stamp=booking.updated_at,
            )
        )

    # --- Deterministic reminder ---------------------------------------------------------------

    async def remind(self, tenant_id: UUID, booking_id: UUID, sequence: int) -> str:
        try:
            booking = await self.store.get(tenant_id, booking_id)
        except NotFound:
            return "missing"
        if not booking.active or booking.sequence != sequence:
            # A cancel or reschedule replaced this reminder; its own job is the valid one.
            return "superseded"
        now = self.clock.now()
        if now >= booking.slot.starts_at - timedelta(minutes=30):
            await self.store.finish_reminder(tenant_id, booking.id, sequence, "expired")
            return "expired"
        if in_quiet_hours(now, booking.candidate_timezone):
            retry = reminder_time(booking.slot.starts_at, booking.candidate_timezone, now)
            if retry is None or retry <= now:
                await self.store.finish_reminder(tenant_id, booking.id, sequence, "skipped")
                return "skipped"
            await self.store.finish_reminder(
                tenant_id, booking.id, sequence, "scheduled", retry_at=retry
            )
            return "deferred"
        tenant = await self.conversations.get_tenant(tenant_id)
        posted = await self._notify(
            booking,
            lambda language: reminder_text(
                booking.slot, booking.candidate_timezone, tenant.name, language
            ),
            f"agenda:r:{booking.id}:{sequence}",
        )
        if posted:
            await self.conversations.add_event(
                tenant_id,
                booking.session_id,
                "interview_reminder_sent",
                {"booking_id": str(booking.id), "sequence": sequence},
            )
        await self.store.finish_reminder(
            tenant_id, booking.id, sequence, "sent" if posted else "unavailable"
        )
        return "sent" if posted else "unavailable"
