from datetime import datetime
from typing import Any
from uuid import UUID

from django.utils import timezone

from web.jobs.models import Job


def enqueue(
    kind: str,
    payload: dict[str, Any] | None = None,
    tenant_id: UUID | None = None,
    idempotency_key: str | None = None,
    run_at: datetime | None = None,
    max_attempts: int = 5,
) -> Job:
    defaults = {
        "kind": kind,
        "payload": payload if payload is not None else {},
        "tenant_id": tenant_id,
        "run_at": run_at or timezone.now(),
        "max_attempts": max_attempts,
    }
    if idempotency_key is not None:
        job, _ = Job.objects.get_or_create(idempotency_key=idempotency_key, defaults=defaults)
        return job
    return Job.objects.create(**defaults)
