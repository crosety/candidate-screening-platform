from uuid import uuid4

import pytest
from asgiref.sync import async_to_sync
from django.test import override_settings

from adapters.persistence.tracing import DjangoTraceSink
from screening.application.ports import CapabilityCallRecord, LlmUsage
from web.accounts.models import User
from web.evals.models import EvalCase, EvalRun
from web.jobs.models import Job
from web.tenancy.context import tenant_context
from web.tenancy.models import Tenant
from web.tracing.models import CapabilityCall, LlmCall

pytestmark = pytest.mark.django_db(transaction=True)


@override_settings(LANGFUSE_ENABLED=True, LANGFUSE_PROJECT_ID="test-project")
def test_llm_metrics_are_queued_without_network_or_candidate_content() -> None:
    tenant = Tenant.objects.create(slug="metrics", name="Private company")
    usage = LlmUsage(
        tenant.id,
        None,
        None,
        "recruiter",
        "gpt-6.1-terra",
        "medium",
        100,
        20,
        30,
        0,
        270,
        42,
        "stop",
    )
    async_to_sync(DjangoTraceSink().record_llm_call)(usage)
    with tenant_context(tenant.id):
        row = LlmCall.objects.get()
    job = Job.objects.get(kind="langfuse_export")
    assert job.tenant_id == tenant.id
    assert job.payload == {"call_id": str(row.id), "project_id": "test-project"}
    assert job.status == "queued"


@override_settings(LANGFUSE_ENABLED=False)
def test_disabled_export_keeps_local_metrics_only() -> None:
    tenant = Tenant.objects.create(slug="local_metrics", name="Local metrics")
    usage = LlmUsage(tenant.id, None, None, "guardian", "test", "low", 1, 2, 0, 0, 0, 2, "stop")
    async_to_sync(DjangoTraceSink().record_llm_call)(usage)
    assert not Job.objects.filter(kind="langfuse_export").exists()
    with tenant_context(tenant.id):
        assert LlmCall.objects.count() == 1


@override_settings(LANGFUSE_ENABLED=True, LANGFUSE_PROJECT_ID="p" * 250)
def test_queue_database_failure_does_not_rollback_the_local_measurement() -> None:
    tenant = Tenant.objects.create(slug="queue_failure", name="Queue failure")
    usage = LlmUsage(tenant.id, None, None, "guardian", "test", "low", 1, 2, 0, 0, 0, 2, "stop")
    # The generated idempotency key exceeds Postgres' varchar limit: real DB error.
    async_to_sync(DjangoTraceSink().record_llm_call)(usage)
    assert not Job.objects.filter(kind="langfuse_export").exists()
    with tenant_context(tenant.id):
        assert LlmCall.objects.get().input_tokens == 1
        assert Tenant.objects.get(pk=tenant.id).slug == "queue_failure"


def test_eval_trace_does_not_reference_an_in_memory_session() -> None:
    tenant = Tenant.objects.create(slug="eval_trace", name="Eval trace")
    user = User.objects.create_user(email="trace@test.local", password="test")
    with tenant_context(tenant.id):
        run = EvalRun.objects.create(
            tenant=tenant, draft_rev=1, max_cost_usd_micros=100000, created_by=user
        )
        case = EvalCase.objects.create(tenant=tenant, run=run, persona_key="test", target="draft")
    sink = DjangoTraceSink()
    usage = LlmUsage(tenant.id, uuid4(), case.id, "juez", "test", "medium", 10, 20, 0, 0, 2, 10, "")
    async_to_sync(sink.record_llm_call)(usage)
    call = CapabilityCallRecord(
        tenant.id, uuid4(), "openings.find", "data", "ok", 1, {}, {}, case.id
    )
    async_to_sync(sink.record_capability_call)(call)
    with tenant_context(tenant.id):
        assert LlmCall.objects.get(eval_case=case).session_id is None
        assert CapabilityCall.objects.get(eval_case=case).session_id is None


def test_trace_sink_writes_with_tenant_isolation() -> None:
    tenant = Tenant.objects.create(slug="tracing", name="Tracing")
    sink = DjangoTraceSink()
    usage = LlmUsage(
        tenant.id, None, None, "guardian", "test", "low", 100, 20, 0, 0, 10, 42, "end_turn"
    )
    call = CapabilityCallRecord(
        tenant.id, None, "openings.find", "data", "ok", 2, {"near": "Demo"}, {"status": "ok"}
    )
    async_to_sync(sink.record_llm_call)(usage)
    async_to_sync(sink.record_capability_call)(call)
    with tenant_context(tenant.id):
        assert LlmCall.objects.get().cost_usd_micros == 10
        assert CapabilityCall.objects.get().request == {"near": "Demo"}
    with tenant_context(uuid4()):
        assert LlmCall.objects.count() == 0
        assert CapabilityCall.objects.count() == 0
