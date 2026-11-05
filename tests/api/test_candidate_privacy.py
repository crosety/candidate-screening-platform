from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.db import DatabaseError
from django.test import Client

from screening.domain.errors import Conflict, NotFound
from screening.domain.signals import TurnSignal
from screening.platform.container import get_container
from tests.api.test_interview_agenda import booked_candidate
from tests.api.test_phone_api import phone
from web.conversations.models import (
    Application,
    CandidateLink,
    Event,
    InterviewBooking,
    Message,
    ModelMessage,
    Session,
)
from web.conversations.privacy import redact_candidate
from web.jobs.models import Job
from web.simulated.models import CalendarSlot
from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent

__all__ = ["phone"]
pytestmark = pytest.mark.django_db(transaction=True)


def test_redaction_dry_run_isolated_and_audited(phone: tuple[Client, UUID, UUID]):
    _, tenant, application = phone
    c = get_container()
    token = async_to_sync(c.conversations.repo.get_or_create_link)(tenant, application)
    opened = async_to_sync(c.conversations.open_session)(token)
    with tenant_context(tenant):
        ModelMessage.objects.create(
            tenant_id=tenant,
            session_id=opened.session_id,
            seq=1,
            role="user",
            content={"text": "Dato privado"},
        )
        Message.objects.create(
            tenant_id=tenant, session_id=opened.session_id, direction="in", text="Dato privado"
        )
        Event.objects.create(
            tenant_id=tenant,
            session_id=opened.session_id,
            type="test",
            payload={"text": "Dato privado"},
        )
    counts = redact_candidate(tenant, application, "REQ-1")
    assert counts["messages"] == 1
    with tenant_context(tenant):
        assert Message.objects.get(session_id=opened.session_id).text == "Dato privado"
    with pytest.raises(DatabaseError), tenant_context(tenant):
        ModelMessage.objects.filter(session_id=opened.session_id).update(content={})
    redact_candidate(tenant, application, "REQ-1", True)
    with tenant_context(tenant):
        assert Application.objects.get(pk=application).erased_at is not None
        assert Session.objects.get(pk=opened.session_id).status == "abandoned"
        assert ModelMessage.objects.get(session_id=opened.session_id).content == {}
        assert Message.objects.get(session_id=opened.session_id).text == "[contenido retirado]"
        assert Event.objects.filter(session_id=opened.session_id).exclude(payload={}).count() == 0
        assert AuditEvent.objects.filter(action="privacy.redacted").count() == 1
        assert CandidateLink.objects.get(application_id=application).revoked_at
        assert (
            Application.objects.exclude(pk=application).exclude(candidate_name="Retirado").exists()
        )
    with pytest.raises(NotFound):
        async_to_sync(c.conversations.repo.resolve_link)(token)
    with pytest.raises(NotFound):
        async_to_sync(c.conversations.repo.get_session)(tenant, opened.session_id)
    with pytest.raises(DatabaseError), tenant_context(tenant):
        ModelMessage.objects.filter(session_id=opened.session_id).update(
            content={"text": "Reintroducido"}
        )


def test_redaction_refuses_in_flight_jobs(phone: tuple[Client, UUID, UUID]):
    _, tenant, application = phone
    Job.objects.create(tenant_id=tenant, kind="maintenance", status="running")
    with pytest.raises(Conflict, match="Pausa"):
        redact_candidate(tenant, application, "REQ-2", True)
    with tenant_context(tenant):
        assert Application.objects.get(pk=application).erased_at is None


def test_redaction_removes_observation_quotes_from_storage_and_candidate_detail(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, application = phone
    service = get_container().conversations
    token = async_to_sync(service.repo.get_or_create_link)(tenant, application)
    opened = async_to_sync(service.open_session)(token)
    message = async_to_sync(service.repo.add_message)(
        tenant, opened.session_id, "in", "No entiendo esta pregunta"
    )
    async_to_sync(service.repo.record_signal)(
        tenant,
        opened.session_id,
        message.id,
        TurnSignal(language="es", mood="confused", quote="No entiendo esta pregunta"),
    )
    async_to_sync(service.repo.add_event)(
        tenant,
        opened.session_id,
        "assessment_completed",
        {
            "unconfirmed_fields": {
                "answer": {
                    "value": "Dato pendiente",
                    "evidence": "No entiendo esta pregunta",
                    "confidence": "high",
                }
            }
        },
    )
    detail_url = f"/api/v1/tenants/{tenant}/candidates/{opened.session_id}"
    before = client.get(detail_url)
    assert before.status_code == 200
    assert before.json()["messages"][0]["signal"]["quote"] == "No entiendo esta pregunta"

    redact_candidate(tenant, application, "REQ-SIGNALS", True)

    with tenant_context(tenant):
        stored = Message.objects.get(pk=message.id)
        assert stored.signal == {} and stored.language == ""
        assert Session.objects.get(pk=opened.session_id).language == ""
        assert (
            Event.objects.get(session_id=opened.session_id, type="assessment_completed").payload
            == {}
        )
    after = client.get(detail_url)
    assert after.status_code == 200
    assert after.json()["messages"][0]["signal"] is None
    assert after.json()["messages"][0]["language"] == ""
    assert "No entiendo esta pregunta" not in after.content.decode()


@pytest.mark.parametrize("reschedule", [False, True])
def test_redaction_cancels_local_interview_and_all_its_reminders(
    phone: tuple[Client, UUID, UUID],
    reschedule: bool,
) -> None:
    client, tenant, _ = phone
    sid, _ = booked_candidate(tenant, "privacy-interview")
    other_sid, _ = booked_candidate(tenant, "unrelated-interview")
    with tenant_context(tenant):
        booking = InterviewBooking.objects.get(session_id=sid)
        other = InterviewBooking.objects.get(session_id=other_sid)
    original_job_ids = [booking.reminder_job_id]
    if reschedule:
        url = f"/api/v1/tenants/{tenant}/interviews/{booking.id}"
        slots = client.get(url + "/slots").json()
        moved = client.post(
            url,
            {"action": "reschedule", "slot_id": slots[-1]["slot_id"]},
            content_type="application/json",
        )
        assert moved.status_code == 200 and moved.json()["status"] == "rescheduled"
        with tenant_context(tenant):
            booking.refresh_from_db()
        original_job_ids.append(booking.reminder_job_id)
    original_sequence = booking.sequence
    original_job_id = booking.reminder_job_id
    assert original_job_id is not None
    assert Job.objects.get(pk=original_job_id).status == "queued"

    redact_candidate(tenant, booking.application_id, "REQ-INTERVIEW", True)

    with tenant_context(tenant):
        booking.refresh_from_db()
        assert booking.status == "cancelled"
        assert booking.sequence == original_sequence + 1
        assert booking.cancelled_by == "privacy" and booking.cancelled_at is not None
        assert booking.reminder_at is None and booking.reminder_job_id is None
        assert booking.reminder_status == "cancelled"
        assert booking.site_name == booking.site_address == booking.candidate_timezone == ""
        assert CalendarSlot.objects.get(pk=booking.slot_ref).booked_application_external_id == ""
        other.refresh_from_db()
        assert other.status == "booked" and other.reminder_status == "scheduled"
        assert (
            CalendarSlot.objects.get(pk=other.slot_ref).booked_application_external_id
            == "unrelated-interview"
        )
    for job_id in original_job_ids:
        reminder = Job.objects.get(pk=job_id)
        assert reminder.status == "cancelled"
        assert reminder.payload == {} and reminder.last_error == ""
    assert Job.objects.get(pk=other.reminder_job_id).status == "queued"
    assert (
        async_to_sync(get_container().interviews.remind)(tenant, booking.id, original_sequence)
        == "superseded"
    )
    redact_candidate(tenant, booking.application_id, "REQ-INTERVIEW", True)
    with tenant_context(tenant):
        booking.refresh_from_db()
        assert booking.sequence == original_sequence + 1


def test_redacted_interview_is_absent_from_agenda_and_calendar_exports(
    phone: tuple[Client, UUID, UUID],
) -> None:
    client, tenant, _ = phone
    sid, _ = booked_candidate(tenant, "private-agenda")
    with tenant_context(tenant):
        booking = InterviewBooking.objects.get(session_id=sid)
    base = f"/api/v1/tenants/{tenant}"
    before = client.get(base + "/interviews")
    assert before.status_code == 200
    assert any(row["id"] == str(booking.id) for row in before.json())

    redact_candidate(tenant, booking.application_id, "REQ-AGENDA", True)

    client.raise_request_exception = False
    agenda = client.get(base + "/interviews?include_past=true")
    assert agenda.status_code == 200
    assert not any(row["id"] == str(booking.id) for row in agenda.json())
    assert client.get(base + f"/sessions/{sid}/interview").json() == {"interview": None}
    assert client.get(base + f"/interviews/{booking.id}/ics").status_code == 404
    assert client.get(base + f"/interviews/{booking.id}/slots").status_code == 404
    assert (
        client.post(
            base + f"/interviews/{booking.id}",
            {"action": "cancel"},
            content_type="application/json",
        ).status_code
        == 404
    )


def test_reapplying_redaction_repairs_legacy_residue_without_rewriting_the_original_audit(
    phone: tuple[Client, UUID, UUID],
) -> None:
    _, tenant, _ = phone
    sid, _ = booked_candidate(tenant, "legacy-redaction")
    service = get_container().conversations
    message = async_to_sync(service.repo.add_message)(tenant, sid, "in", "No entiendo la pregunta")
    with tenant_context(tenant):
        booking = InterviewBooking.objects.get(session_id=sid)
    reminder_id = booking.reminder_job_id
    assert reminder_id is not None
    redact_candidate(tenant, booking.application_id, "REQ-ORIGINAL", True)
    with tenant_context(tenant):
        erased_at = Application.objects.get(pk=booking.application_id).erased_at
        ended_at = Session.objects.get(pk=sid).ended_at
        original_audit = AuditEvent.objects.get(action="privacy.redacted").payload
        unrelated_audit = AuditEvent.objects.create(
            tenant_id=tenant,
            action="unrelated.audit",
            payload={"note": "Retirado de otra candidatura"},
        )
        # Reconstruct the residue left by the older redactor, not a new conversation.
        Message.objects.filter(pk=message.id).update(
            signal={"quote": "No entiendo la pregunta", "mood": "confused"}, language="es"
        )
        Session.objects.filter(pk=sid).update(language="es")
        InterviewBooking.objects.filter(pk=booking.id).update(
            status="booked",
            sequence=booking.sequence,
            cancelled_by="",
            cancelled_at=None,
            reminder_at=booking.reminder_at,
            reminder_status="scheduled",
            reminder_job_id=reminder_id,
            site_name=booking.site_name,
            candidate_timezone=booking.candidate_timezone,
        )
    Job.objects.filter(pk=reminder_id).update(
        status="queued", payload={"booking_id": str(booking.id), "sequence": booking.sequence}
    )

    redact_candidate(tenant, booking.application_id, "REQ-REPAIR", True)

    with tenant_context(tenant):
        assert Message.objects.get(pk=message.id).signal == {}
        booking.refresh_from_db()
        assert booking.status == "cancelled" and booking.reminder_job_id is None
        assert booking.site_name == booking.candidate_timezone == ""
        assert Application.objects.get(pk=booking.application_id).erased_at == erased_at
        assert Session.objects.get(pk=sid).ended_at == ended_at
        assert AuditEvent.objects.get(action="privacy.redacted").payload == original_audit
        unrelated_audit.refresh_from_db()
        assert unrelated_audit.payload == {"note": "Retirado de otra candidatura"}
        assert AuditEvent.objects.filter(action="privacy.redaction_requested").count() == 1
        assert AuditEvent.objects.filter(action="privacy.redaction_repaired").count() == 1
        repaired_at = booking.updated_at
    reminder = Job.objects.get(pk=reminder_id)
    assert reminder.status == "cancelled" and reminder.payload == {}

    redact_candidate(tenant, booking.application_id, "REQ-REPAIR", True)

    with tenant_context(tenant):
        assert AuditEvent.objects.filter(action="privacy.redaction_repaired").count() == 1
        assert InterviewBooking.objects.get(pk=booking.id).updated_at == repaired_at
