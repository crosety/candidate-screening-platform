from uuid import UUID

from screening.application.ports import DraftRecord, VersionRecord
from screening.application.profiles import ProfileService


class DirectProfileVersions:
    def __init__(self, service: ProfileService) -> None:
        self.service = service

    async def draft(self, tenant_id: UUID) -> DraftRecord:
        return self.service.get_draft(tenant_id)

    async def active(self, tenant_id: UUID) -> VersionRecord | None:
        return self.service.active_version(tenant_id)

    async def for_new_session(
        self, tenant_id: UUID, session_id: UUID, segment: str = ""
    ) -> VersionRecord:
        return self.service.version_for_new_session(tenant_id, session_id)

    async def get(self, tenant_id: UUID, version_id: UUID) -> VersionRecord:
        return self.service.get_version(tenant_id, version_id)
