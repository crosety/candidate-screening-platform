from typing import Any
from uuid import UUID

from asgiref.sync import sync_to_async

from web.jobs.enqueue import enqueue


class DjangoJobQueue:
    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        tenant_id: UUID | None,
        idempotency_key: str | None = None,
        max_attempts: int = 5,
    ) -> None:
        await sync_to_async(enqueue)(
            kind, payload, tenant_id, idempotency_key, max_attempts=max_attempts
        )
