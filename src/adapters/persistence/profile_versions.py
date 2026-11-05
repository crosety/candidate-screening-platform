from uuid import UUID

from asgiref.sync import sync_to_async

from screening.application.ports import DraftRecord, VersionRecord
from screening.application.profiles import ProfileService
from web.profiles.models import SegmentPilot
from web.tenancy.context import tenant_context


@sync_to_async
def pilot_version(tenant_id: UUID, session_id: UUID, segment: str) -> UUID | None:
    with tenant_context(tenant_id):
        row = SegmentPilot.objects.filter(
            tenant_id=tenant_id, segment=segment, enabled=True
        ).first()
        if row is None:
            return None
        return (
            row.version_id if int(session_id.hex[:8], 16) % 100 < row.percent else row.baseline_id
        )


class AsyncProfileVersions:
    def __init__(self, service: ProfileService) -> None:
        self.service = service

    async def draft(self, tenant_id: UUID) -> DraftRecord:
        return await sync_to_async(self.service.get_draft)(tenant_id)

    async def active(self, tenant_id: UUID) -> VersionRecord | None:
        return await sync_to_async(self.service.active_version)(tenant_id)

    async def for_new_session(
        self, tenant_id: UUID, session_id: UUID, segment: str = ""
    ) -> VersionRecord:
        chosen = await pilot_version(tenant_id, session_id, segment)
        if chosen is not None:
            return await self.get(tenant_id, chosen)
        return await sync_to_async(self.service.version_for_new_session)(tenant_id, session_id)

    async def get(self, tenant_id: UUID, version_id: UUID) -> VersionRecord:
        return await sync_to_async(self.service.get_version)(tenant_id, version_id)
