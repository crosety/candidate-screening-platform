"""Explicit, audited local redaction. No external providers or backups are altered."""

import hashlib
import json
from uuid import UUID

from django.db import connection
from django.db.models import F, Q
from django.utils import timezone

from screening.domain.errors import Conflict
from web.conversations.models import (
    Application,
    CandidateLink,
    ChannelIdentity,
    ChannelInbound,
    Event,
    Followup,
    InterviewBooking,
    Message,
    ModelMessage,
    Session,
    SmsNotification,
    TelephoneAttempt,
    TwilioOutbound,
)
from web.decisions.models import Decision, OutboxItem, Result
from web.jobs.models import Job
from web.profiles.models import ImprovementProposal
from web.simulated.models import AtsRecord, CalendarSlot
from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent
from web.tracing.models import CapabilityCall, LlmCall, VoiceMeasurement


def redact_candidate(
    tenant_id: UUID, application_id: UUID, case_ref: str, apply: bool = False
) -> dict[str, int]:
    with tenant_context(tenant_id):
        app = Application.objects.select_for_update().get(pk=application_id, tenant_id=tenant_id)
        rows = list(Session.objects.filter(application=app, tenant_id=tenant_id).order_by("id"))
        ids = [row.id for row in rows]
        counts = {
            "sessions": len(ids),
            "messages": Message.objects.filter(tenant_id=tenant_id, session_id__in=ids).count(),
            "model_messages": ModelMessage.objects.filter(
                tenant_id=tenant_id, session_id__in=ids
            ).count(),
            "results": Result.objects.filter(tenant_id=tenant_id, session_id__in=ids).count(),
        }
        if not apply:
            return counts
        repair = app.erased_at is not None
        interview_rows = InterviewBooking.objects.filter(tenant_id=tenant_id, application=app)
        booking_ids = [str(pk) for pk in interview_rows.values_list("id", flat=True)]
        reminder_job_ids = list(interview_rows.values_list("reminder_job_id", flat=True))
        reminder_jobs = Job.objects.filter(tenant_id=tenant_id).filter(
            Q(pk__in=reminder_job_ids) | Q(payload__booking_id__in=booking_ids)
        )
        if repair and not (
            Message.objects.filter(tenant_id=tenant_id, session_id__in=ids)
            .exclude(signal={}, language="")
            .exists()
            or Session.objects.filter(tenant_id=tenant_id, pk__in=ids).exclude(language="").exists()
            or Event.objects.filter(tenant_id=tenant_id, session_id__in=ids)
            .exclude(payload={})
            .exists()
            or interview_rows.exclude(
                status="cancelled",
                reminder_at=None,
                reminder_job_id=None,
                reminder_status="cancelled",
                site_name="",
                site_address="",
                candidate_timezone="",
            ).exists()
            or reminder_jobs.exclude(status="cancelled", payload={}, last_error="").exists()
            or VoiceMeasurement.objects.filter(session_id__in=ids).exists()
        ):
            return counts
        # Operators must pause workers for this tenant. Refuse ambiguous in-flight effects.
        if Job.objects.filter(tenant_id=tenant_id, status="running").exists():
            raise Conflict(
                "Pausa el worker y espera a que terminen los trabajos antes del borrado."
            )
        if SmsNotification.objects.filter(
            session_id__in=ids, state__in=["sending", "unknown"]
        ).exists():
            raise Conflict("Concilia el aviso SMS en curso antes de retirar sus datos.")
        if (
            TelephoneAttempt.objects.filter(session_id__in=ids)
            .exclude(state__in=["queued", "completed", "busy", "failed", "no-answer", "canceled"])
            .exists()
        ):
            raise Conflict("Finaliza o concilia la llamada telefónica antes de retirar sus datos.")
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            for pk in ids:
                key = int.from_bytes(
                    hashlib.sha256(f"{tenant_id}:{pk}".encode()).digest()[:8], "big", signed=True
                )
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", [key])
            cursor.execute("SELECT set_config('app.redact_application', %s, true)", [str(app.id)])
        contacts = list(
            ChannelIdentity.objects.filter(tenant_id=tenant_id, application=app).values_list(
                "contact", flat=True
            )
        )
        needles = [str(app.id), app.external_id, *[str(pk) for pk in ids], *contacts]
        twilio_rows = TwilioOutbound.objects.filter(tenant_id=tenant_id).filter(
            Q(message__session_id__in=ids) | Q(identity__application=app, message__isnull=True)
        )
        needles += [str(pk) for pk in twilio_rows.values_list("id", flat=True)]
        telephone_rows = TelephoneAttempt.objects.filter(session_id__in=ids)
        sms_rows = SmsNotification.objects.filter(session_id__in=ids)
        reminder_jobs.update(status="cancelled", payload={}, last_error="")
        needles += booking_ids
        needles += [str(pk) for pk in sms_rows.values_list("id", flat=True)]
        needles += [str(pk) for pk in telephone_rows.values_list("id", flat=True)]
        needles += [
            str(pk)
            for pk in LlmCall.objects.filter(session_id__in=ids).values_list("id", flat=True)
        ]
        # Derived free-text copies can reference the subject without a foreign key.
        # Remove the whole derived text, never invent a sanitised historical quote.
        if not repair:
            # The replacement name "Retirado" is not an identity for a later repair.
            needles += [s for s in (app.candidate_name, app.phone) if len(s) >= 4]

        def contains(value: object) -> bool:
            text = json.dumps(value, ensure_ascii=False)
            return any(needle in text for needle in needles if needle)

        for job in Job.objects.filter(tenant_id=tenant_id).iterator():
            if contains(job.payload):
                Job.objects.filter(pk=job.id).update(status="cancelled", payload={}, last_error="")
        for inbound in ChannelInbound.objects.filter(tenant_id=tenant_id).iterator():
            if contains(inbound.payload):
                Job.objects.filter(
                    tenant_id=tenant_id, kind="twilio_inbound", payload__inbound_id=str(inbound.id)
                ).update(status="cancelled", payload={}, last_error="")
                inbound.payload = {}
                inbound.save(update_fields=["payload"])
        for event in (
            AuditEvent.objects.filter(tenant_id=tenant_id)
            .exclude(
                action__in=[
                    "privacy.redaction_requested",
                    "privacy.redacted",
                    "privacy.redaction_repaired",
                ]
            )
            .iterator()
        ):
            if contains(event.payload):
                event.payload = {"redacted": True}
                event.save(update_fields=["payload"])
        for proposal in ImprovementProposal.objects.filter(tenant_id=tenant_id).iterator():
            if contains([proposal.evidence, proposal.rationale, proposal.content, proposal.diff]):
                proposal.evidence, proposal.rationale, proposal.content = (
                    [],
                    "Contenido retirado",
                    {},
                )
                proposal.state = "rejected"
                proposal.diff = {}
                proposal.save(update_fields=["evidence", "rationale", "content", "diff", "state"])
        if not repair:
            AuditEvent.objects.create(
                tenant_id=tenant_id,
                action="privacy.redaction_requested",
                payload={"application_id": str(app.id), "case_ref": case_ref},
            )
        Message.objects.filter(tenant_id=tenant_id, session_id__in=ids).update(
            text="[contenido retirado]", provider_message_id="", signal={}, language=""
        )
        ModelMessage.objects.filter(tenant_id=tenant_id, session_id__in=ids).update(content={})
        Event.objects.filter(tenant_id=tenant_id, session_id__in=ids).update(payload={})
        CapabilityCall.objects.filter(tenant_id=tenant_id, session_id__in=ids).update(
            request={}, response={}
        )
        VoiceMeasurement.objects.filter(session_id__in=ids).delete()
        Result.objects.filter(tenant_id=tenant_id, session_id__in=ids).update(
            fields={}, summary="", flags=[], sentiment=[]
        )
        Decision.objects.filter(tenant_id=tenant_id, session_id__in=ids).update(reason="")
        Decision.objects.filter(tenant_id=tenant_id, session_id__in=ids, status="proposed").update(
            status="cancelled"
        )
        OutboxItem.objects.filter(tenant_id=tenant_id, session_id__in=ids).update(
            payload={}, status="dead", last_error="privacy_redacted"
        )
        Followup.objects.filter(tenant_id=tenant_id, session_id__in=ids).update(
            contact="", reason="", status="cancelled"
        )
        twilio_rows.update(
            text="[contenido retirado]", provider_id="", state="blocked", reason="privacy_redacted"
        )
        telephone_rows.update(
            contact="",
            contact_digest="",
            link_digest="",
            provider_id="",
            stream_id="",
            state="canceled",
            reason="privacy_redacted",
        )
        sms_rows.update(
            contact="",
            provider_id="",
            revoked_at=timezone.now(),
            state="canceled",
            reason="privacy_redacted",
        )
        for identity in ChannelIdentity.objects.filter(tenant_id=tenant_id, application=app):
            identity.contact, identity.opted_out = f"erased:{identity.id}", True
            identity.onboarding = {}
            identity.save(update_fields=["contact", "opted_out", "onboarding"])
        CandidateLink.objects.filter(tenant_id=tenant_id, application_id=app.id).update(
            revoked_at=timezone.now()
        )
        Session.objects.filter(tenant_id=tenant_id, pk__in=ids).update(
            status="abandoned",
            ended_at=F("ended_at") if repair else timezone.now(),
            system_prompt="",
            run_context={},
            language="",
        )
        AtsRecord.objects.filter(tenant_id=tenant_id, external_id=app.external_id).update(
            candidate_name="Retirado",
            phone="",
            city="",
            fields={},
            notes=[],
            stage="Datos retirados",
        )
        CalendarSlot.objects.filter(
            tenant_id=tenant_id, booked_application_external_id=app.external_id
        ).update(booked_application_external_id="")
        interview_rows.filter(status__in=["booked", "rescheduled"]).update(
            status="cancelled",
            sequence=F("sequence") + 1,
            cancelled_by="privacy",
            cancelled_at=timezone.now(),
        )
        interview_rows.update(
            reminder_at=None,
            reminder_job_id=None,
            reminder_status="cancelled",
            site_name="",
            site_address="",
            candidate_timezone="",
            updated_at=timezone.now(),
        )
        app.candidate_name, app.phone, app.city, app.segment = "Retirado", "", "", ""
        app.erased_at = app.erased_at or timezone.now()
        app.save(update_fields=["candidate_name", "phone", "city", "segment", "erased_at"])
        AuditEvent.objects.create(
            tenant_id=tenant_id,
            action="privacy.redaction_repaired" if repair else "privacy.redacted",
            payload={"application_id": str(app.id), "case_ref": case_ref, "counts": counts},
        )
        return counts
