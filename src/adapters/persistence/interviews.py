from datetime import datetime
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db import IntegrityError, transaction

from screening.application.interviews import BookingRecord
from screening.domain.errors import Conflict, NotFound
from screening.domain.interviews import SlotBooking
from web.conversations.models import InterviewBooking
from web.jobs.enqueue import enqueue
from web.jobs.models import Job
from web.tenancy.context import tenant_context

REMINDER_JOB = "interview_reminder"


def record(row: InterviewBooking) -> BookingRecord:
    return BookingRecord(
        row.id,
        row.tenant_id,
        row.session_id,
        row.application_id,
        row.connection_id,
        SlotBooking(
            row.slot_ref,
            row.site_ref,
            row.site_name,
            row.site_address,
            row.site_timezone,
            row.starts_at,
            row.duration_minutes,
        ),
        row.candidate_timezone,
        row.status,
        row.sequence,
        row.cancelled_by,
        row.cancelled_at,
        row.reminder_at,
        row.reminder_status,
        row.created_at,
        row.updated_at,
    )


def _apply_slot(row: InterviewBooking, slot: SlotBooking) -> None:
    row.slot_ref, row.site_ref = slot.slot_ref, slot.site_ref
    row.site_name, row.site_address = slot.site_name, slot.site_address
    row.site_timezone, row.starts_at = slot.site_timezone, slot.starts_at
    row.duration_minutes = slot.duration_minutes


def _cancel_reminder(row: InterviewBooking) -> None:
    if row.reminder_job_id:
        Job.objects.filter(pk=row.reminder_job_id, status="queued").update(status="cancelled")
    row.reminder_job_id = None


def _schedule_reminder(row: InterviewBooking) -> None:
    """One job per booking version; a later version cancels the previous job first."""
    _cancel_reminder(row)
    if row.reminder_at is None:
        row.reminder_status = "none"
        return
    job = enqueue(
        REMINDER_JOB,
        {"booking_id": str(row.id), "sequence": row.sequence},
        row.tenant_id,
        f"{REMINDER_JOB}:{row.id}:{row.sequence}:{row.reminder_at.isoformat()}",
        row.reminder_at,
        max_attempts=3,
    )
    row.reminder_job_id, row.reminder_status = job.id, "scheduled"


class DjangoInterviewStore:
    @sync_to_async
    def active_for(self, tenant_id: UUID, application_id: UUID) -> BookingRecord | None:
        with tenant_context(tenant_id):
            row = InterviewBooking.objects.filter(
                tenant_id=tenant_id,
                application_id=application_id,
                status__in=["booked", "rescheduled"],
            ).first()
            return record(row) if row else None

    @sync_to_async
    def latest_for(self, tenant_id: UUID, application_id: UUID) -> BookingRecord | None:
        with tenant_context(tenant_id):
            row = (
                InterviewBooking.objects.filter(tenant_id=tenant_id, application_id=application_id)
                .order_by("-updated_at")
                .first()
            )
            return record(row) if row else None

    @sync_to_async
    def get(self, tenant_id: UUID, booking_id: UUID) -> BookingRecord:
        with tenant_context(tenant_id):
            row = InterviewBooking.objects.filter(tenant_id=tenant_id, pk=booking_id).first()
            if row is None:
                raise NotFound("Entrevista no encontrada")
            return record(row)

    @sync_to_async
    def create(
        self,
        tenant_id: UUID,
        session_id: UUID,
        application_id: UUID,
        connection_id: str,
        slot: SlotBooking,
        candidate_timezone: str,
        reminder_at: datetime | None,
    ) -> BookingRecord:
        def existing() -> BookingRecord:
            row = (
                InterviewBooking.objects.select_for_update()
                .filter(
                    tenant_id=tenant_id,
                    application_id=application_id,
                    status__in=["booked", "rescheduled"],
                )
                .first()
            )
            if row is None:
                raise Conflict("La entrevista ha cambiado; vuelve a consultarla.")
            if row.slot_ref != slot.slot_ref:
                raise Conflict("La candidatura ya tiene otra entrevista.")
            return record(row)

        with tenant_context(tenant_id):
            if InterviewBooking.objects.filter(
                tenant_id=tenant_id,
                application_id=application_id,
                status__in=["booked", "rescheduled"],
            ).exists():
                return existing()
            row = InterviewBooking(
                tenant_id=tenant_id,
                session_id=session_id,
                application_id=application_id,
                connection_id=connection_id,
                candidate_timezone=candidate_timezone,
                reminder_at=reminder_at,
            )
            _apply_slot(row, slot)
            try:
                with transaction.atomic():
                    row.save()
            except IntegrityError:
                # A concurrent replay of the same booking won the unique active constraint.
                return existing()
            _schedule_reminder(row)
            row.save(update_fields=["reminder_job_id", "reminder_status", "updated_at"])
            return record(row)

    @sync_to_async
    def move(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        expected_sequence: int,
        slot: SlotBooking,
        reminder_at: datetime | None,
    ) -> BookingRecord:
        with tenant_context(tenant_id):
            row = InterviewBooking.objects.select_for_update().get(
                tenant_id=tenant_id, pk=booking_id
            )
            if row.sequence != expected_sequence or row.status == "cancelled":
                return record(row)
            _apply_slot(row, slot)
            row.status, row.sequence, row.reminder_at = "rescheduled", row.sequence + 1, reminder_at
            _schedule_reminder(row)
            row.save()
            return record(row)

    @sync_to_async
    def cancel(
        self, tenant_id: UUID, booking_id: UUID, expected_sequence: int, by: str, at: datetime
    ) -> BookingRecord:
        with tenant_context(tenant_id):
            row = InterviewBooking.objects.select_for_update().get(
                tenant_id=tenant_id, pk=booking_id
            )
            if row.sequence != expected_sequence or row.status == "cancelled":
                return record(row)
            _cancel_reminder(row)
            row.status, row.sequence = "cancelled", row.sequence + 1
            row.cancelled_by, row.cancelled_at = by, at
            row.reminder_at, row.reminder_status = None, "cancelled"
            row.save()
            return record(row)

    @sync_to_async
    def finish_reminder(
        self,
        tenant_id: UUID,
        booking_id: UUID,
        sequence: int,
        status: str,
        retry_at: datetime | None = None,
    ) -> None:
        with tenant_context(tenant_id):
            row = (
                InterviewBooking.objects.select_for_update()
                .filter(tenant_id=tenant_id, pk=booking_id)
                .first()
            )
            if row is None or row.sequence != sequence:
                return
            if retry_at is not None:
                row.reminder_at = retry_at
                _schedule_reminder(row)
            else:
                row.reminder_status = status
            row.save(
                update_fields=[
                    "reminder_at",
                    "reminder_status",
                    "reminder_job_id",
                    "updated_at",
                ]
            )
