from copy import deepcopy
from datetime import datetime
from uuid import UUID

from screening.application.ports import ConnectorStatusRecord


class InMemoryConnectorStatusRepository:
    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, str], ConnectorStatusRecord] = {}

    async def upsert(
        self,
        tenant_id: UUID,
        connection_id: str,
        summary: str,
        warnings: list[dict[str, str]],
        checked_at: datetime,
    ) -> None:
        self.rows[(tenant_id, connection_id)] = ConnectorStatusRecord(
            tenant_id, connection_id, summary, deepcopy(warnings), checked_at
        )

    async def list_statuses(self, tenant_id: UUID) -> list[ConnectorStatusRecord]:
        return deepcopy([row for (tenant, _), row in self.rows.items() if tenant == tenant_id])

    async def last_checked(self, tenant_id: UUID) -> datetime | None:
        return max((row.checked_at for row in await self.list_statuses(tenant_id)), default=None)
