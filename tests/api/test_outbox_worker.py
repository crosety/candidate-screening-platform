from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.test import Client

from screening.platform.container import get_container
from tests.api.test_conversations_api import conversation_client as conversation_client
from tests.api.test_conversations_api import open_session
from web.decisions.models import OutboxItem
from web.decisions.reconcile import reconcile_outbox
from web.jobs.enqueue import enqueue
from web.jobs.models import Job
from web.simulated.models import AtsRecord
from web.tenancy.context import tenant_context
from web.tenancy.models import AuditEvent
from worker.loop import run_once

pytestmark = pytest.mark.django_db(transaction=True)


def test_worker_writes_ats_and_marks_sent(conversation_client: tuple[Client, UUID]) -> None:
    client, tenant = conversation_client
    session = UUID(open_session(client))
    with tenant_context(tenant):
        AtsRecord.objects.create(
            tenant_id=tenant,
            external_id="test-lucia",
            candidate_name="Lucía",
            city="València",
            job_ref="repartidores",
            stage="Nuevo",
            fields={},
            notes=[],
        )
    item = async_to_sync(get_container().decisions.create_outbox)(
        tenant,
        session,
        "ats.write_result",
        {
            "application_external_id": "test-lucia",
            "verdict": "qualified",
            "fields": {"custom.licencia": "A2"},
            "summary": "Cumple",
            "stage": "Apto",
        },
        f"test:{session}",
    )
    enqueue("outbox_dispatch", {"outbox_item_id": str(item.id)}, tenant)
    assert run_once("test-worker")
    assert Job.objects.get().status == "done"
    assert async_to_sync(get_container().decisions.get_outbox)(tenant, item.id).status == "sent"
    with tenant_context(tenant):
        record = AtsRecord.objects.get(external_id="test-lucia")
        assert record.fields["custom.licencia"] == "A2"
        assert record.stage == "Apto" and record.notes[-1]["text"] == "Cumple"


@pytest.mark.parametrize("terminal", ["dead", "done", "cancelled"])
def test_reconcile_recovers_orphan_once_and_stops_ambiguous_jobs(
    conversation_client: tuple[Client, UUID], terminal: str
) -> None:
    client, tenant = conversation_client
    session = UUID(open_session(client))
    with tenant_context(tenant):
        item = OutboxItem.objects.create(
            tenant_id=tenant,
            session_id=session,
            kind="ats.set_stage",
            payload={"stage": "Revisión"},
            idempotency_key=f"review:{session}",
        )
    reconcile_outbox()
    reconcile_outbox()
    assert Job.objects.filter(kind="outbox_dispatch").count() == 1
    job = Job.objects.get(kind="outbox_dispatch")
    assert job.tenant_id == tenant and job.payload == {"outbox_item_id": str(item.id)}
    job.status = terminal
    job.save(update_fields=["status"])
    reconcile_outbox()
    reconcile_outbox()
    with tenant_context(tenant):
        item.refresh_from_db()
        assert item.status == "dead"
        assert AuditEvent.objects.filter(action="outbox.manual_review").count() == 1
    # Replaying a stale queued reference must not send a dead item.
    async_to_sync(get_container().outbox.dispatch)(tenant, item.id)
    assert Job.objects.filter(kind="outbox_dispatch").count() == 1
