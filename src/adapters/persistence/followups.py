from datetime import datetime, timedelta
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db.models import F

from adapters.persistence.channels import record as identity_record
from screening.application.channels import ChannelIdentityRecord
from screening.application.followups import FollowupRecord
from screening.domain.errors import NotAllowed, NotFound
from screening.domain.run_context import RunContext
from web.conversations.models import ChannelIdentity, Followup, Session
from web.jobs.enqueue import enqueue
from web.jobs.models import Job
from web.tenancy.context import tenant_context


def record(row: Followup) -> FollowupRecord:
    return FollowupRecord(
        row.id,
        row.tenant_id,
        row.session_id,
        row.contact,
        row.run_at,
        row.reason,
        row.status,
        row.simulated,
        row.last_inbound_at,
        row.channel,
    )


def _enqueue(row: Followup) -> None:
    job = enqueue(
        "followup_dispatch",
        {"followup_id": str(row.id)},
        row.tenant_id,
        f"followup:{row.id}:{row.run_at.isoformat()}",
        row.run_at,
        max_attempts=1,
    )
    row.job_id = job.id
    row.save(update_fields=["job_id"])


def cancel_pending(tenant_id: UUID, session_id: UUID) -> None:
    rows = Followup.objects.filter(
        tenant_id=tenant_id, session_id=session_id, status__in=["proposed", "scheduled"]
    )
    jobs = list(rows.exclude(job_id=None).values_list("job_id", flat=True))
    rows.update(status="cancelled")
    Job.objects.filter(tenant_id=tenant_id, id__in=jobs, status="queued").update(status="cancelled")


class DjangoFollowupStore:
    @sync_to_async
    def create(
        self,
        ctx: RunContext,
        identity: ChannelIdentityRecord,
        run_at: datetime,
        reason: str,
        key: str,
        status: str,
        author: str,
    ) -> FollowupRecord:
        if ctx.session_id is None:
            raise NotAllowed("El recordatorio necesita una sesión.")
        with tenant_context(ctx.tenant_id):
            session = Session.objects.select_for_update().get(
                tenant_id=ctx.tenant_id, pk=ctx.session_id
            )
            existing = Followup.objects.filter(tenant_id=ctx.tenant_id, idempotency_key=key).first()
            if existing:
                return record(existing)
            if session.status != "active":
                raise NotAllowed("La sesión está cerrada.")
            attempts = Followup.objects.filter(tenant_id=ctx.tenant_id, session=session)
            if attempts.filter(status__in=["proposed", "scheduled"]).exists():
                raise NotAllowed("Solo puede haber un recordatorio pendiente por candidatura.")
            sent = attempts.filter(status="sent")
            if sent.count() >= 3:
                raise NotAllowed("Máximo tres recordatorios por candidatura.")
            if sent.filter(run_at__gt=run_at - timedelta(hours=1)).exists():
                raise NotAllowed("Debe transcurrir al menos una hora entre recordatorios.")
            row = Followup.objects.create(
                tenant_id=ctx.tenant_id,
                session=session,
                contact=identity.contact,
                run_at=run_at,
                reason=reason,
                status=status,
                simulated=identity.simulated,
                last_inbound_at=identity.last_inbound_at,
                idempotency_key=key,
                author=author,
            )
            if status == "scheduled":
                _enqueue(row)
            return record(row)

    @sync_to_async
    def get(self, tenant_id: UUID, followup_id: UUID) -> FollowupRecord:
        with tenant_context(tenant_id):
            row = Followup.objects.filter(tenant_id=tenant_id, pk=followup_id).first()
            if row is None:
                raise NotFound("Recordatorio no encontrado")
            return record(row)

    @sync_to_async
    def contact(self, ctx: RunContext) -> ChannelIdentityRecord | None:
        if ctx.application_id is None:
            return None
        with tenant_context(ctx.tenant_id):
            row = (
                ChannelIdentity.objects.filter(
                    tenant_id=ctx.tenant_id, application_id=ctx.application_id, channel="whatsapp"
                )
                .order_by(F("last_inbound_at").desc(nulls_last=True), "-created_at")
                .first()
            )
            return identity_record(row) if row else None

    @sync_to_async
    def finish(self, tenant_id: UUID, followup_id: UUID, status: str) -> None:
        with tenant_context(tenant_id):
            row = Followup.objects.select_for_update().get(tenant_id=tenant_id, pk=followup_id)
            if row.status not in ("proposed", "scheduled"):
                return
            row.status = status
            row.save(update_fields=["status"])
            if (
                status == "sent"
                and Followup.objects.filter(
                    tenant_id=tenant_id, session_id=row.session_id, status="sent"
                ).count()
                >= 3
            ):
                enqueue(
                    "followup_expire",
                    {"followup_id": str(row.id)},
                    tenant_id,
                    f"followup-expire:{row.id}",
                    row.run_at + timedelta(days=1),
                )
            if row.job_id:
                Job.objects.filter(pk=row.job_id, tenant_id=tenant_id, status="queued").update(
                    status="cancelled"
                )

    @sync_to_async
    def approve(self, tenant_id: UUID, followup_id: UUID) -> FollowupRecord:
        with tenant_context(tenant_id):
            row = (
                Followup.objects.select_for_update()
                .filter(tenant_id=tenant_id, pk=followup_id)
                .first()
            )
            if row is None:
                raise NotFound("Recordatorio no encontrado")
            if row.status == "proposed":
                row.status = "scheduled"
                row.save(update_fields=["status"])
                _enqueue(row)
            return record(row)

    @sync_to_async
    def postpone(self, tenant_id: UUID, followup_id: UUID, until: datetime) -> None:
        with tenant_context(tenant_id):
            row = Followup.objects.select_for_update().get(tenant_id=tenant_id, pk=followup_id)
            if row.status == "scheduled":
                row.run_at = until
                row.save(update_fields=["run_at"])
                _enqueue(row)

    @sync_to_async
    def due(self, tenant_id: UUID, contact: str, now: datetime) -> list[FollowupRecord]:
        with tenant_context(tenant_id):
            return [
                record(row)
                for row in Followup.objects.filter(
                    tenant_id=tenant_id,
                    contact=contact,
                    simulated=True,
                    status="scheduled",
                    run_at__lte=now,
                ).order_by("run_at")
            ]
