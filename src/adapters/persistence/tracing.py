import logging
from dataclasses import asdict

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction

from adapters.llm.budget import current_usage
from screening.application.ports import CapabilityCallRecord, LlmUsage
from web.jobs.enqueue import enqueue
from web.tenancy.context import tenant_context
from web.tracing.models import CapabilityCall, LlmCall

logger = logging.getLogger(__name__)


def enqueue_llm_export(row: LlmCall) -> None:
    if not settings.LANGFUSE_ENABLED:
        return
    if row.session is not None and row.session.application.erased_at:
        return
    payload = {"call_id": str(row.id), "project_id": settings.LANGFUSE_PROJECT_ID}
    if row.session_id:
        payload["session_id"] = str(row.session_id)
    enqueue(
        "langfuse_export",
        payload,
        row.tenant_id,
        f"langfuse:{settings.LANGFUSE_PROJECT_ID}:{row.id}",
        max_attempts=10,
    )


class DjangoTraceSink:
    @sync_to_async
    def record_llm_call(self, usage: LlmUsage) -> None:
        counter = current_usage.get()
        if counter is not None:
            counter.micros += usage.cost_usd_micros
        data = asdict(usage)
        if usage.eval_case_id is not None:
            data["session_id"] = None
        with tenant_context(usage.tenant_id):
            row = LlmCall.objects.create(**data)
            try:
                # A savepoint isolates optional telemetry from the canonical local trace.
                with transaction.atomic():
                    enqueue_llm_export(row)
            except Exception:
                logger.error(
                    "No se pudo encolar la métrica LLM %s; reconciliar exportación", row.id
                )

    @sync_to_async
    def record_capability_call(self, call: CapabilityCallRecord) -> None:
        data = asdict(call)
        if call.eval_case_id is not None:
            data["session_id"] = None
        with tenant_context(call.tenant_id):
            CapabilityCall.objects.create(**data)
