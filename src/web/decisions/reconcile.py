"""Recover missing queue entries without replaying ambiguous external writes."""

from django.db.models import Q

from web.decisions.models import OutboxItem
from web.jobs.enqueue import enqueue
from web.jobs.models import Job
from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent, Tenant


def reconcile_outbox() -> None:
    for tenant_id in Tenant.objects.values_list("id", flat=True):
        with tenant_context(tenant_id):
            rows = OutboxItem.objects.select_for_update(skip_locked=True).filter(
                tenant_id=tenant_id, status="pending"
            )
            for item in rows.iterator():
                jobs = Job.objects.filter(
                    Q(idempotency_key=f"outbox:{item.id}")
                    | Q(payload__outbox_item_id=str(item.id)),
                    tenant_id=tenant_id,
                    kind="outbox_dispatch",
                )
                if not jobs.exists():
                    enqueue(
                        "outbox_dispatch",
                        {"outbox_item_id": str(item.id)},
                        tenant_id,
                        f"outbox:{item.id}",
                    )
                elif not jobs.filter(status__in=["queued", "running"]).exists():
                    # A dead/interrupted job may already have written remotely.
                    # Do not silently reset attempts or duplicate that side effect.
                    item.status = "dead"
                    item.last_error = "Cola terminada sin confirmación; requiere revisión manual."
                    item.save(update_fields=["status", "last_error"])
                    AuditEvent.objects.create(
                        tenant_id=tenant_id,
                        action="outbox.manual_review",
                        payload={
                            "outbox_item_id": str(item.id),
                            "session_id": str(item.session_id),
                        },
                    )
