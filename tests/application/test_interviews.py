import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from adapters.fakes.clock import FixedClock
from adapters.fakes.conversations import InMemoryConversationRepository
from adapters.fakes.decisions import InMemoryDecisionRepository
from adapters.fakes.interviews import (
    FakeSlot,
    InMemoryCalendar,
    InMemoryInterviewStore,
    RecordingNotifier,
)
from adapters.fakes.jobs import InMemoryJobQueue
from adapters.fakes.tracing import InMemoryTraceSink
from screening.application.assessment import DecisionBookingPolicy
from screening.application.capability_gateway import CapabilityGateway, PendingAssessment
from screening.application.interviews import InterviewService
from screening.application.ports import NewApplication, TenantRecord, VersionRecord
from screening.domain.profile import ProfileContent
from screening.domain.run_context import RunContext

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 10, 28, 9, tzinfo=UTC)
SLOT_TIME = datetime(2026, 10, 30, 9, tzinfo=UTC)  # 10:00 in Madrid


class StaticVersions:
    def __init__(self, profile: ProfileContent) -> None:
        self.profile = profile

    async def get(self, tenant_id: UUID, version_id: UUID) -> VersionRecord:
        return VersionRecord(
            version_id, tenant_id, 1, "1.0", self.profile, "", "low", "", uuid4(), NOW
        )


@dataclass
class World:
    repo: InMemoryConversationRepository
    decisions: InMemoryDecisionRepository
    queue: InMemoryJobQueue
    calendar: InMemoryCalendar
    store: InMemoryInterviewStore
    notifier: RecordingNotifier
    clock: FixedClock
    gateway: CapabilityGateway
    service: InterviewService
    profile: ProfileContent
    tenant: UUID

    async def candidate(self, external_id: str, timezone: str = "Europe/Madrid") -> RunContext:
        app = await self.repo.upsert_application(
            self.tenant,
            NewApplication(
                external_id, external_id.title(), "", "València", "repartidores", timezone
            ),
        )
        ctx = RunContext(
            "conversador",
            self.tenant,
            "grupo_sazon",
            "tenant",
            "candidate",
            uuid4(),
            app.id,
            uuid4(),
            frozenset(self.profile.grants.conversador),
            self.profile.autonomy.model_dump(),
        )
        assert ctx.session_id is not None and ctx.profile_version_id is not None
        await self.repo.create_session(
            self.tenant, ctx.session_id, app.id, ctx.profile_version_id, "web", ctx, "fixed"
        )
        await self.decisions.create_decision(
            self.tenant, ctx.session_id, "qualify", "applied", 2, "agent", None, "Cumple", NOW
        )
        return ctx

    async def call(self, ctx: RunContext, tool: str, **args: str) -> dict[str, object]:
        return await self.gateway.call(ctx, self.profile, tool, dict(args))

    async def status(self, ctx: RunContext) -> str:
        assert ctx.session_id is not None
        return (await self.repo.get_session(self.tenant, ctx.session_id)).status


@pytest.fixture
def profile() -> ProfileContent:
    return ProfileContent.model_validate_json(
        (Path(__file__).resolve().parents[2] / "seeds/tenants/grupo_sazon/profile.json").read_text()
    )


@pytest.fixture
async def world(profile: ProfileContent) -> World:
    clock = FixedClock(NOW)
    repo, decisions, queue = (
        InMemoryConversationRepository(),
        InMemoryDecisionRepository(),
        InMemoryJobQueue(),
    )
    tenant = uuid4()
    repo.add_tenant(TenantRecord(tenant, "grupo_sazon", "Grupo Sazón"))
    calendar = InMemoryCalendar(
        clock,
        [
            FakeSlot("a", "VLC-RUZ", SLOT_TIME),
            FakeSlot("b", "VLC-RUZ", SLOT_TIME + timedelta(hours=1)),
            FakeSlot("c", "VLC-RUZ", SLOT_TIME + timedelta(days=1)),
            FakeSlot("past", "VLC-RUZ", NOW - timedelta(hours=1)),
        ],
    )
    store, notifier = InMemoryInterviewStore(clock), RecordingNotifier()
    sink = InMemoryTraceSink()
    gateway = CapabilityGateway(
        repo,
        calendar,
        sink,
        clock,
        DecisionBookingPolicy(decisions, repo, queue),
        PendingAssessment(),
        timeout_s=0.05,
    )
    service = InterviewService(
        store,
        repo,
        StaticVersions(profile),
        calendar,
        decisions,
        queue,
        clock,
        notifier,
        timeout_s=0.05,
    )
    gateway.interviews = service
    return World(
        repo,
        decisions,
        queue,
        calendar,
        store,
        notifier,
        clock,
        gateway,
        service,
        profile,
        tenant,
    )


def stages(world: World) -> list[str]:
    return [item.payload["stage"] for item in world.decisions.outbox.values()]


async def test_book_returns_confirmation_schedules_reminder_and_completes(world: World) -> None:
    ctx = await world.candidate("lucia")
    result = await world.call(ctx, "interview_book", slot_id="a")
    assert result["status"] == "booked"
    confirmation = result["confirmation"]
    assert isinstance(confirmation, dict)
    assert confirmation["when"] == "viernes 30/10/2026 a las 10:00"
    assert confirmation["address"] == "Calle Mayor 1"
    assert confirmation["duration_minutes"] == 15
    assert "cancelar" in confirmation["how_to_change"]
    assert result["reminder"] == {"scheduled": True, "at": "jueves 29/10/2026 a las 10:00"}
    booking = next(iter(world.store.rows.values()))
    assert world.store.reminders == [(booking.id, 0, SLOT_TIME - timedelta(hours=24))]
    assert world.calendar.slots["a"].owner == "lucia"
    assert stages(world) == ["Entrevista"]
    assert await world.status(ctx) == "completed"
    assert [e[2] for e in world.repo.events].count("interview_booked") == 1


async def test_ambiguous_timeout_then_retry_books_once(world: World) -> None:
    ctx = await world.candidate("lucia")
    world.calendar.delay_s = 0.2
    first = await world.call(ctx, "interview_book", slot_id="a")
    assert first["status"] == "unavailable"  # timed out, but the calendar did write
    assert world.calendar.slots["a"].owner == "lucia" and not world.store.rows
    world.calendar.delay_s = 0
    second = await world.call(ctx, "interview_book", slot_id="a")
    assert second["status"] == "booked"
    assert len(world.store.rows) == 1
    keys = [c[1]["idempotency_key"] for c in world.calendar.calls if c[0] == "book_slot"]
    assert len(set(keys)) == 1


async def test_double_booking_race_has_one_winner(world: World) -> None:
    first, second = await world.candidate("lucia"), await world.candidate("pablo")
    results = await asyncio.gather(
        world.call(first, "interview_book", slot_id="a"),
        world.call(second, "interview_book", slot_id="a"),
    )
    assert sorted(str(r["status"]) for r in results) == ["booked", "taken"]
    assert len(world.store.rows) == 1


async def test_change_tools_need_own_active_booking_and_active_session(world: World) -> None:
    ctx = await world.candidate("lucia")
    assert await world.call(ctx, "interview_cancel") == {"status": "not_allowed"}
    await world.call(ctx, "interview_book", slot_id="a")
    # Booking completes the conversation: the candidate must reopen it to change anything.
    assert await world.call(ctx, "interview_reschedule", slot_id="b") == {"status": "not_allowed"}
    assert ctx.session_id is not None
    assert await world.service.reopen(world.tenant, ctx.session_id) == "active"
    other = await world.candidate("pablo")
    # Another candidate has no booking: they cannot act on Lucía's interview.
    assert await world.call(other, "interview_cancel") == {"status": "not_allowed"}
    assert await world.call(other, "interview_book", slot_id="a") == {"status": "taken"}
    dial = replace(ctx, autonomy={**ctx.autonomy, "schedule": 0})
    assert await world.call(dial, "interview_cancel") == {"status": "not_allowed"}
    ungranted = replace(ctx, granted=frozenset())
    assert await world.call(ungranted, "interview_cancel") == {"status": "not_allowed"}
    assert world.calendar.slots["a"].owner == "lucia"


async def test_reschedule_moves_atomically_and_replaces_reminder(world: World) -> None:
    ctx = await world.candidate("lucia")
    await world.call(ctx, "interview_book", slot_id="a")
    assert ctx.session_id is not None
    await world.service.reopen(world.tenant, ctx.session_id)
    pablo = await world.candidate("pablo")
    await world.call(pablo, "interview_book", slot_id="c")
    await world.service.reopen(world.tenant, ctx.session_id)
    assert await world.call(ctx, "interview_reschedule", slot_id="c") == {"status": "taken"}
    assert await world.call(ctx, "interview_reschedule", slot_id="past") == {"status": "expired"}
    world.calendar.delay_s = 0.2
    assert (await world.call(ctx, "interview_reschedule", slot_id="b"))["status"] == "unavailable"
    world.calendar.delay_s = 0
    result = await world.call(ctx, "interview_reschedule", slot_id="b")
    assert result["status"] == "rescheduled"
    assert world.calendar.slots["a"].owner == "" and world.calendar.slots["b"].owner == "lucia"
    assert ctx.application_id is not None
    booking = await world.store.active_for(world.tenant, ctx.application_id)
    assert booking is not None and booking.sequence == 1 and booking.status == "rescheduled"
    assert (booking.id, 0) in world.store.cancelled_reminders
    assert world.store.reminders[-1] == (booking.id, 1, SLOT_TIME + timedelta(hours=-23))
    assert await world.status(ctx) == "completed"
    ics = await world.service.ics(world.tenant, booking.id)
    assert f"UID:{booking.id}@agenda.orbio" in ics and "SEQUENCE:1" in ics
    assert "DTSTART;TZID=Europe/Madrid:20261030T110000" in ics


async def test_cancel_frees_slot_keeps_conversation_and_is_idempotent(world: World) -> None:
    ctx = await world.candidate("lucia")
    await world.call(ctx, "interview_book", slot_id="a")
    assert ctx.session_id is not None
    await world.service.reopen(world.tenant, ctx.session_id)
    result = await world.call(ctx, "interview_cancel")
    assert result["status"] == "cancelled"
    assert world.calendar.slots["a"].owner == ""
    booking = next(iter(world.store.rows.values()))
    assert booking.status == "cancelled" and booking.cancelled_by == "candidate"
    assert booking.sequence == 1 and (booking.id, 0) in world.store.cancelled_reminders
    assert stages(world) == ["Entrevista", "Cribado superado"]
    assert await world.status(ctx) == "active"
    assert await world.call(ctx, "interview_cancel") == {"status": "not_allowed"}
    ics = await world.service.ics(world.tenant, booking.id)
    assert "METHOD:CANCEL" in ics and "STATUS:CANCELLED" in ics and "SEQUENCE:1" in ics
    # The candidate may book again: a new interview gets a new UID.
    assert (await world.call(ctx, "interview_book", slot_id="b"))["status"] == "booked"
    assert len({row.id for row in world.store.rows.values()}) == 2


async def test_recruiter_change_notifies_candidate_and_replays(world: World) -> None:
    ctx = await world.candidate("lucia", "America/Mexico_City")
    await world.call(ctx, "interview_book", slot_id="a")
    booking = next(iter(world.store.rows.values()))
    assert [
        s["slot_id"] for s in await world.service.recruiter_slots(world.tenant, booking.id)
    ] == [
        "b",
        "c",
    ]
    result = await world.service.recruiter_change(world.tenant, booking.id, "reschedule", "b")
    assert result["status"] == "rescheduled"
    text = next(iter(world.notifier.posted.values()))[1]
    assert "ha cambiado tu entrevista" in text and "hora del centro de México" in text
    assert "a las 04:00" in text  # 11:00 Madrid is 04:00 in Mexico City
    cancelled = await world.service.recruiter_change(world.tenant, booking.id, "cancel")
    assert cancelled["status"] == "cancelled"
    assert (await world.store.get(world.tenant, booking.id)).cancelled_by == "recruiter"
    assert len(world.notifier.posted) == 2


async def test_candidate_notices_use_the_conversation_language(world: World) -> None:
    ctx = await world.candidate("emily")
    assert ctx.session_id is not None
    await world.repo.set_language_choice(world.tenant, ctx.session_id, "en", "")
    await world.call(ctx, "interview_book", slot_id="a")
    booking = next(iter(world.store.rows.values()))
    world.clock.advance(24 * 3600)  # exactly 24 h before the interview, 10:00 in Madrid
    assert await world.service.remind(world.tenant, booking.id, 0) == "sent"
    await world.service.recruiter_change(world.tenant, booking.id, "cancel")
    reminder, cancelled = (text for _, text in world.notifier.posted.values())
    assert reminder.startswith("Reminder from Grupo Sazón: you have a 15-minute interview")
    assert cancelled.startswith("The Grupo Sazón hiring team has cancelled your interview")


async def test_reminder_dispatch_rules(world: World) -> None:
    ctx = await world.candidate("lucia")
    await world.call(ctx, "interview_book", slot_id="a")
    booking = next(iter(world.store.rows.values()))
    world.clock.advance(13 * 3600)  # 22:00 UTC is 23:00 in Madrid: quiet hours
    assert await world.service.remind(world.tenant, booking.id, 0) == "deferred"
    world.clock.advance(11 * 3600)  # exactly 24 h before the interview
    assert await world.service.remind(world.tenant, booking.id, 0) == "sent"
    assert await world.service.remind(world.tenant, booking.id, 0) == "sent"
    assert len(world.notifier.posted) == 1  # same key: posted once
    text = next(iter(world.notifier.posted.values()))[1]
    assert text.startswith("Recordatorio de Grupo Sazón: tienes una entrevista de 15 minutos")
    assert await world.service.remind(world.tenant, booking.id, 7) == "superseded"
    world.clock.advance(23 * 3600 + 50 * 60)  # ten minutes before the interview
    assert await world.service.remind(world.tenant, booking.id, 0) == "expired"
