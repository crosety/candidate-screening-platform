import asyncio
from uuid import UUID

from screening.application.ports import (
    Clock,
    ConnectorRegistry,
    ConnectorStatusRecord,
    ConnectorStatusRepository,
    ConversationRepository,
    ProfileVersions,
)


class LiveService:
    def __init__(
        self,
        versions: ProfileVersions,
        statuses: ConnectorStatusRepository,
        registry: ConnectorRegistry,
        tenants: ConversationRepository,
        clock: Clock,
    ) -> None:
        self.versions, self.statuses, self.registry = versions, statuses, registry
        self.tenants, self.clock = tenants, clock

    async def sync(self, tenant_id: UUID) -> None:
        version = await self.versions.active(tenant_id)
        profile = version.content if version else (await self.versions.draft(tenant_id)).content
        tenant = await self.tenants.get_tenant(tenant_id)
        for connection in profile.connections:
            try:
                data = await asyncio.wait_for(
                    self.registry.get(tenant.slug, connection).call(
                        "status", {"schedule_enabled": profile.autonomy.schedule >= 1}
                    ),
                    timeout=10,
                )
            except Exception:
                data = {
                    "summary": "Sin conexión",
                    "warnings": [
                        {
                            "code": "unreachable",
                            "text": f"No se puede conectar con «{connection.name}».",
                        }
                    ],
                }
            await self.statuses.upsert(
                tenant_id, connection.id, data["summary"], data["warnings"], self.clock.now()
            )

    async def list_statuses(self, tenant_id: UUID) -> list[ConnectorStatusRecord]:
        return await self.statuses.list_statuses(tenant_id)
