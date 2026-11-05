from uuid import uuid4

import pytest

from adapters.fakes.connectors import FailingConnectorRegistry, StaticConnectorRegistry
from adapters.fakes.live import InMemoryConnectorStatusRepository
from screening.application.live import LiveService
from tests.application.test_conversation_service import Bundle
from tests.application.test_conversation_service import bundle as bundle
from tests.application.test_conversation_service import content as content

pytestmark = pytest.mark.asyncio


async def test_live_saves_warnings_and_scopes_tenants(bundle: Bundle) -> None:
    statuses = InMemoryConnectorStatusRepository()
    service = LiveService(
        bundle.service.versions,
        statuses,
        StaticConnectorRegistry(
            {
                "status": {
                    "summary": "Disponible",
                    "warnings": [{"code": "demo", "text": "Sin agenda"}],
                }
            }
        ),
        bundle.repo,
        bundle.profiles.clock,
    )
    await service.sync(bundle.tenant)
    rows = await service.list_statuses(bundle.tenant)
    assert len(rows) == 3 and rows[0].warnings[0]["text"] == "Sin agenda"
    assert await statuses.last_checked(bundle.tenant) == bundle.profiles.clock.now()
    assert not await service.list_statuses(uuid4())


async def test_live_failure_is_explicit(bundle: Bundle) -> None:
    statuses = InMemoryConnectorStatusRepository()
    service = LiveService(
        bundle.service.versions,
        statuses,
        FailingConnectorRegistry(RuntimeError("down")),
        bundle.repo,
        bundle.profiles.clock,
    )
    await service.sync(bundle.tenant)
    assert all(
        row.summary == "Sin conexión" and row.warnings[0]["code"] == "unreachable"
        for row in await service.list_statuses(bundle.tenant)
    )
