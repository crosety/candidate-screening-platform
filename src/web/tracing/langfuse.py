"""Durable delivery of the local trace; exporting never runs on the interview path."""

import re
from datetime import timedelta
from typing import Any
from uuid import UUID

from django.conf import settings

from adapters.llm.langfuse import MetricsExportError, metric_span
from screening.platform.container import build_metrics_delivery, get_container
from web.conversations.models import Session
from web.jobs.enqueue import enqueue
from web.jobs.models import Job
from web.tenancy.context import tenant_context
from web.tracing.models import LlmCall, VoiceMeasurement


def suppress_withdrawn(job: Job) -> None:
    job.payload["delivery"] = "suppressed_withdrawn"
    job.save(update_fields=["payload", "updated_at"])


def deliver_metrics(job: Job, spans: list[dict[str, Any]]) -> None:
    if job.payload.get("delivery") == "delivered":
        return
    delivery = build_metrics_delivery()
    received = delivery.received(spans)
    if received == {span["spanId"] for span in spans}:
        job.payload["delivery"] = "delivered"
        job.save(update_fields=["payload", "updated_at"])
        return
    if received or job.payload.get("delivery") == "uncertain":
        raise MetricsExportError("Entrega incierta: comprobar recibo antes de reenviar")
    # Persist before dispatch: a crash or timeout must never cause an automatic second POST.
    job.payload["delivery"] = "uncertain"
    job.save(update_fields=["payload", "updated_at"])
    delivery.export(spans)
    job.payload["delivery"] = "delivered"
    job.save(update_fields=["payload", "updated_at"])


def export_llm_job(job: Job) -> None:
    if not settings.LANGFUSE_ENABLED:
        raise MetricsExportError("Exportación de métricas desactivada; trabajo conservado")
    if not job.tenant_id or job.payload.get("project_id") != settings.LANGFUSE_PROJECT_ID:
        raise MetricsExportError("Ámbito de exportación no válido")
    with tenant_context(job.tenant_id):
        row = LlmCall.objects.select_related("session__application").get(
            id=UUID(job.payload["call_id"])
        )
        if row.session is not None and row.session.application.erased_at:
            suppress_withdrawn(job)
            return
    unmetered_voice = row.agent == "voice" and row.effort == "live"
    span = metric_span(
        identity=f"llm:{row.tenant_id}:{row.id}",
        session=f"{row.tenant_id}:{row.session_id or row.eval_case_id or row.id}",
        agent=row.agent,
        model=row.model_id,
        effort=row.effort,
        ended_at=row.created_at,
        latency_ms=row.latency_ms,
        usage=None
        if unmetered_voice
        else {
            "input": row.input_tokens,
            "output": row.output_tokens,
            "input_cache_read": row.cache_read_tokens,
            "input_cache_write": row.cache_write_tokens,
        },
        cost_micros=row.cost_usd_micros,
        environment=settings.LANGFUSE_ENVIRONMENT,
        stop_reason=row.stop_reason,
        metadata={
            "timing": "whole_call" if unmetered_voice else "completion_minus_latency",
            "cost_basis": "local_meter",
            "tokens_status": "not_reported" if unmetered_voice else "reported",
        },
    )
    deliver_metrics(job, [span])


def enqueue_voice_metrics(tenant_id: UUID, session_id: UUID, conversation_id: str) -> None:
    if not settings.LANGFUSE_ENABLED or not conversation_id:
        return
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", conversation_id):
        raise MetricsExportError("Identificador de métricas de voz no válido")
    with tenant_context(tenant_id):
        session = Session.objects.select_related("application").get(id=session_id)
        if session.application.erased_at:
            return
    enqueue(
        "eleven_metrics",
        {
            "session_id": str(session_id),
            "conversation_id": conversation_id,
            "project_id": settings.LANGFUSE_PROJECT_ID,
        },
        tenant_id,
        f"voice-metrics:{settings.LANGFUSE_PROJECT_ID}:{tenant_id}:{conversation_id}",
        run_at=get_container().clock.now() + timedelta(seconds=30),
        max_attempts=10,
    )


def export_voice_job(job: Job) -> None:
    if (
        not settings.LANGFUSE_ENABLED
        or not job.tenant_id
        or job.payload.get("project_id") != settings.LANGFUSE_PROJECT_ID
    ):
        raise MetricsExportError("Exportación de voz no habilitada para este ámbito")
    with tenant_context(job.tenant_id):
        session = Session.objects.select_related("application").get(
            id=UUID(job.payload["session_id"])
        )
        if session.application.erased_at:
            suppress_withdrawn(job)
            return
        measurement = VoiceMeasurement.objects.filter(
            conversation_id=job.payload["conversation_id"],
            project_id=settings.LANGFUSE_PROJECT_ID,
        ).first()
    delivery = build_metrics_delivery()
    if measurement:
        if measurement.session_id != session.id:
            raise MetricsExportError("La medición no corresponde a esta sesión")
        deliver_metrics(job, measurement.spans)
        return
    identity = job.payload["conversation_id"]
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", identity):
        raise MetricsExportError("Identificador de métricas de voz no válido")
    spans = delivery.measure_voice(identity, f"{job.tenant_id}:{session.id}")
    with tenant_context(job.tenant_id):
        measurement, _ = VoiceMeasurement.objects.get_or_create(
            conversation_id=identity,
            project_id=settings.LANGFUSE_PROJECT_ID,
            defaults={"tenant_id": job.tenant_id, "session_id": session.id, "spans": spans},
        )
        if measurement.session_id != session.id:
            raise MetricsExportError("La medición no corresponde a esta sesión")
    deliver_metrics(job, measurement.spans)
