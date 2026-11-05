from datetime import timedelta
from typing import Any
from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.test import Client
from django.utils import timezone

from adapters.fakes.clock import FixedClock
from screening.application.ports import NewApplication
from screening.domain.profile import profile_for_offer
from screening.platform.container import get_container
from tests.api.test_phone_api import phone
from web.accounts.models import User
from web.conversations.models import Application, Event, InterviewBooking, Message, Session
from web.decisions.models import Decision
from web.jobs.models import Job
from web.simulated.models import CalendarSlot
from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent, Membership, Tenant
from worker.handlers import HANDLERS

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


def free_slots(tenant: UUID) -> list[str]:
    with tenant_context(tenant):
        return [
            str(s.id)
            for s in CalendarSlot.objects.filter(
                site__external_id="VLC-RUZ",
                booked_application_external_id="",
                starts_at__gt=timezone.now() + timedelta(days=1),
            ).order_by("starts_at")[:3]
        ]


def booked_candidate(tenant: UUID, external_id: str, segment: str = "") -> tuple[UUID, str]:
    c = get_container()
    service = c.conversations
    app, link = async_to_sync(service.ingest)(
        "grupo_sazon",
        NewApplication(external_id, f"Cand {external_id}", "", "València", "repartidores"),
    )
    token = link.rsplit("/", 1)[1]
    opened = async_to_sync(service.open_session)(token)
    with tenant_context(tenant):
        Application.objects.filter(pk=app.id).update(segment=segment)
        Decision.objects.create(
            tenant_id=tenant,
            session_id=opened.session_id,
            decision_type="qualify",
            status="applied",
            autonomy_level=2,
            decided_by="agent",
        )
    session = async_to_sync(service.repo.get_session)(tenant, opened.session_id)
    version = async_to_sync(service.versions.get)(tenant, session.profile_version_id)
    profile = profile_for_offer(version.content, session.run_context.offer_key)
    result = async_to_sync(c.gateway.call)(
        session.run_context, profile, "interview_book", {"slot_id": free_slots(tenant)[0]}
    )
    assert result["status"] == "booked", result
    return opened.session_id, token


def chat(client: Client, path: str, sid: UUID, token: str) -> Any:
    return client.post(
        f"/api/v1/chat/interview{path}",
        {"token": token, "session_id": str(sid)},
        content_type="application/json",
    )


def test_candidate_sees_downloads_and_reopens_own_interview(
    phone: tuple[Client, UUID, UUID],
) -> None:
    _, tenant, _ = phone
    sid, token = booked_candidate(tenant, "agenda-a")
    other, other_token = booked_candidate(tenant, "agenda-b")
    anonymous = Client()
    state = chat(anonymous, "", sid, token)
    assert state.status_code == 200, state.content
    data = state.json()["interview"]
    assert data["status"] == "booked" and data["duration_minutes"] == 15
    assert data["can_change"] is True and data["site_name"] == "Sazón Ruzafa"
    # Another candidate's token never opens this interview.
    assert chat(anonymous, "", sid, other_token).status_code in (400, 404)
    ics = chat(anonymous, "/ics", sid, token)
    assert ics.status_code == 200
    assert ics["Content-Type"].startswith("text/calendar")
    assert ics["Cache-Control"] == "no-store"
    body = ics.content.decode()
    assert f"UID:{data['id']}@agenda.orbio" in body and "METHOD:PUBLISH" in body
    assert token not in body
    with tenant_context(tenant):
        assert Session.objects.get(pk=sid).status == "completed"
    managed = chat(anonymous, "/manage", sid, token)
    assert managed.status_code == 200 and managed.json() == {"status": "active"}
    with tenant_context(tenant):
        assert Session.objects.get(pk=sid).status == "active"
        assert Event.objects.filter(session_id=sid, type="interview_change_requested").exists()
        assert Session.objects.get(pk=other).status == "completed"


def test_recruiter_agenda_scope_actions_audit_and_reminder_jobs(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, _ = phone
    visible, _ = booked_candidate(tenant, "agenda-north", "north")
    hidden, _ = booked_candidate(tenant, "agenda-south", "south")
    user = User.objects.get(email="reclutadora@orbio.local")
    member = Membership.objects.get(user=user, tenant_id=tenant)
    member.allowed_segments = ["north"]
    member.save()
    client.force_login(user)
    base = f"/api/v1/tenants/{tenant}"
    rows = client.get(base + "/interviews").json()
    assert [r["session_id"] for r in rows] == [str(visible)]
    row = rows[0]
    assert row["status"] == "booked" and row["reminder_status"] == "scheduled"
    assert "hora peninsular de España" in row["starts_at_local"]
    with tenant_context(tenant):
        hidden_id = InterviewBooking.objects.get(session_id=hidden).id
        first_job = Job.objects.get(kind="interview_reminder", payload__booking_id=row["id"])
    assert first_job.status == "queued"
    assert client.get(base + f"/interviews/{hidden_id}/slots").status_code == 404
    assert (
        client.post(
            base + f"/interviews/{hidden_id}", {"action": "cancel"}, content_type="application/json"
        ).status_code
        == 404
    )
    assert client.get(base + f"/sessions/{hidden}/interview").json() == {"interview": None}
    detail = client.get(base + f"/sessions/{visible}/interview").json()["interview"]
    assert detail["id"] == row["id"]
    slots = client.get(base + f"/interviews/{row['id']}/slots").json()
    assert slots and all(s["site_name"] == "Sazón Ruzafa" for s in slots)
    moved = client.post(
        base + f"/interviews/{row['id']}",
        # The furthest slot always leaves time for a reminder, whatever the hour of the run.
        {"action": "reschedule", "slot_id": slots[-1]["slot_id"]},
        content_type="application/json",
    )
    assert moved.status_code == 200, moved.content
    assert moved.json()["status"] == "rescheduled" and moved.json()["sequence"] == 1
    first_job.refresh_from_db()
    assert first_job.status == "cancelled"
    ics = client.get(base + f"/interviews/{row['id']}/ics")
    assert ics.status_code == 200 and "SEQUENCE:1" in ics.content.decode()
    with tenant_context(tenant):
        assert AuditEvent.objects.filter(action="interview.reschedule").count() == 1
        assert Message.objects.filter(
            session_id=visible, client_message_id__startswith="agenda:c:"
        ).exists()
        booking = InterviewBooking.objects.get(pk=row["id"])
        job = Job.objects.get(pk=booking.reminder_job_id)
    # The reminder job posts once into the candidate's own conversation.
    assert booking.reminder_at is not None
    get_container().interviews.clock = FixedClock(booking.reminder_at)
    HANDLERS["interview_reminder"](job)
    HANDLERS["interview_reminder"](job)
    with tenant_context(tenant):
        reminders = Message.objects.filter(
            session_id=visible, client_message_id__startswith="agenda:r:"
        )
        assert reminders.count() == 1
        assert reminders.get().text.startswith("Recordatorio de Grupo Sazón")
        assert InterviewBooking.objects.get(pk=row["id"]).reminder_status == "sent"
    cancelled = client.post(
        base + f"/interviews/{row['id']}", {"action": "cancel"}, content_type="application/json"
    )
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert (
        client.post(
            base + f"/interviews/{row['id']}", {"action": "cancel"}, content_type="application/json"
        ).status_code
        == 409
    )
    assert "METHOD:CANCEL" in client.get(base + f"/interviews/{row['id']}/ics").content.decode()
    with tenant_context(tenant):
        slot = CalendarSlot.objects.get(pk=slots[-1]["slot_id"])
        assert slot.booked_application_external_id == ""
        assert AuditEvent.objects.filter(action="interview.cancel").count() == 1


def test_interviews_are_tenant_isolated(phone: tuple[Client, UUID, UUID]) -> None:
    client, tenant, _ = phone
    booked_candidate(tenant, "agenda-rls")
    other = Tenant.objects.get(slug="logistica")
    with tenant_context(other.id):
        assert InterviewBooking.objects.count() == 0
    with tenant_context(tenant):
        assert InterviewBooking.objects.count() == 1
    assert client.get(f"/api/v1/tenants/{other.id}/interviews").json() == []
    outsider = User.objects.get(email="reclutadora@orbio.local")
    client.force_login(outsider)
    assert client.get(f"/api/v1/tenants/{other.id}/interviews").status_code == 404


def test_voice_exposes_change_tools_with_strict_arguments() -> None:
    from types import SimpleNamespace
    from typing import cast

    from screening.application.ports import SessionRecord
    from screening.platform.voice_runtime import tool_specs

    session = cast(
        SessionRecord,
        SimpleNamespace(run_context=SimpleNamespace(granted=frozenset({"interview.book"}))),
    )
    specs = {spec["name"]: spec for spec in tool_specs(session)}
    assert {"interview_book", "interview_reschedule", "interview_cancel"} <= set(specs)
    assert specs["interview_cancel"]["parameters"].get("properties", {}) == {}
    assert set(specs["interview_reschedule"]["parameters"]["properties"]) == {"slot_id"}
