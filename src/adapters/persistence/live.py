from datetime import datetime
from uuid import UUID

from asgiref.sync import sync_to_async
from django.db.models import Max

from screening.application.ports import ConnectorStatusRecord
from web.profiles.models import ConnectorStatus
from web.tenancy.context import tenant_context


class DjangoConnectorStatusRepository:
    @sync_to_async
    def upsert(
        self,
        tenant_id: UUID,
        connection_id: str,
        summary: str,
        warnings: list[dict[str, str]],
        checked_at: datetime,
    ) -> None:
        with tenant_context(tenant_id):
            ConnectorStatus.objects.update_or_create(
                tenant_id=tenant_id,
                connection_id=connection_id,
                defaults={"summary": summary, "warnings": warnings, "checked_at": checked_at},
            )

    @sync_to_async
    def list_statuses(self, tenant_id: UUID) -> list[ConnectorStatusRecord]:
        with tenant_context(tenant_id):
            return [
                ConnectorStatusRecord(
                    row.tenant_id, row.connection_id, row.summary, row.warnings, row.checked_at
                )
                for row in ConnectorStatus.objects.filter(tenant_id=tenant_id).order_by(
                    "connection_id"
                )
            ]

    @sync_to_async
    def last_checked(self, tenant_id: UUID) -> datetime | None:
        with tenant_context(tenant_id):
            return ConnectorStatus.objects.filter(tenant_id=tenant_id).aggregate(
                latest=Max("checked_at")
            )["latest"]


def django_live_warnings(tenant_id: UUID) -> list[str]:
    with tenant_context(tenant_id):
        return [
            warning["text"]
            for row in ConnectorStatus.objects.filter(tenant_id=tenant_id)
            for warning in row.warnings
        ]
