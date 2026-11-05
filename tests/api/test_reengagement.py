from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.test import Client

from adapters.fakes.clock import FixedClock
from adapters.persistence.reengagement import DjangoSilenceStore
from screening.application.ports import MessageRecord
from screening.application.reengagement import ReengagementService
from screening.domain.profile import ReengagementPolicy
from screening.domain.reengagement import is_quiet, next_allowed, reminder_due
from screening.platform.container import get_container
from tests.api.test_conversations_api import conversation_client as conversation_client
from tests.api.test_conversations_api import open_session
from web.conversations.models import (
    Application,
    ChannelIdentity,
    Event,
    Followup,
    Message,
    Session,
    TwilioOutbound,
)
from web.jobs.models import Job
from web.tenancy.context import tenant_context
from worker.handlers import followup_dispatch

pytestmark = pytest.mark.django_db(transaction=True)
POLICY = ReengagementPolicy(enabled=True)


def test_first_reminder_waits_and_skips_quiet_hours() -> None:
    evening = datetime(2026, 10, 28, 19, 30, tzinfo=UTC)  # 20:30 in Madrid
    due = reminder_due(POLICY, 1, evening, "Europe/Madrid")
    assert due == datetime(2026, 10, 29, 8, 0, tzinfo=UTC)  # 09:00 Madrid, not 22:30
    morning = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)  # 10:00 Madrid
    assert reminder_due(POLICY, 1, morning, "Europe/Madrid") == morning + timedelta(hours=2)


def test_second_reminder_goes_to_next_local_morning_in_mexico() -> None:
    first = datetime(2026, 10, 28, 20, 0, tzinfo=UTC)  # 14:00 in Mexico City
    due = reminder_due(POLICY, 2, first, "America/Mexico_City")
    assert due == datetime(2026, 10, 29, 16, 0, tzinfo=UTC)  # 10:00 next day local


def test_policy_can_only_be_stricter_than_platform_floor() -> None:
    lax = ReengagementPolicy(enabled=True, quiet_from_hour=23, quiet_until_hour=6)
    at = datetime(2026, 10, 28, 21, 30, tzinfo=UTC)  # 22:30 Madrid: platform floor applies
    assert is_quiet(at, "Europe/Madrid", lax)
    assert next_allowed(at, "Europe/Madrid", lax) == datetime(2026, 10, 29, 7, 0, tzinfo=UTC)


def daytime_zone(now: datetime) -> str:
    from zoneinfo import ZoneInfo

    zones = ("Europe/Madrid", "America/Mexico_City", "Asia/Tokyo", "Pacific/Honolulu")
    return next(z for z in zones if 10 <= now.astimezone(ZoneInfo(z)).hour < 19)


def _started(client: Client, tenant: UUID) -> UUID:
    session = UUID(open_session(client))
    async_to_sync(get_container().conversations.start)(tenant, session)
    return session


def _last_out(tenant: UUID, session: UUID) -> datetime:
    with tenant_context(tenant):
        last = (
            Message.objects.filter(session_id=session, direction="out")
            .order_by("-created_at")
            .first()
        )
        assert last is not None
        return last.created_at


def _service(at: datetime) -> ReengagementService:
    return ReengagementService(DjangoSilenceStore(), get_container().conversations, FixedClock(at))


def test_silence_schedules_one_web_reminder_then_dispatches_it(
    conversation_client: tuple[Client, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, tenant = conversation_client
    session = _started(client, tenant)
    due = reminder_due(POLICY, 1, _last_out(tenant, session), "Europe/Madrid")
    assert async_to_sync(_service(due - timedelta(minutes=1)).tick)()["scheduled"] == 0
    assert async_to_sync(_service(due).tick)()["scheduled"] == 1
    assert async_to_sync(_service(due).tick)()["scheduled"] == 0  # one pending at a time
    with tenant_context(tenant):
        row = Followup.objects.get(session_id=session)
        assert (row.channel, row.author, row.status, row.contact) == (
            "web",
            "platform",
            "scheduled",
            "",
        )
        assert Event.objects.filter(session_id=session, type="silence_detected").exists()
        assert Job.objects.filter(kind="followup_dispatch", payload__followup_id=str(row.id))
    pushed: list[MessageRecord] = []

    async def push(tenant_id: UUID, session_id: UUID, message: MessageRecord) -> None:
        pushed.append(message)

    followups = get_container().followups
    monkeypatch.setattr(followups, "web_push", push)
    monkeypatch.setattr(followups.channels.conversations, "clock", FixedClock(due))
    followup_dispatch(
        Job(tenant_id=tenant, kind="followup_dispatch", payload={"followup_id": str(row.id)})
    )
    with tenant_context(tenant):
        row.refresh_from_db()
        assert row.status == "sent"
        reminder = Message.objects.filter(session_id=session, direction="out").order_by(
            "-created_at"
        )[0]
        assert reminder.channel == "web"
        assert Event.objects.filter(session_id=session, type="reengagement_sent").exists()
    assert [m.id for m in pushed] == [reminder.id]


def test_reply_before_dispatch_cancels_the_reminder(
    conversation_client: tuple[Client, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, tenant = conversation_client
    session = _started(client, tenant)
    due = reminder_due(POLICY, 1, _last_out(tenant, session), "Europe/Madrid")
    async_to_sync(_service(due).tick)()
    async_to_sync(get_container().conversations.handle_message)(
        tenant, session, "late-reply", "Perdona, ya estoy aquí"
    )
    with tenant_context(tenant):
        assert Followup.objects.get(session_id=session).status == "cancelled"


def test_exhausted_reminders_abandon_with_the_pending_schema_stage(
    conversation_client: tuple[Client, UUID],
) -> None:
    client, tenant = conversation_client
    session = _started(client, tenant)
    with tenant_context(tenant):
        Message.objects.create(
            tenant_id=tenant,
            session_id=session,
            direction="out",
            text="¿Tienes carnet de conducir en vigor?",
        )
        last = _last_out(tenant, session)
        for attempt in range(3):
            Followup.objects.create(
                tenant_id=tenant,
                session_id=session,
                contact="",
                channel="web",
                run_at=last + timedelta(hours=attempt + 1),
                reason=f"silencio:{attempt + 1}",
                status="sent",
                idempotency_key=f"test-{attempt}",
                author="platform",
            )
            reminder = Message.objects.create(
                tenant_id=tenant, session_id=session, direction="out", text="¿Sigues ahí?"
            )
            # Reminders are written when dispatched, after their scheduled time.
            Message.objects.filter(pk=reminder.pk).update(
                created_at=last + timedelta(hours=attempt + 1, seconds=5)
            )
    later = last + timedelta(hours=3 + 25)
    assert async_to_sync(_service(later - timedelta(hours=2)).tick)()["abandoned"] == 0
    assert async_to_sync(_service(later).tick)()["abandoned"] == 1
    with tenant_context(tenant):
        row = Session.objects.get(pk=session)
        assert row.status == "abandoned"
        assert row.abandoned_stage == "carnet"
        event = Event.objects.get(session_id=session, type="followups_exhausted")
        assert event.payload == {"stage": "carnet", "reason": "silence"}


def test_dial_one_only_proposes_and_disabled_policy_does_nothing(
    conversation_client: tuple[Client, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, tenant = conversation_client
    session = _started(client, tenant)
    with tenant_context(tenant):
        row = Session.objects.get(pk=session)
        context: dict[str, Any] = dict(row.run_context)
        context["autonomy"] = {**context["autonomy"], "followup": 1}
        row.run_context = context
        row.save(update_fields=["run_context"])
    due = reminder_due(POLICY, 1, _last_out(tenant, session), "Europe/Madrid")
    assert async_to_sync(_service(due).tick)()["proposed"] == 1
    with tenant_context(tenant):
        assert Followup.objects.get(session_id=session).status == "proposed"
        assert not Job.objects.filter(kind="followup_dispatch").exists()
        Followup.objects.all().delete()
    versions = get_container().conversations.versions
    original = versions.get

    async def disabled(tenant_id: UUID, version_id: UUID) -> Any:
        # Published versions are append-only; the FDE would publish a new one instead.
        version = await original(tenant_id, version_id)
        content = version.content.model_copy(deep=True)
        content.reengagement.enabled = False
        return replace(version, content=content)

    monkeypatch.setattr(versions, "get", disabled)
    assert async_to_sync(_service(due + timedelta(days=3)).tick)() == {
        "scheduled": 0,
        "proposed": 0,
        "abandoned": 0,
    }


def test_twilio_identity_reminder_goes_through_the_sandbox_queue(
    conversation_client: tuple[Client, UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, tenant = conversation_client
    session = _started(client, tenant)
    now = datetime.now(UTC)
    with tenant_context(tenant):
        application_id = Session.objects.get(pk=session).application_id
        # Quiet hours would rightly postpone the send; run the check in a daytime zone.
        Application.objects.filter(pk=application_id).update(timezone=daytime_zone(now))
        ChannelIdentity.objects.create(
            tenant_id=tenant,
            provider="twilio",
            channel="whatsapp",
            contact="+34000000099",
            application_id=application_id,
            last_inbound_at=now - timedelta(minutes=30),
        )
    item = next(
        s for s in async_to_sync(DjangoSilenceStore().silent_sessions)() if s.session_id == session
    )
    assert item.contact == "+34000000099"
    assert async_to_sync(DjangoSilenceStore().schedule)(item, now, "silencio:1", "scheduled", "k")
    with tenant_context(tenant):
        row = Followup.objects.get(session_id=session)
        assert row.channel == "whatsapp"
    monkeypatch.setattr(
        get_container().followups.channels.conversations,
        "clock",
        FixedClock(now + timedelta(seconds=5)),
    )
    followup_dispatch(
        Job(tenant_id=tenant, kind="followup_dispatch", payload={"followup_id": str(row.id)})
    )
    with tenant_context(tenant):
        row.refresh_from_db()
        outbound = TwilioOutbound.objects.get(identity__contact="+34000000099")
        assert outbound.key.startswith("message:")
        assert outbound.message is not None and outbound.message.session_id == session
        assert Job.objects.filter(kind="twilio_send", payload__outbound_id=str(outbound.id))
    assert row.status == "sent"


def test_assessed_session_reopened_for_interview_is_never_abandoned(
    conversation_client: tuple[Client, UUID],
) -> None:
    from web.decisions.models import Result

    client, tenant = conversation_client
    session = _started(client, tenant)
    with tenant_context(tenant):
        Result.objects.create(
            tenant_id=tenant,
            session_id=session,
            verdict="qualified",
            fields={},
            summary="Apta",
            evaluator_model="test",
            flags=[],
        )
    assert not any(
        s.session_id == session for s in async_to_sync(DjangoSilenceStore().silent_sessions)()
    )
